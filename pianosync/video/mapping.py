"""Time-mapping helpers shared by audio assembly and video rendering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

import numpy as np

from ..config import (
    OutputConfig,
    SIZE_PRESETS,
    normalize_size,
)
from ..core.alignment import TimeMap
from ..core.transitions import Segment


@dataclass
class SegmentTiming:
    index: int
    source: str
    src_start: float  # in-point inside the shown source video
    out_duration: float  # length on the output timeline
    a_start: float
    a_end: float
    input_index: int = 0  # 0-based ffmpeg input backing ``source``


def plan_segment_timings(segments: List[Segment], b_duration: float) -> List[SegmentTiming]:
    """Convert alignment segments into concrete source in/out points.

    Output duration is the reference (A) span, so audio taken from A stays
    naturally continuous. For B-sourced segments the in-point is the mapped B
    time and the clip is clamped to B's length.
    """
    timings: List[SegmentTiming] = []
    for seg in segments:
        out_duration = max(0.0, seg.a_end - seg.a_start)
        if out_duration <= 0:
            continue
        if seg.source == "B":
            src_start = max(0.0, min(seg.b_start, max(b_duration - 0.01, 0.0)))
        else:
            src_start = max(0.0, seg.a_start)
        timings.append(
            SegmentTiming(
                index=seg.index,
                source=seg.source,
                src_start=src_start,
                out_duration=out_duration,
                a_start=seg.a_start,
                a_end=seg.a_end,
                input_index=0 if seg.source == "A" else 1,
            )
        )
    return timings


def plan_segment_timings_multi(
    segments: List[Segment], source_durations: List[float]
) -> List[SegmentTiming]:
    """Like :func:`plan_segment_timings` but for an arbitrary number of sources.

    ``source_durations[k]`` is the length of the media backing track ``k``; the
    in-point comes from ``Segment.src_start`` (falling back to the reference
    time for the reference track).
    """
    timings: List[SegmentTiming] = []
    for seg in segments:
        out_duration = max(0.0, seg.a_end - seg.a_start)
        if out_duration <= 0:
            continue
        index = int(getattr(seg, "source_index", 0) or 0)
        index = min(max(index, 0), max(len(source_durations) - 1, 0))
        duration = source_durations[index] if index < len(source_durations) else 0.0
        raw_start = getattr(seg, "src_start", -1.0)
        if raw_start is None or raw_start < 0:
            raw_start = seg.a_start if index == 0 else seg.b_start
        if duration:
            src_start = max(0.0, min(float(raw_start), max(duration - 0.01, 0.0)))
        else:
            src_start = max(0.0, float(raw_start))
        timings.append(
            SegmentTiming(
                index=seg.index,
                source=seg.source,
                src_start=src_start,
                out_duration=out_duration,
                a_start=seg.a_start,
                a_end=seg.a_end,
                input_index=index,
            )
        )
    return timings


def resolve_output_size(
    output: OutputConfig, a_width: int, a_height: int
) -> Tuple[int, int]:
    """Return the output (width, height), even-ised for H.264.

    Precedence: explicit ``width``/``height`` (both non-zero) > the ``size``
    preset > the reference source resolution. The default preset is
    ``"original"``, which preserves the source resolution *and orientation* so a
    portrait phone recording is not letterboxed into a landscape frame.
    """
    if output.width and output.height:
        width, height = int(output.width), int(output.height)
    else:
        preset = SIZE_PRESETS.get(normalize_size(getattr(output, "size", None)))
        if preset is None:  # "original" (or unknown) -> follow the source
            width = int(output.width or a_width or 1920)
            height = int(output.height or a_height or 1080)
        else:
            width = int(output.width or preset[0])
            height = int(output.height or preset[1])
    width -= width % 2
    height -= height % 2
    return max(width, 2), max(height, 2)


def _clamp_index(values: np.ndarray, upper: int) -> np.ndarray:
    return np.clip(values, 0.0, float(upper))


def warp_signal_to_reference(
    signal: np.ndarray,
    sample_rate: int,
    time_map: TimeMap,
    grid_times: np.ndarray,
) -> np.ndarray:
    """Resample ``signal`` so its musical positions line up with ``grid_times``.

    ``grid_times`` are absolute times on the reference (A) clock; the returned
    signal has one sample per grid point, taken from B at the mapped time.
    """
    if signal.size == 0 or grid_times.size == 0:
        return np.zeros(grid_times.size, dtype=np.float32)
    mapped_times = np.interp(grid_times, time_map.a_times, time_map.a_to_b_sec)
    sample_positions = _clamp_index(mapped_times * sample_rate, signal.size - 1)
    axis = np.arange(signal.size, dtype=np.float64)
    warped = np.interp(sample_positions, axis, signal.astype(np.float64))
    return warped.astype(np.float32)
