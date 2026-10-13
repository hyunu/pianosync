"""In-memory workspace holding the current analysis state for the web UI.

The web edition keeps a single workspace (local, single-user), mirroring the
desktop app's single project. Long operations run in background threads via
:mod:`pianosync.web.jobs`; this module only holds data and pure helpers so it can
be unit-tested without a server.
"""

from __future__ import annotations

import os
import threading
from typing import Dict, List, Optional, Tuple

import numpy as np

from ..ai.gemini import GeminiClient
from ..audio.confidence import onset_peak_times
from ..audio.envelope import envelope_from_wav
from ..config import AlignmentConfig, OutputConfig, TransitionConfig
from ..core.analysis import AnalysisEngine
from ..core.transitions import TransitionPlan, generate_transitions
from ..errors import GeminiError
from ..video.render import RenderResult, render_final_video

MAX_WAVE_POINTS = 1600
MAX_MAP_POINTS = 1200


def _downsample(xs, ys, target: int):
    """Resample ``(xs, ys)`` onto ``target`` uniform x positions."""
    if xs is None or ys is None:
        return [], []
    xs = np.asarray(xs, dtype=np.float64)
    ys = np.asarray(ys, dtype=np.float64)
    if xs.size == 0:
        return [], []
    if xs.size <= target:
        return [round(float(v), 4) for v in xs], [round(float(v), 4) for v in ys]
    grid = np.linspace(float(xs[0]), float(xs[-1]), target)
    values = np.interp(grid, xs, ys)
    return [round(float(v), 4) for v in grid], [round(float(v), 4) for v in values]


def feature_summary(features) -> Dict[str, object]:
    return {
        "duration_seconds": round(features.duration, 3),
        "tempo_bpm": round(features.tempo_bpm, 1),
        "mean_chroma": [round(float(v), 4) for v in features.chroma.mean(axis=0)],
        "mean_onset": round(float(np.mean(features.onset)), 4),
        "onset_rate_hz": round(float(features.frame_rate), 2),
    }


class Workspace:
    def __init__(self, cache_dir: str, output_dir: str):
        self.engine = AnalysisEngine(cache_dir)
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        self.lock = threading.RLock()

        self.path_a: Optional[str] = None
        self.path_b: Optional[str] = None
        self.perf_a = None
        self.perf_b = None
        self.alignment = None
        self.plan: Optional[TransitionPlan] = None
        self.manual_points: List[Tuple[float, float]] = []
        self.transition = TransitionConfig()
        self.output = OutputConfig()
        self.ai_note: Optional[str] = None
        self.last_render: Optional[RenderResult] = None

    # -- lifecycle ------------------------------------------------------
    def reset(self) -> None:
        with self.lock:
            self.path_a = self.path_b = None
            self.perf_a = self.perf_b = None
            self.alignment = None
            self.plan = None
            self.manual_points = []
            self.ai_note = None
            self.last_render = None

    @property
    def has_alignment(self) -> bool:
        return self.alignment is not None

    # -- analysis -------------------------------------------------------
    def analyze(
        self,
        path_a: str,
        path_b: str,
        quality: str = "balanced",
        use_ai: bool = True,
        progress=None,
    ) -> dict:
        with self.lock:
            self.engine = AnalysisEngine(self.engine.cache_dir, AlignmentConfig(quality=quality))
            self.path_a, self.path_b = path_a, path_b
            perf_a = self.engine.analyze_performance(path_a, progress=progress)
            perf_b = self.engine.analyze_performance(path_b, progress=progress)
            alignment = self.engine.align(perf_a, perf_b, progress=progress)
            self.perf_a, self.perf_b, self.alignment = perf_a, perf_b, alignment
            self.ai_note = None
            self.manual_points = []
            self.plan = generate_transitions(alignment, self.transition)
            if use_ai:
                self.ai_note = self._ai_note(perf_a, perf_b)
        return self.status_payload()

    def _ai_note(self, perf_a, perf_b) -> Optional[str]:
        client = GeminiClient()
        if not client.configured:
            return None
        try:
            verdict = client.verify_same_piece(
                feature_summary(perf_a.features), feature_summary(perf_b.features)
            )
            if verdict.get("same") is not None and verdict["same"] < 0.5:
                return (
                    "Gemini 보조 판단: 두 녹음이 같은 곡일 가능성이 낮다고 응답했습니다 "
                    f"(same={verdict['same']:.2f}). 로컬 정렬 결과를 주의해서 확인하세요."
                )
            return f"Gemini 보조 판단: 같은 곡일 가능성이 높습니다 (same={verdict['same']:.2f})."
        except GeminiError as exc:
            return f"Gemini 보조 분석을 사용할 수 없음: {exc}"

    # -- transitions ----------------------------------------------------
    def regenerate(self, settings: Optional[dict] = None) -> dict:
        if self.alignment is None:
            raise ValueError("분석을 먼저 실행하세요.")
        with self.lock:
            if settings:
                self._apply_transition_settings(settings)
            self.plan = generate_transitions(
                self.alignment, self.transition, user_points=self.manual_points
            )
        return self.plan_payload()

    def _apply_transition_settings(self, s: dict) -> None:
        cfg = self.transition
        if "interval_seconds" in s:
            cfg.interval_seconds = float(s["interval_seconds"])
        if "mode" in s and s["mode"] in ("musical", "fixed_output"):
            cfg.mode = s["mode"]
        if "effect" in s and s["effect"] in ("cut", "crossfade", "fadeblack"):
            cfg.effect = s["effect"]
        if "effect_duration" in s:
            cfg.effect_duration = float(s["effect_duration"])
        if "audio_mode" in s and s["audio_mode"] in ("A", "B", "aligned", "none"):
            cfg.audio_mode = s["audio_mode"]
        if "snap_to_onset" in s:
            cfg.snap_to_onset = bool(s["snap_to_onset"])
        if "start_with" in s and s["start_with"] in ("A", "B"):
            cfg.start_with = s["start_with"]
        if "alternate" in s:
            cfg.alternate = bool(s["alternate"])

    def set_anchors(self, anchors: List[Tuple[float, float]]) -> dict:
        with self.lock:
            self.manual_points = [(float(a), float(b)) for a, b in anchors]
        return self.regenerate()

    def add_anchor(self, a_time: float, b_time: Optional[float] = None) -> dict:
        if self.alignment is None:
            raise ValueError("분석을 먼저 실행하세요.")
        if b_time is None:
            b_time = self.alignment.time_map.b_for_a(a_time)
        points = list(self.manual_points)
        points.append((float(a_time), float(b_time)))
        return self.set_anchors(points)

    def remove_anchor(self, index: int) -> dict:
        points = list(self.manual_points)
        if 0 <= index < len(points):
            points.pop(index)
        return self.set_anchors(points)

    # -- rendering ------------------------------------------------------
    def render(self, options: Optional[dict] = None, progress=None) -> RenderResult:
        if self.plan is None or self.alignment is None:
            raise ValueError("분석과 전환 생성이 먼저 필요합니다.")
        with self.lock:
            self._apply_output_settings(options or {})
            result = render_final_video(
                self.plan,
                self.alignment,
                self.perf_a.media,
                self.perf_b.media,
                self.perf_a.wav_path,
                self.perf_b.wav_path,
                self.output,
                self.transition,
                progress=progress,
            )
            self.last_render = result
        return result

    def _apply_output_settings(self, s: dict) -> None:
        cfg = self.output
        if "filename" in s and s["filename"]:
            name = os.path.basename(str(s["filename"])).strip() or "comparison.mp4"
            if not name.lower().endswith(".mp4"):
                name += ".mp4"
            cfg.path = os.path.join(self.output_dir, name)
        elif not cfg.path:
            cfg.path = os.path.join(self.output_dir, "comparison.mp4")
        if "width" in s:
            cfg.width = int(s["width"])
        if "height" in s:
            cfg.height = int(s["height"])
        if "fps" in s:
            cfg.fps = float(s["fps"])
        if "crf" in s:
            cfg.crf = int(s["crf"])
        if "preset" in s:
            cfg.preset = str(s["preset"])
        if "include_audio" in s:
            cfg.include_audio = bool(s["include_audio"])
        if "effect" in s and s["effect"] in ("cut", "crossfade", "fadeblack"):
            self.transition.effect = s["effect"]
        if "audio_mode" in s and s["audio_mode"] in ("A", "B", "aligned", "none"):
            self.transition.audio_mode = s["audio_mode"]

    # -- payloads -------------------------------------------------------
    def status_payload(self) -> dict:
        base = {
            "ready": self.has_alignment,
            "ai_note": self.ai_note,
            "anchor_count": len(self.manual_points),
            "render": self._render_dict(),
        }
        if not self.has_alignment:
            return base
        a = self.perf_a
        b = self.perf_b
        base.update(
            {
                "a": {
                    "name": os.path.basename(a.path),
                    "width": a.media.width,
                    "height": a.media.height,
                    "fps": round(a.media.fps, 2),
                    "duration": round(a.features.duration, 2),
                    "tempo_bpm": round(a.features.tempo_bpm, 1),
                },
                "b": {
                    "name": os.path.basename(b.path),
                    "width": b.media.width,
                    "height": b.media.height,
                    "fps": round(b.media.fps, 2),
                    "duration": round(b.features.duration, 2),
                    "tempo_bpm": round(b.features.tempo_bpm, 1),
                },
                "alignment": {
                    "mean_confidence": round(self.alignment.confidence.mean, 3),
                    "low_fraction": round(self.alignment.confidence.low_fraction, 4),
                    "low_segments": [
                        [round(a0, 2), round(b0, 2)]
                        for a0, b0 in self.alignment.low_confidence_segments_seconds()
                    ],
                },
                "plan": self.plan.to_dict() if self.plan else None,
            }
        )
        return base

    def plan_payload(self) -> dict:
        return {
            "plan": self.plan.to_dict() if self.plan else None,
            "anchor_count": len(self.manual_points),
            "ready": self.has_alignment,
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
            "segments": r.segments,
            "audio_mode": r.audio_mode,
            "audio_included": r.audio_included,
        }

    def waveform_payload(self) -> dict:
        if not self.has_alignment:
            return {"ready": False}
        a, b = self.perf_a, self.perf_b
        env_a_t, env_a_v = envelope_from_wav(a.wav_path)
        env_b_t, env_b_v = envelope_from_wav(b.wav_path)
        env_a_t, env_a_v = _downsample(env_a_t, env_a_v, MAX_WAVE_POINTS)
        env_b_t, env_b_v = _downsample(env_b_t, env_b_v, MAX_WAVE_POINTS)

        a_times = self.alignment.time_map.a_times
        b_curve = self.alignment.time_map.a_to_b_sec
        map_t, map_v = _downsample(a_times, b_curve, MAX_MAP_POINTS)

        conf_t, conf_v = _downsample(
            a_times, self.alignment.confidence.per_frame, MAX_WAVE_POINTS
        )

        onsets = onset_peak_times(self.perf_a.features)
        if onsets.size > 400:
            step = int(np.ceil(onsets.size / 400))
            onsets = onsets[::step]

        return {
            "ready": True,
            "a_duration": round(a.features.duration, 3),
            "b_duration": round(b.features.duration, 3),
            "env_a": {"t": env_a_t, "v": env_a_v},
            "env_b": {"t": env_b_t, "v": env_b_v},
            "map": {"a": map_t, "b": map_v},
            "confidence": {"t": conf_t, "v": conf_v},
            "low_segments": [
                [round(a0, 3), round(b0, 3)]
                for a0, b0 in self.alignment.low_confidence_segments_seconds()
            ],
            "onsets": [round(float(t), 3) for t in onsets.tolist()],
            "anchors": [[round(x, 3), round(y, 3)] for x, y in self.manual_points],
            "segments": [
                {
                    "index": s.index,
                    "source": s.source,
                    "a_start": round(s.a_start, 3),
                    "a_end": round(s.a_end, 3),
                    "b_start": round(s.b_start, 3),
                    "confidence": round(s.confidence, 3),
                    "reason": s.reason,
                }
                for s in (self.plan.segments if self.plan else [])
            ],
            "low_threshold": self.transition.low_confidence_threshold,
        }
