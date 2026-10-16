"""Assemble a single, click-free audio track for the rendered video.

Audio is generated in the reference (A) time base so that it always matches the
output timeline. This keeps A's performance continuous by construction and lets
us time-map the other performances onto the same clock when needed.

Boundaries where the picture *and* the audio source change are crossfaded over a
short window so the hard cut between two performances never produces a click.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from ..config import XFADE_EFFECTS
from ..core.alignment import TimeMap
from ..core.transitions import Segment
from .mapping import warp_signal_to_reference

#: Length of the anti-click crossfade applied when the audio source changes.
ANTICLICK_SECONDS = 0.012
#: Tiny fade applied at the very start/end of the assembled track.
EDGE_FADE_SECONDS = 0.006


def _resample_linear(signal: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    if src_sr == dst_sr or signal.size == 0:
        return signal.astype(np.float32)
    duration = signal.size / float(src_sr)
    n_out = max(1, int(round(duration * dst_sr)))
    src_axis = np.arange(signal.size, dtype=np.float64) / float(src_sr)
    dst_axis = np.arange(n_out, dtype=np.float64) / float(dst_sr)
    return np.interp(dst_axis, src_axis, signal.astype(np.float64)).astype(np.float32)


def _edge_fades(signal: np.ndarray, sample_rate: int) -> np.ndarray:
    """Fade the very first/last few milliseconds to silence."""
    n = int(round(EDGE_FADE_SECONDS * sample_rate))
    if n <= 1 or signal.size <= 2 * n:
        return signal
    out = signal.copy()
    ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
    out[:n] *= ramp
    out[-n:] *= ramp[::-1]
    return out


def _crossfade_chain(
    signal: np.ndarray, seg_lengths: List[int], fade: int
) -> np.ndarray:
    """Overlap consecutive segments by ``fade`` samples (shortens the result).

    Used for the ``crossfade`` effect, where the picture is also overlapped and
    the final duration is shortened to match.
    """
    if fade <= 1 or len(seg_lengths) <= 1:
        return signal
    acc = signal[: seg_lengths[0]].copy()
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


def _crossfade_boundaries(
    track: np.ndarray,
    seg_lengths: List[int],
    source_of_seg: List[int],
    sources: Dict[int, np.ndarray],
    fade: int,
) -> np.ndarray:
    """Crossfade ``track`` in place at boundaries where the source changes.

    The length is preserved (unlike :func:`_crossfade_chain`): the outgoing and
    incoming performances are blended over a short window centred on the cut, so
    a hard picture cut is click-free while staying in sync.
    """
    if fade <= 1 or len(seg_lengths) <= 1:
        return track
    total = track.size
    offset = 0
    for i in range(len(seg_lengths) - 1):
        offset += seg_lengths[i]
        prev_src = source_of_seg[i]
        next_src = source_of_seg[i + 1]
        if prev_src == next_src:
            continue
        prev_sig = sources.get(prev_src)
        next_sig = sources.get(next_src)
        if prev_sig is None or next_sig is None:
            continue
        half = fade // 2
        lo = max(0, offset - half)
        hi = min(total, offset + half)
        if hi - lo <= 1:
            continue
        ramp = np.linspace(0.0, 1.0, hi - lo, dtype=np.float32)
        track[lo:hi] = prev_sig[lo:hi] * (1.0 - ramp) + next_sig[lo:hi] * ramp
    return track


def _fade_samples(sample_rate: int, seg_lengths: List[int], seconds: float) -> int:
    fade = int(round(seconds * sample_rate))
    if seg_lengths:
        fade = min(fade, int(min(seg_lengths) * 0.4))
    return max(0, fade)


def _slice_grid(signal: np.ndarray, start: int, total: int) -> np.ndarray:
    """Return ``total`` samples starting at ``start``, zero-padded if short."""
    out = signal[start : start + total]
    if out.size < total:
        out = np.pad(out, (0, total - out.size))
    return out


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
    a_track = _slice_grid(a_signal, start, total_samples)
    b_track = warp_signal_to_reference(b_signal, sample_rate, time_map, grid_times)

    sources: Dict[int, np.ndarray] = {0: a_track, 1: b_track}
    source_of_seg = [0 if s.source == "A" else 1 for s in segments]

    if audio_mode in ("A", "single_A"):
        track = a_track.copy()
    elif audio_mode in ("B", "single_B"):
        track = b_track.copy()
    elif audio_mode == "aligned":
        track = np.zeros(total_samples, dtype=np.float32)
        offset = 0
        for src, length in zip(source_of_seg, seg_lengths):
            track[offset : offset + length] = sources[src][offset : offset + length]
            offset += length
    else:
        track = a_track.copy()

    if effect in XFADE_EFFECTS and len(segments) > 1:
        fade = _fade_samples(sample_rate, seg_lengths, effect_duration)
        if fade > 1:
            return _edge_fades(_crossfade_chain(track, seg_lengths, fade), sample_rate), sample_rate

    if audio_mode == "aligned":
        fade = _fade_samples(sample_rate, seg_lengths, ANTICLICK_SECONDS)
        track = _crossfade_boundaries(track, seg_lengths, source_of_seg, sources, fade)

    return _edge_fades(track, sample_rate), sample_rate


def assemble_audio_multi(
    segments: List[Segment],
    track_signals: Sequence[Tuple[np.ndarray, int, Optional[TimeMap]]],
    audio_mode: str = "A",
    effect: str = "cut",
    effect_duration: float = 0.35,
) -> Tuple[np.ndarray, int]:
    """Assemble audio for a project with up to ``MAX_TRACKS`` performances.

    ``track_signals`` holds ``(signal, sample_rate, time_map)`` per performance in
    registration order; ``time_map`` is ``None`` for the reference. Every track is
    warped onto the reference clock so switching between performances stays in
    sync, and cuts between different performers are crossfaded.
    """
    if audio_mode == "none" or not segments or not track_signals:
        sr = int(track_signals[0][1]) if track_signals else 22050
        return np.zeros(0, dtype=np.float32), sr

    sample_rate = int(track_signals[0][1])

    durations = [max(0.0, s.a_end - s.a_start) for s in segments]
    seg_lengths = [int(round(d * sample_rate)) for d in durations]
    total_samples = int(sum(seg_lengths))
    if total_samples <= 0:
        return np.zeros(0, dtype=np.float32), sample_rate

    a_first = segments[0].a_start
    grid_times = a_first + np.arange(total_samples, dtype=np.float64) / sample_rate
    start = int(round(a_first * sample_rate))

    sources: Dict[int, np.ndarray] = {}
    for k, (signal, sr, time_map) in enumerate(track_signals):
        signal = np.asarray(signal, dtype=np.float32)
        if time_map is None:
            tracked = _resample_linear(signal, sr, sample_rate)
            sources[k] = _slice_grid(tracked, start, total_samples)
        else:
            tracked = _resample_linear(signal, sr, sample_rate)
            sources[k] = warp_signal_to_reference(tracked, sample_rate, time_map, grid_times)

    source_of_seg = [int(getattr(s, "source_index", 0) or 0) for s in segments]

    if audio_mode == "aligned":
        track = np.zeros(total_samples, dtype=np.float32)
        offset = 0
        for src, length in zip(source_of_seg, seg_lengths):
            sig = sources.get(src, sources[0])
            track[offset : offset + length] = sig[offset : offset + length]
            offset += length
    else:
        pick = 0
        if audio_mode in ("B", "single_B"):
            pick = 1 if len(track_signals) > 1 else 0
        track = sources[pick].copy()

    if effect in XFADE_EFFECTS and len(segments) > 1:
        fade = _fade_samples(sample_rate, seg_lengths, effect_duration)
        if fade > 1:
            return _edge_fades(_crossfade_chain(track, seg_lengths, fade), sample_rate), sample_rate

    if audio_mode == "aligned":
        fade = _fade_samples(sample_rate, seg_lengths, ANTICLICK_SECONDS)
        track = _crossfade_boundaries(track, seg_lengths, [int(getattr(s, "source_index", 0) or 0) for s in segments], sources, fade)

    return _edge_fades(track, sample_rate), sample_rate
