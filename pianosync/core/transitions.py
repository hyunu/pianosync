"""Transition-point generation from an alignment result.

Two policies are supported:

* ``musical`` (mode 1): boundaries target multiples of the requested interval on
  the reference (A) timeline but snap to nearby musical onsets, so the cadence
  follows the music and each video may play for a different length.
* ``fixed_output`` (mode 2): boundaries are exactly multiples of the interval on
  the reference/output timeline, guaranteeing a constant switching cadence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from ..audio.confidence import onset_peak_times
from ..config import TransitionConfig
from .alignment import AlignmentResult


@dataclass
class Segment:
    index: int
    source: str  # "A" or "B" — the video shown during this segment
    a_start: float
    a_end: float
    b_start: float
    b_end: float
    confidence: float
    reason: str
    user_modified: bool = False

    @property
    def duration(self) -> float:
        return max(0.0, self.a_end - self.a_start)


@dataclass
class TransitionPlan:
    segments: List[Segment]
    total_duration: float
    mode: str
    start_with: str
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "start_with": self.start_with,
            "total_duration": self.total_duration,
            "warnings": self.warnings,
            "segments": [
                {
                    "index": s.index,
                    "source": s.source,
                    "a_start": s.a_start,
                    "a_end": s.a_end,
                    "b_start": s.b_start,
                    "b_end": s.b_end,
                    "confidence": s.confidence,
                    "reason": s.reason,
                    "user_modified": s.user_modified,
                }
                for s in self.segments
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TransitionPlan":
        segments = [
            Segment(
                index=int(s["index"]),
                source=str(s["source"]),
                a_start=float(s["a_start"]),
                a_end=float(s["a_end"]),
                b_start=float(s["b_start"]),
                b_end=float(s["b_end"]),
                confidence=float(s.get("confidence", 0.0)),
                reason=str(s.get("reason", "")),
                user_modified=bool(s.get("user_modified", False)),
            )
            for s in data.get("segments", [])
        ]
        return cls(
            segments=segments,
            total_duration=float(data.get("total_duration", 0.0)),
            mode=str(data.get("mode", "musical")),
            start_with=str(data.get("start_with", "A")),
            warnings=list(data.get("warnings", [])),
        )


def _snap(value: float, candidates: np.ndarray, tolerance: float) -> Tuple[float, bool]:
    if candidates.size == 0:
        return value, False
    distances = np.abs(candidates - value)
    nearest = int(np.argmin(distances))
    if distances[nearest] <= tolerance:
        return float(candidates[nearest]), True
    return value, False


def _segment_confidence(result: AlignmentResult, a_start: float, a_end: float) -> float:
    times = result.feat_a.times
    mask = (times >= a_start) & (times < a_end)
    if not np.any(mask):
        return float(np.mean(result.confidence.per_frame)) if result.confidence.per_frame.size else 0.0
    return float(np.mean(result.confidence.per_frame[mask]))


def generate_transitions(
    result: AlignmentResult,
    cfg: TransitionConfig,
    a_start: Optional[float] = None,
    a_end: Optional[float] = None,
    user_points: Optional[List[Tuple[float, float]]] = None,
) -> TransitionPlan:
    """Create the segment / transition plan for the final video.

    ``user_points`` are manual (A time, B time) correspondences that are treated
    as hard anchors: boundaries near them are forced to the user's mapping.
    """
    warnings: List[str] = []
    interval = float(cfg.interval_seconds)
    if interval < cfg.min_interval:
        warnings.append(
            f"Interval {interval:.2f}s is below the minimum ({cfg.min_interval:.2f}s); "
            f"using {cfg.min_interval:.2f}s."
        )
        interval = cfg.min_interval
    if interval > cfg.max_interval:
        warnings.append(
            f"Interval {interval:.2f}s exceeds the maximum ({cfg.max_interval:.2f}s); "
            f"using {cfg.max_interval:.2f}s."
        )
        interval = cfg.max_interval

    if a_start is None:
        a_start = float(result.feat_a.times[0]) if result.feat_a.times.size else 0.0
    if a_end is None:
        a_end = float(min(result.a_duration, result.feat_a.times[-1])) if result.feat_a.times.size else result.a_duration
    if a_end <= a_start:
        raise ValueError("Invalid analysis range for transition generation.")

    boundaries = [a_start]
    k = 1
    while a_start + k * interval < a_end - 1e-6:
        boundaries.append(a_start + k * interval)
        k += 1
    boundaries.append(a_end)

    snapped_flags: List[bool] = []
    if cfg.mode == "musical" and cfg.snap_to_onset:
        onsets = onset_peak_times(result.feat_a)
        new_boundaries = [boundaries[0]]
        flags = [False]
        for b in boundaries[1:-1]:
            value, did = _snap(b, onsets, cfg.snap_tolerance)
            new_boundaries.append(value)
            flags.append(did)
        new_boundaries.append(boundaries[-1])
        flags.append(False)
        boundaries = new_boundaries
        snapped_flags = flags
    else:
        snapped_flags = [False] * len(boundaries)

    # Ensure strictly increasing boundaries after snapping.
    cleaned = [boundaries[0]]
    for b in boundaries[1:]:
        if b - cleaned[-1] >= min(cfg.min_interval, interval) * 0.25:
            cleaned.append(b)
        else:
            warnings.append(
                f"Dropped a transition near {b:.2f}s to keep intervals apart."
            )
    if cleaned[-1] < a_end:
        cleaned[-1] = a_end
    boundaries = cleaned

    # Apply user anchor points by adjusting the nearest boundary's B mapping.
    anchors = sorted(user_points or [], key=lambda p: p[0])

    segments: List[Segment] = []
    source = "A" if cfg.start_with.upper() != "B" else "B"
    for index in range(len(boundaries) - 1):
        seg_start = boundaries[index]
        seg_end = boundaries[index + 1]
        b_start = result.time_map.b_for_a(seg_start)
        b_end = result.time_map.b_for_a(seg_end)
        if anchors:
            nearest = min(anchors, key=lambda p: abs(p[0] - seg_start))
            if abs(nearest[0] - seg_start) <= cfg.snap_tolerance:
                b_start = nearest[1]
                b_end = result.time_map.b_for_a(seg_end)
        confidence = _segment_confidence(result, seg_start, seg_end)
        reason = "interval"
        if index < len(snapped_flags) - 1 and snapped_flags[index]:
            reason = "onset_snap"
        if confidence < cfg.low_confidence_threshold:
            reason += "+low_confidence"
            warnings.append(
                f"Segment {index} ({seg_start:.2f}s-{seg_end:.2f}s) has low alignment "
                f"confidence ({confidence:.2f}); verify it in the preview."
            )
        current_source = source
        if cfg.alternate:
            source = "B" if source == "A" else "A"
        segments.append(
            Segment(
                index=index,
                source=current_source,
                a_start=seg_start,
                a_end=seg_end,
                b_start=b_start,
                b_end=b_end,
                confidence=confidence,
                reason=reason,
            )
        )

    total_duration = sum(s.duration for s in segments)
    return TransitionPlan(
        segments=segments,
        total_duration=total_duration,
        mode=cfg.mode,
        start_with=segments[0].source if segments else cfg.start_with,
        warnings=warnings,
    )
