"""In-memory workspace for the single-performance "Solo Cinema" editor.

The comparison studio (:mod:`pianosync.web.workspace`) aligns several videos.
This workspace keeps exactly one performance, analyses it with
:mod:`pianosync.core.drama` and renders a dramatic edit with
:mod:`pianosync.video.solo`.

Like the comparison workspace it only holds data and pure helpers, so it can be
unit-tested without starting a server. Long operations still run in background
threads through :class:`pianosync.web.jobs.JobManager`.
"""

from __future__ import annotations

import os
import threading
from dataclasses import replace
from typing import Dict, List, Optional

from ..config import AlignmentConfig, OutputConfig, get_api_key, normalize_size
from ..core.analysis import AnalysisEngine, PerformanceAnalysis
from ..core.drama import DramaPlan, ImpactMoment, analyze_solo
from ..video.solo import (
    SOLO_STYLE_KEYS,
    SOLO_STYLES,
    SoloConfig,
    SoloRenderResult,
    render_solo,
)

MAX_MOMENTS = 8
DEFAULT_FILENAME = "solo-cinema.mp4"


def _clamp(value: float, lo: float, hi: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = lo
    return max(lo, min(hi, number))


class SoloWorkspace:
    def __init__(self, cache_dir: str, output_dir: str):
        self.cache_dir = cache_dir
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        self.engine = AnalysisEngine(cache_dir)
        self.lock = threading.RLock()

        self.performance: Optional[PerformanceAnalysis] = None
        self.plan: Optional[DramaPlan] = None
        #: moment id -> enabled. ``None`` inside means "everything enabled".
        self.selected: Dict[int, bool] = {}
        self.config = SoloConfig()
        self.output = OutputConfig(path=os.path.join(output_dir, DEFAULT_FILENAME))
        self.last_render: Optional[SoloRenderResult] = None
        #: Last validated Gemini direction (``None`` until AI is used).
        self.direction: Optional[dict] = None
        #: moment id -> punch multiplier suggested by the AI director.
        self.emphasis: Dict[int, float] = {}

    # -- lifecycle ------------------------------------------------------
    def reset(self) -> None:
        with self.lock:
            self.performance = None
            self.plan = None
            self.selected = {}
            self.config = SoloConfig()
            self.output = OutputConfig(path=os.path.join(self.output_dir, DEFAULT_FILENAME))
            self.last_render = None
            self.direction = None
            self.emphasis = {}

    @property
    def ready(self) -> bool:
        return self.performance is not None and self.plan is not None

    # -- analysis -------------------------------------------------------
    def analyze(
        self,
        path: str,
        quality: str = "balanced",
        max_moments: int = MAX_MOMENTS,
        progress=None,
    ) -> dict:
        with self.lock:
            self.engine = AnalysisEngine(
                self.engine.cache_dir, AlignmentConfig(quality=quality)
            )
            perf = self.engine.analyze_performance(path, progress=progress)
            if progress is not None:
                progress.update(0.82, "연출 지점을 찾는 중…")
            plan = analyze_solo(
                perf.features,
                perf.wav_path,
                max_moments=max(1, min(int(max_moments or MAX_MOMENTS), 32)),
            )
            self.performance = perf
            self.plan = plan
            self.selected = {i: True for i in range(len(plan.moments))}
            self.last_render = None
            self.direction = None
            self.emphasis = {}
            if progress is not None:
                progress.update(1.0, "분석 완료")
        return self.status_payload()

    # -- AI direction ---------------------------------------------------
    def direct(
        self,
        note: str = "",
        progress=None,
        client=None,
    ) -> dict:
        """Ask the optional Gemini director and apply its validated choices."""
        if not self.ready:
            raise ValueError("분석을 먼저 실행하세요.")
        from ..ai.solo_director import request_direction

        with self.lock:
            assert self.plan is not None
            if progress is not None:
                progress.update(0.15, "AI 연출 제안을 요청하는 중…")
            direction = request_direction(self.plan, note=note, client=client)
            if progress is not None:
                progress.update(0.85, "AI 연출을 적용하는 중…")

            cfg = self.config
            cfg.style = direction["style"]
            cfg.intensity = float(direction["intensity"])
            for key in ("camera", "punch", "flash", "glow", "letterbox"):
                setattr(cfg, key, bool(direction[key]))
            self.config = cfg.normalized()

            keep = set(int(i) for i in direction["keep_moments"])
            self.selected = {
                i: (i in keep) for i in range(len(self.plan.moments))
            }
            self.emphasis = {
                int(key): float(value)
                for key, value in (direction.get("emphasis") or {}).items()
            }
            self.direction = direction
            self.last_render = None
            if progress is not None:
                progress.update(1.0, "AI 연출 적용 완료")
        return self.status_payload()

    # -- moments --------------------------------------------------------
    def set_selection(self, settings: Optional[dict]) -> None:
        """Update the enabled impact moments from a render/status payload."""
        if not settings or "moments" not in settings:
            return
        raw = settings.get("moments")
        if raw is None:
            return
        if not isinstance(raw, (list, tuple)):
            raw = [raw]
        enabled = set()
        for value in raw:
            try:
                enabled.add(int(value))
            except (TypeError, ValueError):
                continue
        self.selected = {
            i: (i in enabled) for i in range(len(self.plan.moments) if self.plan else 0)
        }

    def selected_moments(self, ids: Optional[List[int]] = None) -> List[ImpactMoment]:
        if self.plan is None:
            return []
        if ids is None:
            return [
                m for i, m in enumerate(self.plan.moments) if self.selected.get(i, True)
            ]
        wanted = set()
        for value in ids:
            try:
                wanted.add(int(value))
            except (TypeError, ValueError):
                continue
        return [m for i, m in enumerate(self.plan.moments) if i in wanted]

    # -- rendering ------------------------------------------------------
    def render(self, options: Optional[dict] = None, progress=None) -> SoloRenderResult:
        if not self.ready:
            raise ValueError("분석을 먼저 실행하세요.")
        options = options or {}
        with self.lock:
            self._apply_solo_settings(options)
            self._apply_output_settings(options)
            self.set_selection(options)
            if "moments" in options and options.get("moments") is not None:
                wanted = set()
                for value in list(options.get("moments") or []):
                    try:
                        wanted.add(int(value))
                    except (TypeError, ValueError):
                        continue
            else:
                wanted = {i for i, on in self.selected.items() if on}
            moments = []
            assert self.plan is not None
            for index, moment in enumerate(self.plan.moments):
                if index not in wanted:
                    continue
                boost = float(self.emphasis.get(index, 1.0))
                moments.append(
                    replace(
                        moment,
                        strength=max(0.0, min(1.0, float(moment.strength) * boost)),
                    )
                )
            assert self.performance is not None and self.plan is not None
            result = render_solo(
                self.performance.media,
                self.performance.wav_path,
                self.plan,
                self.output,
                self.config,
                moments=moments,
                progress=progress,
            )
            self.last_render = result
        return result

    def _apply_solo_settings(self, s: dict) -> None:
        cfg = self.config
        if s.get("style") in SOLO_STYLE_KEYS:
            cfg.style = str(s["style"])
        if "intensity" in s:
            cfg.intensity = _clamp(s["intensity"], 0.0, 1.0)
        for key in (
            "camera",
            "punch",
            "flash",
            "glow",
            "vignette",
            "grain",
            "letterbox",
            "normalize_audio",
            "include_audio",
        ):
            if key in s:
                setattr(cfg, key, bool(s[key]))
        if s.get("intro_text") is not None:
            cfg.intro_text = str(s.get("intro_text") or "").strip()
        if s.get("intro_subtitle") is not None:
            cfg.intro_subtitle = str(s.get("intro_subtitle") or "").strip()
        if "intro_seconds" in s:
            cfg.intro_seconds = _clamp(s["intro_seconds"], 0.0, 8.0)
        cfg = cfg.normalized()
        self.config = cfg

    def _apply_output_settings(self, s: dict) -> None:
        cfg = self.output
        filename = s.get("filename")
        if filename:
            name = os.path.basename(str(filename)).strip() or DEFAULT_FILENAME
            if not name.lower().endswith(".mp4"):
                name += ".mp4"
            cfg.path = os.path.join(self.output_dir, name)
        elif not cfg.path:
            cfg.path = os.path.join(self.output_dir, DEFAULT_FILENAME)

        if s.get("size"):
            cfg.size = normalize_size(str(s["size"]))
            cfg.width = 0
            cfg.height = 0
        width = int(s.get("width") or 0)
        height = int(s.get("height") or 0)
        if width > 0 and height > 0:
            cfg.size = "custom"
            cfg.width = width
            cfg.height = height
        if "fps" in s:
            cfg.fps = _clamp(s["fps"], 1.0, 120.0)
        if "crf" in s:
            cfg.crf = int(_clamp(s["crf"], 0, 51))
        if s.get("preset"):
            cfg.preset = str(s["preset"])

    # -- payloads -------------------------------------------------------
    def status_payload(self) -> dict:
        base = {
            "ready": self.ready,
            "moment_count": len(self.plan.moments) if self.plan else 0,
            "selected": [i for i, on in sorted(self.selected.items()) if on],
            "render": self._render_dict(),
            "settings": self.settings_payload(),
            "direction": self.direction,
            "ai_configured": bool(get_api_key()),
        }
        if not self.ready:
            return base
        assert self.performance is not None and self.plan is not None
        media = self.performance.media
        base["source"] = {
            "name": os.path.basename(self.performance.path),
            "path": self.performance.path,
            "width": media.width,
            "height": media.height,
            "fps": round(media.fps, 2),
            "duration": round(self.plan.duration, 3),
            "tempo_bpm": round(self.plan.tempo_bpm, 1),
        }
        base["plan"] = self.plan.to_dict()
        return base

    def settings_payload(self) -> dict:
        cfg = self.config
        return {
            "style": cfg.style,
            "intensity": round(cfg.intensity, 3),
            "camera": cfg.camera,
            "punch": cfg.punch,
            "flash": cfg.flash,
            "glow": cfg.glow,
            "vignette": cfg.vignette,
            "grain": cfg.grain,
            "letterbox": cfg.letterbox,
            "intro_text": cfg.intro_text,
            "intro_subtitle": cfg.intro_subtitle,
            "intro_seconds": cfg.intro_seconds,
            "normalize_audio": cfg.normalize_audio,
            "include_audio": cfg.include_audio,
        }

    def waveform_payload(self) -> dict:
        if not self.ready:
            return {"ready": False}
        assert self.plan is not None
        plan = self.plan
        return {
            "ready": True,
            "duration": round(plan.duration, 3),
            "energy": {
                "t": [round(float(v), 3) for v in plan.energy_times],
                "v": [round(float(v), 4) for v in plan.energy],
            },
            "onsets": [round(float(t), 3) for t in plan.onsets],
            "phrase_times": [round(float(t), 3) for t in plan.phrase_times],
            "moments": [m.to_dict(i) for i, m in enumerate(plan.moments)],
            "selected": [i for i, on in sorted(self.selected.items()) if on],
            "direction": self.direction,
        }

    def _render_dict(self) -> Optional[dict]:
        if self.last_render is None:
            return None
        r = self.last_render
        return {
            "name": os.path.basename(r.path),
            "duration": round(r.duration, 2),
            "width": r.width,
            "height": r.height,
            "fps": round(r.fps, 2),
            "moments": r.moments,
            "style": r.style,
            "intro_seconds": round(r.intro_seconds, 2),
        }


def solo_style_choices() -> List[dict]:
    """Config payload for the style picker (order follows SOLO_STYLES)."""
    return [
        {
            "value": key,
            "label": SOLO_STYLES[key].label,
            "accent": "#%02x%02x%02x" % SOLO_STYLES[key].accent,
        }
        for key in SOLO_STYLE_KEYS
    ]
