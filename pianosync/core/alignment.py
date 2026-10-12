"""Alignment domain objects: time mapping between two performances."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import numpy as np

from ..audio.confidence import ConfidenceResult, compute_confidence
from ..audio.dtw import WarpResult, align_feature_sets
from ..audio.features import FeatureSet
from ..config import AlignmentConfig
from ..errors import AlignmentError


@dataclass
class TimeMap:
    """Bidirectional mapping between performance A and performance B times."""

    a_times: np.ndarray
    b_times: np.ndarray
    a_to_b_sec: np.ndarray
    b_to_a_sec: np.ndarray

    def b_for_a(self, t: float) -> float:
        if self.a_times.size == 0:
            return 0.0
        if self.a_times.size == 1:
            return float(self.a_to_b_sec[0])
        return float(np.interp(t, self.a_times, self.a_to_b_sec))

    def a_for_b(self, t: float) -> float:
        if self.b_times.size == 0:
            return 0.0
        if self.b_times.size == 1:
            return float(self.b_to_a_sec[0])
        return float(np.interp(t, self.b_times, self.b_to_a_sec))


@dataclass
class AlignmentResult:
    time_map: TimeMap
    confidence: ConfidenceResult
    warp: WarpResult
    feat_a: FeatureSet
    feat_b: FeatureSet
    a_duration: float = 0.0
    b_duration: float = 0.0

    def low_confidence_segments_seconds(self) -> List[Tuple[float, float]]:
        out: List[Tuple[float, float]] = []
        times = self.feat_a.times
        for start, end in self.confidence.segments:
            if 0 <= start < times.size and 0 <= end < times.size:
                out.append((float(times[start]), float(times[end])))
        return out

    def summary(self) -> Dict[str, float]:
        return {
            "a_duration": self.a_duration,
            "b_duration": self.b_duration,
            "a_tempo_bpm": self.feat_a.tempo_bpm,
            "b_tempo_bpm": self.feat_b.tempo_bpm,
            "mean_confidence": self.confidence.mean,
            "low_confidence_fraction": self.confidence.low_fraction,
        }


def _build_time_map(warp: WarpResult, feat_a: FeatureSet, feat_b: FeatureSet) -> TimeMap:
    a_times = feat_a.times.astype(np.float64)
    b_times = feat_b.times.astype(np.float64)
    if a_times.size == 0 or b_times.size == 0:
        raise AlignmentError("Cannot build a time map from empty feature sets.")
    a_to_b = np.interp(warp.a_to_b, np.arange(b_times.size), b_times)
    b_to_a = np.interp(warp.b_to_a, np.arange(a_times.size), a_times)
    return TimeMap(
        a_times=a_times,
        b_times=b_times,
        a_to_b_sec=a_to_b.astype(np.float64),
        b_to_a_sec=b_to_a.astype(np.float64),
    )


def align_featuresets(
    feat_a: FeatureSet, feat_b: FeatureSet, cfg: AlignmentConfig
) -> AlignmentResult:
    """Run hierarchical DTW and confidence estimation for two feature sets."""
    try:
        warp = align_feature_sets(
            feat_a,
            feat_b,
            target_frames=cfg.downsample_target(),
            band_ratio=cfg.band_ratio,
            onset_weight=cfg.onset_weight,
            refine=cfg.refine,
            free_start=cfg.free_start,
            free_end=cfg.free_end,
        )
    except ValueError as exc:
        raise AlignmentError(str(exc)) from exc

    time_map = _build_time_map(warp, feat_a, feat_b)
    confidence = compute_confidence(
        feat_a, feat_b, warp, onset_weight=cfg.onset_weight
    )
    return AlignmentResult(
        time_map=time_map,
        confidence=confidence,
        warp=warp,
        feat_a=feat_a,
        feat_b=feat_b,
        a_duration=feat_a.duration,
        b_duration=feat_b.duration,
    )
