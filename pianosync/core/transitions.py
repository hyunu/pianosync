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

#: Labels assigned to the 0th..7th performance (matches core.analysis.LABELS).
LABELS = "ABCDEFGH"


@dataclass
class Segment:
    index: int
    source: str  # "A", "B", "C", … — the performance shown during this segment
    a_start: float
    a_end: float
    b_start: float
    b_end: float
    confidence: float
    reason: str
    user_modified: bool = False
    source_index: int = 0  # 0-based track index backing ``source``
    src_start: float = -1.0  # in-point inside the shown performance (-1 = derive)

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
                    "source_index": s.source_index,
                    "src_start": s.src_start,
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
                source_index=int(s.get("source_index", 0)),
                src_start=float(s.get("src_start", -1.0)),
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
                source_index=0 if current_source == "A" else 1,
                src_start=seg_start if current_source == "A" else b_start,
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


def _multi_segment_confidence(tracks, a_start: float, a_end: float) -> float:
    """Worst-case confidence across every non-reference track in a span."""
    worst = 1.0
    found = False
    for track in tracks[1:]:
        alignment = getattr(track, "alignment", None)
        if alignment is None:
            continue
        per_frame = alignment.confidence.per_frame
        times = alignment.feat_a.times
        mask = (times >= a_start) & (times < a_end)
        if not np.any(mask):
            value = float(np.mean(per_frame)) if per_frame.size else 0.0
        else:
            value = float(np.mean(per_frame[mask]))
        worst = min(worst, value)
        found = True
    return worst if found else 1.0


def generate_transitions_multi(
    tracks: list,
    cfg: TransitionConfig,
    a_start: Optional[float] = None,
    a_end: Optional[float] = None,
    user_points: Optional[List[Tuple[float, float]]] = None,
    order: Optional[List[int]] = None,
) -> TransitionPlan:
    """Create a segment plan that rotates through several aligned performances.

    Boundaries live on the reference (track 0) timeline exactly as in
    :func:`generate_transitions`; only the shown source cycles through
    ``order`` (default: registration order). ``user_points`` are (reference,
    second-track) anchors applied to the second track's in-point.
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

    ref_features = tracks[0].features
    n = len(tracks)
    if a_start is None:
        a_start = float(ref_features.times[0]) if ref_features.times.size else 0.0
    if a_end is None:
        ref_end = float(ref_features.times[-1]) if ref_features.times.size else 0.0
        ref_duration = float(getattr(tracks[0], "media").duration) if getattr(tracks[0], "media", None) else 0.0
        a_end = float(min(ref_duration, ref_end)) if ref_duration else ref_end
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
        onsets = onset_peak_times(ref_features)
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

    cleaned = [boundaries[0]]
    for b in boundaries[1:]:
        if b - cleaned[-1] >= min(cfg.min_interval, interval) * 0.25:
            cleaned.append(b)
        else:
            warnings.append(f"Dropped a transition near {b:.2f}s to keep intervals apart.")
    if cleaned[-1] < a_end:
        cleaned[-1] = a_end
    boundaries = cleaned

    cycle = [int(i) for i in (order if order else range(n)) if 0 <= int(i) < n]
    if not cycle:
        cycle = list(range(n))

    start_pos = 0
    sw = (cfg.start_with or "A").strip().upper()
    if len(sw) == 1 and sw in LABELS:
        wanted = LABELS.index(sw)
        if wanted in cycle:
            start_pos = cycle.index(wanted)
    anchors = sorted(user_points or [], key=lambda p: p[0])

    segments: List[Segment] = []
    for index in range(len(boundaries) - 1):
        seg_start = boundaries[index]
        seg_end = boundaries[index + 1]
        if cfg.alternate:
            source_index = cycle[(start_pos + index) % len(cycle)]
        else:
            source_index = cycle[start_pos % len(cycle)]
        source_track = tracks[source_index]
        src_start = float(source_track.source_time(seg_start))
        if source_index == 1 and anchors and getattr(source_track, "time_map", None) is not None:
            nearest = min(anchors, key=lambda p: abs(p[0] - seg_start))
            if abs(nearest[0] - seg_start) <= cfg.snap_tolerance:
                src_start = float(nearest[1])
        b_start = float(tracks[1].source_time(seg_start)) if n >= 2 else seg_start
        b_end = float(tracks[1].source_time(seg_end)) if n >= 2 else seg_end
        confidence = _multi_segment_confidence(tracks, seg_start, seg_end)
        reason = "interval"
        if index < len(snapped_flags) - 1 and snapped_flags[index]:
            reason = "onset_snap"
        if confidence < cfg.low_confidence_threshold:
            reason += "+low_confidence"
            warnings.append(
                f"Segment {index} ({seg_start:.2f}s-{seg_end:.2f}s) has low alignment "
                f"confidence ({confidence:.2f}); verify it in the preview."
            )
        segments.append(
            Segment(
                index=index,
                source=source_track.label,
                a_start=seg_start,
                a_end=seg_end,
                b_start=b_start,
                b_end=b_end,
                confidence=confidence,
                reason=reason,
                source_index=source_index,
                src_start=src_start,
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
