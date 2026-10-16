"""In-memory workspace holding the current analysis state for the web UI.

The web edition keeps a single workspace (local, single-user), mirroring the
desktop app's single project. Long operations run in background threads via
:mod:`pianosync.web.jobs`; this module only holds data and pure helpers so it can
be unit-tested without a server.

Up to :data:`pianosync.core.analysis.MAX_TRACKS` performances can be combined.
Track 0 is the reference; every other track is aligned onto it, and the rendered
video rotates through the registered performances.
"""

from __future__ import annotations

import os
import threading
from typing import Dict, List, Optional, Tuple

import numpy as np

from ..ai.gemini import GeminiClient
from ..audio.confidence import onset_peak_times
from ..audio.envelope import envelope_from_wav
from ..config import EFFECTS, AlignmentConfig, OutputConfig, TransitionConfig
from ..core.analysis import MAX_TRACKS, AnalysisEngine, Track
from ..core.transitions import TransitionPlan, generate_transitions, generate_transitions_multi
from ..errors import GeminiError
from ..video.render import RenderResult, RenderTrack, render_final_video_multi

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

        self.tracks: List[Track] = []
        self.plan: Optional[TransitionPlan] = None
        self.manual_points: List[Tuple[float, float]] = []
        self.transition = TransitionConfig()
        self.output = OutputConfig()
        self.ai_note: Optional[str] = None
        self.last_render: Optional[RenderResult] = None

    # -- lifecycle ------------------------------------------------------
    def reset(self) -> None:
        with self.lock:
            self.tracks = []
            self.plan = None
            self.manual_points = []
            self.ai_note = None
            self.last_render = None

    @property
    def paths(self) -> List[str]:
        return [t.path for t in self.tracks]

    @property
    def reference(self) -> Optional[Track]:
        return self.tracks[0] if self.tracks else None

    @property
    def has_alignment(self) -> bool:
        return len(self.tracks) >= 2

    # -- analysis -------------------------------------------------------
    def analyze(
        self,
        path_a: str,
        path_b: str,
        quality: str = "balanced",
        use_ai: bool = True,
        progress=None,
    ) -> dict:
        return self.analyze_paths(
            [path_a, path_b], quality=quality, use_ai=use_ai, progress=progress
        )

    def analyze_paths(
        self,
        paths: List[str],
        quality: str = "balanced",
        use_ai: bool = True,
        progress=None,
    ) -> dict:
        with self.lock:
            self.engine = AnalysisEngine(self.engine.cache_dir, AlignmentConfig(quality=quality))
            self.tracks = self.engine.analyze_many(paths, progress=progress)
            self.ai_note = None
            self.manual_points = []
            self.plan = self._build_plan()
            if use_ai:
                self.ai_note = self._ai_note()
        return self.status_payload()

    def _build_plan(self) -> TransitionPlan:
        if len(self.tracks) == 2:
            return generate_transitions(
                self.tracks[1].alignment,
                self.transition,
                user_points=self.manual_points,
            )
        return generate_transitions_multi(
            self.tracks, self.transition, user_points=self.manual_points
        )

    def _ai_note(self) -> Optional[str]:
        client = GeminiClient()
        if not client.configured or len(self.tracks) < 2:
            return None
        try:
            verdict = client.verify_same_piece(
                feature_summary(self.tracks[0].features),
                feature_summary(self.tracks[1].features),
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
        if not self.has_alignment:
            raise ValueError("분석을 먼저 실행하세요.")
        with self.lock:
            if settings:
                self._apply_transition_settings(settings)
            self.plan = self._build_plan()
        return self.plan_payload()

    def _apply_transition_settings(self, s: dict) -> None:
        cfg = self.transition
        if "interval_seconds" in s:
            cfg.interval_seconds = float(s["interval_seconds"])
        if "mode" in s and s["mode"] in ("musical", "fixed_output"):
            cfg.mode = s["mode"]
        if "effect" in s and s["effect"] in EFFECTS:
            cfg.effect = s["effect"]
        if "effect_duration" in s:
            cfg.effect_duration = float(s["effect_duration"])
        if "audio_mode" in s and s["audio_mode"] in ("A", "B", "aligned", "none"):
            cfg.audio_mode = s["audio_mode"]
        if "snap_to_onset" in s:
            cfg.snap_to_onset = bool(s["snap_to_onset"])
        if "start_with" in s and isinstance(s["start_with"], str):
            cfg.start_with = s["start_with"].upper()
        if "alternate" in s:
            cfg.alternate = bool(s["alternate"])

    def set_anchors(self, anchors: List[Tuple[float, float]]) -> dict:
        with self.lock:
            self.manual_points = [(float(a), float(b)) for a, b in anchors]
        return self.regenerate()

    def add_anchor(self, a_time: float, b_time: Optional[float] = None) -> dict:
        if not self.has_alignment:
            raise ValueError("분석을 먼저 실행하세요.")
        if b_time is None:
            second = self.tracks[1]
            b_time = second.source_time(a_time)
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
        if self.plan is None or not self.has_alignment:
            raise ValueError("분석과 전환 생성이 먼저 필요합니다.")
        with self.lock:
            self._apply_output_settings(options or {})
            render_tracks = [
                RenderTrack(media=t.media, wav_path=t.wav_path, time_map=t.time_map)
                for t in self.tracks
            ]
            result = render_final_video_multi(
                self.plan,
                render_tracks,
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
        if "effect" in s and s["effect"] in EFFECTS:
            self.transition.effect = s["effect"]
        if "audio_mode" in s and s["audio_mode"] in ("A", "B", "aligned", "none"):
            self.transition.audio_mode = s["audio_mode"]

    # -- payloads -------------------------------------------------------
    def _source_dict(self, track: Track) -> dict:
        return {
            "index": track.index,
            "label": track.label,
            "name": os.path.basename(track.path),
            "width": track.media.width,
            "height": track.media.height,
            "fps": round(track.media.fps, 2),
            "duration": round(track.features.duration, 2),
            "tempo_bpm": round(track.features.tempo_bpm, 1),
            "is_reference": track.is_reference,
        }

    def status_payload(self) -> dict:
        base = {
            "ready": self.has_alignment,
            "ai_note": self.ai_note,
            "anchor_count": len(self.manual_points),
            "render": self._render_dict(),
            "source_count": len(self.tracks),
            "max_sources": MAX_TRACKS,
        }
        if not self.has_alignment:
            return base
        sources = [self._source_dict(t) for t in self.tracks]
        base["sources"] = sources
        base["a"] = sources[0]
        base["b"] = sources[1]
        base["alignment"] = self._alignment_summary()
        base["plan"] = self.plan.to_dict() if self.plan else None
        return base

    def _alignment_summary(self) -> dict:
        paired = [t for t in self.tracks[1:] if t.alignment is not None]
        if not paired:
            return {"mean_confidence": 1.0, "low_fraction": 0.0, "low_segments": []}
        low_segments: List[List[float]] = []
        for t in paired:
            low_segments.extend(
                [round(a, 2), round(b, 2)]
                for a, b in t.alignment.low_confidence_segments_seconds()
            )
        return {
            "mean_confidence": round(float(np.mean([t.alignment.confidence.mean for t in paired])), 3),
            "low_fraction": round(float(np.mean([t.alignment.confidence.low_fraction for t in paired])), 4),
            "low_segments": low_segments,
        }

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
        ref = self.tracks[0]

        envs: List[dict] = []
        for track in self.tracks:
            et, ev = envelope_from_wav(track.wav_path)
            et, ev = _downsample(et, ev, MAX_WAVE_POINTS)
            envs.append({"index": track.index, "label": track.label, "t": et, "v": ev})

        ref_times = ref.features.times

        maps: List[dict] = []
        for track in self.tracks[1:]:
            if track.time_map is None:
                continue
            mt, mv = _downsample(ref_times, track.time_map.a_to_b_sec, MAX_MAP_POINTS)
            maps.append({"index": track.index, "label": track.label, "a": mt, "b": mv})

        paired = [t for t in self.tracks[1:] if t.alignment is not None]
        if paired:
            conf = np.min(np.vstack([t.alignment.confidence.per_frame for t in paired]), axis=0)
        else:
            conf = np.ones_like(ref_times, dtype=np.float32)
        conf_t, conf_v = _downsample(ref_times, conf, MAX_WAVE_POINTS)

        onsets = onset_peak_times(ref.features)
        if onsets.size > 400:
            step = int(np.ceil(onsets.size / 400))
            onsets = onsets[::step]

        low_segments: List[List[float]] = []
        for t in paired:
            low_segments.extend(
                [round(a, 3), round(b, 3)] for a, b in t.alignment.low_confidence_segments_seconds()
            )

        env_a = envs[0]
        env_b = envs[1] if len(envs) > 1 else {"index": 1, "label": "B", "t": [], "v": []}
        first_map = maps[0] if maps else {"a": [], "b": []}

        return {
            "ready": True,
            "a_duration": round(ref.features.duration, 3),
            "b_duration": round(self.tracks[1].features.duration, 3) if len(self.tracks) > 1 else 0.0,
            "env_a": {"t": env_a["t"], "v": env_a["v"]},
            "env_b": {"t": env_b["t"], "v": env_b["v"]},
            "envs": envs,
            "map": {"a": first_map["a"], "b": first_map["b"]},
            "maps": maps,
            "confidence": {"t": conf_t, "v": conf_v},
            "low_segments": low_segments,
            "onsets": [round(float(t), 3) for t in onsets.tolist()],
            "anchors": [[round(x, 3), round(y, 3)] for x, y in self.manual_points],
            "segments": [
                {
                    "index": s.index,
                    "source": s.source,
                    "source_index": s.source_index,
                    "a_start": round(s.a_start, 3),
                    "a_end": round(s.a_end, 3),
                    "src_start": round(s.src_start, 3) if s.src_start >= 0 else round(s.b_start, 3),
                    "b_start": round(s.b_start, 3),
                    "confidence": round(s.confidence, 3),
                    "reason": s.reason,
                }
                for s in (self.plan.segments if self.plan else [])
            ],
            "low_threshold": self.transition.low_confidence_threshold,
        }
