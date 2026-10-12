"""Assemble a single, click-free audio track for the rendered video.

Audio is generated in the reference (A) time base so that it always matches the
output timeline. This keeps A's performance continuous by construction and lets
us time-map B's performance onto the same clock when needed.
"""

from __future__ import annotations

from typing import List, Tuple

import numpy as np

from ..core.alignment import TimeMap
from ..core.transitions import Segment
from .mapping import warp_signal_to_reference


def _resample_linear(signal: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    if src_sr == dst_sr or signal.size == 0:
        return signal.astype(np.float32)
    duration = signal.size / float(src_sr)
    n_out = max(1, int(round(duration * dst_sr)))
    src_axis = np.arange(signal.size, dtype=np.float64) / float(src_sr)
    dst_axis = np.arange(n_out, dtype=np.float64) / float(dst_sr)
    return np.interp(dst_axis, src_axis, signal.astype(np.float64)).astype(np.float32)


def _micro_fades(signal: np.ndarray, boundaries: List[int], fade: int) -> np.ndarray:
    if fade <= 1:
        return signal
    out = signal.copy()
    for b in boundaries:
        lo = max(0, b - fade)
        hi = min(out.size, b + fade)
        if hi <= lo:
            continue
        ramp = np.linspace(0.0, 1.0, hi - lo, dtype=np.float32)
        if b - lo > 0:
            out[lo:b] *= ramp[: b - lo]
        if hi - b > 0:
            out[b:hi] *= ramp[b - lo :]
    return out


def _crossfade_chain(
    signal: np.ndarray, seg_lengths: List[int], fade: int
) -> np.ndarray:
    if fade <= 1 or len(seg_lengths) <= 1:
        return signal
    offset = 0
    pieces: List[np.ndarray] = []
    first = signal[: seg_lengths[0]].copy()
    acc = first
    offset = seg_lengths[0]
    for length in seg_lengths[1:]:
        seg = signal[offset : offset + length]
        offset += length
        ov = min(fade, acc.size, seg.size)
        if ov > 1:
            ramp = np.linspace(0.0, 1.0, ov, dtype=np.float32)
            acc = acc.copy()
            acc[-ov:] = acc[-ov:] * (1.0 - ramp) + seg[:ov] * ramp
        acc = np.concatenate([acc, seg[ov:]])
    return acc


def assemble_audio(
    segments: List[Segment],
    time_map: TimeMap,
    a_signal: np.ndarray,
    a_sr: int,
    b_signal: np.ndarray,
    b_sr: int,
    audio_mode: str = "A",
    effect: str = "cut",
    effect_duration: float = 0.35,
) -> Tuple[np.ndarray, int]:
    """Return ``(samples, sample_rate)`` for the assembled output audio."""
    sample_rate = a_sr
    a_signal = np.asarray(a_signal, dtype=np.float32)
    b_signal = _resample_linear(np.asarray(b_signal, dtype=np.float32), b_sr, sample_rate)

    if audio_mode == "none" or not segments:
        return np.zeros(0, dtype=np.float32), sample_rate

    durations = [max(0.0, s.a_end - s.a_start) for s in segments]
    seg_lengths = [int(round(d * sample_rate)) for d in durations]
    total_samples = int(sum(seg_lengths))
    if total_samples <= 0:
        return np.zeros(0, dtype=np.float32), sample_rate

    a_first = segments[0].a_start
    grid_times = a_first + np.arange(total_samples, dtype=np.float64) / sample_rate

    start = int(round(a_first * sample_rate))
    a_track = a_signal[start : start + total_samples]
    if a_track.size < total_samples:
        a_track = np.pad(a_track, (0, total_samples - a_track.size))
    b_track = warp_signal_to_reference(b_signal, sample_rate, time_map, grid_times)

    if audio_mode in ("A", "single_A"):
        track = a_track.copy()
    elif audio_mode in ("B", "single_B"):
        track = b_track.copy()
    elif audio_mode == "aligned":
        track = np.empty(total_samples, dtype=np.float32)
        offset = 0
        for seg, length in zip(segments, seg_lengths):
            source_track = a_track if seg.source == "A" else b_track
            track[offset : offset + length] = source_track[offset : offset + length]
            offset += length
    else:
        track = a_track.copy()

    boundaries: List[int] = []
    offset = 0
    for length in seg_lengths[:-1]:
        offset += length
        boundaries.append(offset)

    if effect == "crossfade" and len(segments) > 1:
        min_len = min(seg_lengths) if seg_lengths else 0
        fade = int(min(effect_duration * sample_rate, min_len * 0.4))
        if fade > 1:
            track = _crossfade_chain(track, seg_lengths, fade)
            return track.astype(np.float32), sample_rate

    track = _micro_fades(track, boundaries, int(0.005 * sample_rate))
    return track.astype(np.float32), sample_rate
