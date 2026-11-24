"""In-memory workspace for the score-play mode on the main page."""

from __future__ import annotations

import os
import tempfile
import threading
from dataclasses import dataclass, replace
from typing import Dict, List, Optional

from ..config import AlignmentConfig, OutputConfig, normalize_size
from ..core.analysis import AnalysisEngine
from ..core.drama import DramaPlan
from ..errors import PianoSyncError
from ..score.alignment import align_score
from ..score.layout import ScoreLayout, render_svg
from ..score.midi import parse_midi
from ..score.model import ScoreDocument, ScoreSync
from ..score.musicxml import parse_musicxml
from ..video.mapping import resolve_output_size
from ..video.score_overlay import panel_geometry, render_overlay_frames
from ..video.solo import SoloConfig, SoloRenderResult, render_solo

DEFAULT_FILENAME = "score-play.mp4"
_SCORE_EXTENSIONS = (".musicxml", ".mxl", ".xml", ".mid", ".midi")


def parse_score(path: str) -> ScoreDocument:
    """Dispatch an uploaded score file to the right parser."""
    lower = str(path).lower()
    if lower.endswith(".mid") or lower.endswith(".midi"):
        return parse_midi(path)
    if lower.endswith(_SCORE_EXTENSIONS):
        return parse_musicxml(path)
    raise PianoSyncError(
        "악보는 MusicXML(.musicxml/.mxl/.xml) 또는 MIDI(.mid/.midi) 파일만 지원합니다."
    )


@dataclass
class ScorePlayConfig:
    overlay_enabled: bool = True
    position: str = "bottom"
    fps: float = 12.0
    scale: float = 0.9
    margin: int = 12

    def normalized(self) -> "ScorePlayConfig":
        position = str(self.position or "bottom").strip().lower()
        if position not in ("bottom", "top", "left", "right"):
            position = "bottom"
        return replace(
            self,
            position=position,
            fps=max(1.0, min(30.0, float(self.fps or 12.0))),
            scale=max(0.4, min(1.0, float(self.scale or 0.9))),
            margin=max(0, min(80, int(self.margin or 0))),
        )


class ScoreWorkspace:
    def __init__(self, cache_dir: str, output_dir: str):
        self.cache_dir = cache_dir
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        self.engine = AnalysisEngine(cache_dir)
        self.lock = threading.RLock()

        self.performance = None
        self.document: Optional[ScoreDocument] = None
        self.sync: Optional[ScoreSync] = None
        self.config = ScorePlayConfig()
        self.output = OutputConfig(path=os.path.join(output_dir, DEFAULT_FILENAME))
        self.last_render: Optional[SoloRenderResult] = None

    # -- lifecycle ------------------------------------------------------
    def reset(self) -> None:
        with self.lock:
            self.performance = None
            self.document = None
            self.sync = None
            self.config = ScorePlayConfig()
            self.output = OutputConfig(path=os.path.join(self.output_dir, DEFAULT_FILENAME))
            self.last_render = None

    @property
    def ready(self) -> bool:
        return self.performance is not None and self.document is not None and self.sync is not None

    # -- analysis -------------------------------------------------------
    def analyze(
        self,
        video_path: str,
        score_path: str,
        quality: str = "balanced",
        progress=None,
    ) -> dict:
        with self.lock:
            self.engine = AnalysisEngine(
                self.engine.cache_dir, AlignmentConfig(quality=quality)
            )
            perf = self.engine.analyze_performance(video_path, progress=progress)
            if progress is not None:
                progress.update(0.72, "악보를 읽는 중…")
            doc = parse_score(score_path)
            sync = align_score(perf.features, doc, cfg=self.engine.cfg, progress=progress)
            self.performance = perf
            self.document = doc
            self.sync = sync
            self.last_render = None
        return self.status_payload()

    # -- rendering ------------------------------------------------------
    def render(self, options: Optional[dict] = None, progress=None) -> SoloRenderResult:
        if not self.ready:
            raise PianoSyncError("악보 정렬을 먼저 실행하세요.")
        options = options or {}
        with self.lock:
            assert self.performance is not None
            assert self.document is not None
            assert self.sync is not None
            self._apply_score_settings(options)
            self._apply_output_settings(options)
            media = self.performance.media
            width, height = resolve_output_size(self.output, media.width, media.height)
            panel_w, panel_h, _, _ = panel_geometry(
                width, height, self.config.position, self.config.margin, self.config.scale
            )
            layout = ScoreLayout.from_document(
                self.document, width=panel_w, height=panel_h, measures_per_system=3
            )
            solo_config = SoloConfig(
                style="clean",
                intensity=0.0,
                camera=False,
                punch=False,
                flash=False,
                glow=False,
                vignette=False,
                grain=False,
                letterbox=False,
                intro_text="",
                intro_subtitle="",
                normalize_audio=True,
                include_audio=bool(options.get("include_audio", True)),
            )
            plan = DramaPlan(
                duration=float(self.performance.features.duration),
                tempo_bpm=float(self.performance.features.tempo_bpm or 0.0),
            )
            with tempfile.TemporaryDirectory(prefix="pianosync_score_frames_") as frames_dir:
                score_overlay = None
                if self.config.overlay_enabled:
                    score_overlay = render_overlay_frames(
                        layout,
                        self.sync.events,
                        frames_dir,
                        fps=self.config.fps,
                        width=width,
                        height=height,
                        position=self.config.position,
                        margin=self.config.margin,
                        scale=self.config.scale,
                    )
                result = render_solo(
                    media,
                    self.performance.wav_path,
                    plan,
                    self.output,
                    solo_config,
                    moments=[],
                    progress=progress,
                    score_overlay=score_overlay,
                )
            self.last_render = result
        return result

    def _apply_score_settings(self, options: dict) -> None:
        cfg = self.config
        if "overlay_enabled" in options:
            cfg.overlay_enabled = bool(options["overlay_enabled"])
        if "position" in options:
            cfg.position = str(options["position"])
        if "fps" in options:
            cfg.fps = float(options["fps"])
        if "scale" in options:
            cfg.scale = float(options["scale"])
        if "margin" in options:
            cfg.margin = int(options["margin"])
        self.config = cfg.normalized()

    def _apply_output_settings(self, options: dict) -> None:
        cfg = self.output
        filename = options.get("filename")
        if filename:
            name = os.path.basename(str(filename)).strip() or DEFAULT_FILENAME
            if not name.lower().endswith(".mp4"):
                name += ".mp4"
            cfg.path = os.path.join(self.output_dir, name)
        elif not cfg.path:
            cfg.path = os.path.join(self.output_dir, DEFAULT_FILENAME)
        if options.get("size"):
            cfg.size = normalize_size(str(options["size"]))
            cfg.width = 0
            cfg.height = 0
        width = int(options.get("width") or 0)
        height = int(options.get("height") or 0)
        if width > 0 and height > 0:
            cfg.size = "custom"
            cfg.width = width
            cfg.height = height
        if "fps" in options:
            cfg.fps = max(1.0, min(120.0, float(options["fps"])))
        if "crf" in options:
            cfg.crf = max(0, min(51, int(options["crf"])))
        if options.get("preset"):
            cfg.preset = str(options["preset"])

    # -- payloads -------------------------------------------------------
    def svg(self) -> str:
        if not self.ready:
            raise PianoSyncError("악보 정렬을 먼저 실행하세요.")
        assert self.document is not None
        layout = ScoreLayout.from_document(self.document, width=1000, height=300)
        return render_svg(layout)

    def status_payload(self) -> dict:
        base: Dict[str, object] = {
            "ready": self.ready,
            "settings": self._settings_dict(),
            "render": self._render_dict(),
        }
        if not self.ready:
            return base
        assert self.performance is not None
        assert self.document is not None
        assert self.sync is not None
        media = self.performance.media
        base["source"] = {
            "name": os.path.basename(self.performance.path),
            "path": self.performance.path,
            "width": media.width,
            "height": media.height,
            "fps": round(media.fps, 2),
            "duration": round(float(self.performance.features.duration), 3),
        }
        base["score"] = self.document.to_dict()
        base["sync"] = self.sync.to_dict()
        base["notes"] = [event.to_dict() for event in self.sync.events]
        return base

    def _settings_dict(self) -> dict:
        cfg = self.config
        return {
            "overlay_enabled": cfg.overlay_enabled,
            "position": cfg.position,
            "fps": cfg.fps,
            "scale": cfg.scale,
            "margin": cfg.margin,
        }

    def _render_dict(self) -> Optional[dict]:
        result = self.last_render
        if result is None or not os.path.isfile(result.path):
            return None
        return {
            "name": os.path.basename(result.path),
            "duration": round(result.duration, 2),
            "width": result.width,
            "height": result.height,
            "fps": round(result.fps, 2),
        }
