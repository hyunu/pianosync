"""Alignment confidence estimation and low-confidence segment detection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

import numpy as np

from .dtw import WarpResult
from .features import FeatureSet, sequence_for_alignment


@dataclass
class ConfidenceResult:
    per_frame: np.ndarray  # (Na,) 0..1
    cost: np.ndarray  # (Na,) local feature distance
    slope: np.ndarray  # (Na,) local time-warp slope
    mean: float
    low_fraction: float
    segments: List[Tuple[int, int]]  # (start_frame, end_frame) low-confidence runs


def _smooth(x: np.ndarray, window: int) -> np.ndarray:
    window = max(1, int(window))
    if window <= 1 or x.size < window:
        return x
    kernel = np.ones(window, dtype=np.float64) / window
    padded = np.pad(x, (window // 2, window - 1 - window // 2), mode="edge")
    return np.convolve(padded, kernel, mode="valid")[: x.size]


def compute_confidence(
    feat_a: FeatureSet,
    feat_b: FeatureSet,
    warp: WarpResult,
    onset_weight: float = 0.6,
    low_threshold: float = 0.35,
) -> ConfidenceResult:
    """Estimate per-A-frame alignment confidence in [0, 1]."""
    seq_a = sequence_for_alignment(feat_a, onset_weight)
    seq_b = sequence_for_alignment(feat_b, onset_weight)

    idx = np.clip(np.round(warp.a_to_b).astype(int), 0, seq_b.shape[0] - 1)
    cost = np.linalg.norm(seq_a - seq_b[idx], axis=1)
    cost_scale = float(np.percentile(cost, 90)) if cost.size else 1.0
    if cost_scale < 1e-6:
        cost_scale = 1.0
    cost_conf = np.exp(-cost / cost_scale)

    slope = np.gradient(warp.a_to_b)
    positive = slope[slope > 0]
    median_slope = float(np.median(positive)) if positive.size else 1.0
    if median_slope <= 0:
        median_slope = 1.0
    slope_dev = np.abs(slope - median_slope)
    slope_scale = float(np.percentile(slope_dev, 90)) if slope_dev.size else 1.0
    if slope_scale < 1e-6:
        slope_scale = 1.0
    slope_conf = np.exp(-slope_dev / slope_scale)

    per_frame = 0.65 * cost_conf + 0.35 * slope_conf
    per_frame = _smooth(per_frame, max(3, feat_a.n_frames // 100))
    per_frame = np.clip(per_frame, 0.0, 1.0)

    low_mask = per_frame < low_threshold
    segments = _runs(low_mask)
    low_fraction = float(np.mean(low_mask)) if per_frame.size else 0.0
    return ConfidenceResult(
        per_frame=per_frame.astype(np.float32),
        cost=cost.astype(np.float32),
        slope=slope.astype(np.float32),
        mean=float(np.mean(per_frame)) if per_frame.size else 0.0,
        low_fraction=low_fraction,
        segments=segments,
    )


def _runs(mask: np.ndarray, min_length: int = 3) -> List[Tuple[int, int]]:
    runs: List[Tuple[int, int]] = []
    start = None
    for index, flag in enumerate(mask):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            if index - start >= min_length:
                runs.append((start, index - 1))
            start = None
    if start is not None and len(mask) - start >= min_length:
        runs.append((start, len(mask) - 1))
    return runs


def onset_peak_times(
    feat: FeatureSet, min_separation: float = 0.08, threshold_ratio: float = 0.25
) -> np.ndarray:
    """Return times of onset-envelope peaks, useful for boundary snapping."""
    onset = feat.onset
    if onset.size < 3:
        return np.array([], dtype=np.float64)
    threshold = float(np.max(onset)) * threshold_ratio
    min_gap = max(1, int(round(min_separation * feat.frame_rate)))
    peaks: List[int] = []
    last = -min_gap
    for i in range(1, onset.size - 1):
        if onset[i] >= threshold and onset[i] >= onset[i - 1] and onset[i] > onset[i + 1]:
            if i - last >= min_gap:
                peaks.append(i)
                last = i
    return feat.times[np.asarray(peaks, dtype=int)] if peaks else np.array([], dtype=np.float64)
