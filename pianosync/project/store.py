"""Project save/load as a single JSON document.

API keys and other secrets are never stored. Analysis artifacts (extracted WAV
and feature caches) live in the analyser cache directory and are re-attached on
load; the persisted alignment arrays let us reopen a project without re-running
DTW.
"""

from __future__ import annotations

import base64
import io
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np

from ..audio.confidence import ConfidenceResult
from ..config import AlignmentConfig, OutputConfig, TransitionConfig
from ..core.alignment import AlignmentResult, TimeMap
from ..core.analysis import AnalysisEngine, PerformanceAnalysis
from ..core.transitions import TransitionPlan
from ..errors import ProjectError

SCHEMA_VERSION = 1


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _arr_to_b64(array: np.ndarray) -> str:
    buffer = io.BytesIO()
    np.save(buffer, np.asarray(array), allow_pickle=False)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _b64_to_arr(text: str) -> np.ndarray:
    try:
        return np.load(io.BytesIO(base64.b64decode(text)), allow_pickle=False)
    except Exception as exc:
        raise ProjectError("A stored analysis array could not be decoded.") from exc


@dataclass
class ProjectErrorDetail:
    message: str
    relink_key: Optional[str] = None  # "a" or "b" if relinking is possible


@dataclass
class Project:
    file_path: str = ""
    created_at: str = field(default_factory=_iso_now)
    updated_at: str = field(default_factory=_iso_now)
    alignment_config: AlignmentConfig = field(default_factory=AlignmentConfig)
    transition_config: TransitionConfig = field(default_factory=TransitionConfig)
    output_config: OutputConfig = field(default_factory=OutputConfig)
    engine: Optional[AnalysisEngine] = None
    perf_a: Optional[PerformanceAnalysis] = None
    perf_b: Optional[PerformanceAnalysis] = None
    alignment: Optional[AlignmentResult] = None
    plan: Optional[TransitionPlan] = None
    manual_points: List[Tuple[float, float]] = field(default_factory=list)

    # -- simple state ------------------------------------------------
    def has_inputs(self) -> bool:
        return self.perf_a is not None and self.perf_b is not None

    def has_alignment(self) -> bool:
        return self.alignment is not None

    def set_engine(self, engine: AnalysisEngine) -> None:
        self.engine = engine

    # -- serialization -----------------------------------------------
    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "alignment_config": self.alignment_config.__dict__,
            "transition_config": self.transition_config.__dict__,
            "output_config": self.output_config.__dict__,
            "inputs": {
                "a": self._perf_to_dict(self.perf_a),
                "b": self._perf_to_dict(self.perf_b),
            },
            "alignment": self._alignment_to_dict(self.alignment),
            "plan": self.plan.to_dict() if self.plan else None,
            "manual_points": [list(p) for p in self.manual_points],
        }

    @staticmethod
    def _perf_to_dict(perf: Optional[PerformanceAnalysis]) -> Optional[dict]:
        if perf is None:
            return None
        media = perf.media
        return {
            "path": perf.path,
            "wav_path": perf.wav_path,
            "media": {
                "duration": media.duration,
                "width": media.width,
                "height": media.height,
                "fps": media.fps,
                "has_audio": media.has_audio,
                "audio_codec": media.audio_codec,
                "format_name": media.format_name,
            },
        }

    @staticmethod
    def _alignment_to_dict(alignment: Optional[AlignmentResult]) -> Optional[dict]:
        if alignment is None:
            return None
        conf = alignment.confidence
        return {
            "a_duration": alignment.a_duration,
            "b_duration": alignment.b_duration,
            "a_times": _arr_to_b64(alignment.time_map.a_times),
            "b_times": _arr_to_b64(alignment.time_map.b_times),
            "a_to_b_sec": _arr_to_b64(alignment.time_map.a_to_b_sec),
            "b_to_a_sec": _arr_to_b64(alignment.time_map.b_to_a_sec),
            "confidence": {
                "per_frame": _arr_to_b64(conf.per_frame),
                "mean": conf.mean,
                "low_fraction": conf.low_fraction,
                "segments": [list(s) for s in conf.segments],
            },
        }

    @classmethod
    def from_dict(cls, data: dict, engine: AnalysisEngine) -> "Project":
        if int(data.get("schema_version", 0)) != SCHEMA_VERSION:
            raise ProjectError(
                f"Unsupported project version {data.get('schema_version')}."
            )
        cfg_kwargs = lambda key: {  # noqa: E731
            k: v
            for k, v in (data.get(key, {}) or {}).items()
            if k
            in {
                "sample_rate", "hop_length", "n_fft", "quality", "target_frames",
                "band_ratio", "onset_weight", "chroma_weight", "refine",
                "free_start", "free_end",
            }
        }
        project = cls(
            created_at=str(data.get("created_at", _iso_now())),
            updated_at=str(data.get("updated_at", _iso_now())),
            alignment_config=AlignmentConfig(**cfg_kwargs("alignment_config")),
        )
        project.engine = engine
        project.transition_config = _apply_dict(
            project.transition_config, data.get("transition_config")
        )
        project.output_config = _apply_dict(
            project.output_config, data.get("output_config")
        )

        inputs = data.get("inputs", {}) or {}
        project.perf_a = _perf_from_dict(engine, inputs.get("a"), "a")
        project.perf_b = _perf_from_dict(engine, inputs.get("b"), "b")
        project.alignment = _alignment_from_dict(
            data.get("alignment"), project.perf_a, project.perf_b
        )
        if data.get("plan"):
            project.plan = TransitionPlan.from_dict(data["plan"])
        project.manual_points = [
            (float(p[0]), float(p[1]))
            for p in (data.get("manual_points") or [])
            if isinstance(p, list) and len(p) >= 2
        ]
        return project

    # -- mutation -----------------------------------------------------
    def mark_updated(self) -> None:
        self.updated_at = _iso_now()


def _apply_dict(instance, values: Optional[dict]):
    if not values:
        return instance
    allowed = set(instance.__dict__.keys())
    for key, value in values.items():
        if key in allowed:
            setattr(instance, key, value)
    return instance


def _perf_from_dict(
    engine: AnalysisEngine, data: Optional[dict], side: str
) -> Optional[PerformanceAnalysis]:
    if not data:
        return None
    path = str(data.get("path", ""))
    if not path:
        return None
    if not os.path.isfile(path):
        raise ProjectErrorDetailException(
            ProjectErrorDetail(
                f"Input file '{path}' (side {side}) no longer exists.",
                relink_key=side,
            )
        )
    return engine.analyze_performance(path, use_cache=True)


def _alignment_from_dict(
    data: Optional[dict],
    perf_a: Optional[PerformanceAnalysis],
    perf_b: Optional[PerformanceAnalysis],
) -> Optional[AlignmentResult]:
    if not data or not perf_a or not perf_b:
        return None
    time_map = TimeMap(
        a_times=_b64_to_arr(data["a_times"]),
        b_times=_b64_to_arr(data["b_times"]),
        a_to_b_sec=_b64_to_arr(data["a_to_b_sec"]),
        b_to_a_sec=_b64_to_arr(data["b_to_a_sec"]),
    )
    conf_data = data.get("confidence", {})
    per_frame = _b64_to_arr(conf_data.get("per_frame", np.array([0.0])))
    confidence = ConfidenceResult(
        per_frame=per_frame.astype(np.float32),
        cost=per_frame.astype(np.float32),
        slope=np.zeros_like(per_frame),
        mean=float(conf_data.get("mean", 0.0)),
        low_fraction=float(conf_data.get("low_fraction", 0.0)),
        segments=[(int(s[0]), int(s[1])) for s in conf_data.get("segments", [])],
    )
    return AlignmentResult(
        time_map=time_map,
        confidence=confidence,
        warp=None,  # not required for editing/rendering
        feat_a=perf_a.features,
        feat_b=perf_b.features,
        a_duration=float(data.get("a_duration", perf_a.features.duration)),
        b_duration=float(data.get("b_duration", perf_b.features.duration)),
    )


class ProjectErrorDetailException(Exception):
    """Internal carrier for a user-facing relink prompt."""


def save_project(project: Project, path: str) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    project.file_path = os.path.abspath(path)
    project.mark_updated()
    payload = project.to_dict()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_project(path: str, engine: AnalysisEngine) -> Project:
    if not os.path.isfile(path):
        raise ProjectError(f"Project file not found: {path}")
    with open(path, "r", encoding="utf-8") as handle:
        try:
            data = json.load(handle)
        except json.JSONDecodeError as exc:
            raise ProjectError(f"'{path}' is not a valid project JSON.") from exc
    try:
        project = Project.from_dict(data, engine)
    except ProjectErrorDetailException as exc:
        detail = exc.args[0]
        relink_key = detail.relink_key
        missing = ""
        inputs = (data.get("inputs") or {}).get("a", {})
        if relink_key is None:
            for key in ("a", "b"):
                ref = ((data.get("inputs") or {}).get(key) or {}).get("path")
                if ref and not os.path.isfile(ref):
                    relink_key = key
                    missing = str(ref) or ""
                    break
        raise ProjectError(
            f"The input file for side {relink_key or '?'} is missing "
            f"({missing or 'unknown path'}). Please relink it and try again."
        )
    project.file_path = os.path.abspath(path)
    return project