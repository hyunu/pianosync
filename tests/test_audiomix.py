"""Audio assembly tests."""

from __future__ import annotations

import numpy as np
import pytest

from pianosync.core.alignment import TimeMap
from pianosync.core.transitions import Segment
from pianosync.video.audiomix import assemble_audio


def _time_map(a_times, b_times, a_to_b, b_to_a):
    return TimeMap(
        a_times=np.asarray(a_times, dtype=float),
        b_times=np.asarray(b_times, dtype=float),
        a_to_b_sec=np.asarray(a_to_b, dtype=float),
        b_to_a_sec=np.asarray(b_to_a, dtype=float),
    )


def _segments():
    return [
        Segment(0, "A", 0.0, 2.0, 0.0, 1.6, 0.9, "interval"),
        Segment(1, "B", 2.0, 4.0, 1.6, 3.2, 0.9, "interval"),
        Segment(2, "A", 4.0, 6.0, 3.2, 4.6, 0.9, "interval"),
    ]


def _tm():
    a = np.linspace(0, 6, 601)
    b = a * 0.8
    return _time_map(a, a, b, a / 0.8)


def test_audio_A_mode_length_and_values():
    sr = 8000
    a = np.ones(int(6.0 * sr), dtype=np.float32) * 0.5
    b = np.ones(int(6.0 * sr), dtype=np.float32) * 0.1
    out, out_sr = assemble_audio(_segments(), _tm(), a, sr, b, sr, audio_mode="A")
    assert out_sr == sr
    assert abs(out.size - int(6.0 * sr)) <= 2
    assert np.allclose(out[1000], 0.5, atol=1e-3)


def test_audio_aligned_switches_source():
    sr = 8000
    a = np.ones(int(6.0 * sr), dtype=np.float32) * 0.5
    b = np.ones(int(6.0 * sr), dtype=np.float32) * 0.1
    out, _ = assemble_audio(_segments(), _tm(), a, sr, b, sr, audio_mode="aligned")
    # Segment 1 (B source) spans output 2.0..4.0 s.
    mid = out[int(3.0 * sr)]
    assert abs(mid - 0.1) < 0.05


def test_crossfade_shortens_audio():
    sr = 8000
    a = np.ones(int(6.0 * sr), dtype=np.float32) * 0.5
    b = np.ones(int(6.0 * sr), dtype=np.float32) * 0.5
    out, _ = assemble_audio(
        _segments(), _tm(), a, sr, b, sr, audio_mode="A", effect="crossfade", effect_duration=0.5
    )
    expected = 6.0 * sr - 2 * 0.5 * sr
    assert abs(out.size - expected) < 10


def test_empty_mode_returns_empty():
    sr = 8000
    a = np.ones(int(6.0 * sr), dtype=np.float32)
    out, _ = assemble_audio(_segments(), _tm(), a, sr, a, sr, audio_mode="none")
    assert out.size == 0
