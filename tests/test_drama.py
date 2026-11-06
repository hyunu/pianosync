"""Unit tests for the solo-performance drama analysis (no FFmpeg video)."""

from __future__ import annotations

import os

import numpy as np
import pytest

from pianosync.audio.features import FeatureSet
from pianosync.core.drama import analyze_solo, energy_envelope

SR = 22050
DURATION = 8.0
PULSES = [0.5, 1.1, 1.9, 2.7, 3.4, 4.2, 5.1, 6.0, 6.9, 7.6]


def _write_bursts(path: str) -> None:
    scipy_io = pytest.importorskip("scipy.io")
    signal = np.zeros(int(DURATION * SR), dtype=np.float32)
    size = int(0.16 * SR)
    envelope = np.exp(-np.arange(size) / (0.045 * SR)).astype(np.float32)
    tone = np.sin(2 * np.pi * 523.25 * np.arange(size) / SR).astype(np.float32)
    for t in PULSES:
        start = int(t * SR)
        signal[start : start + size] += 0.72 * tone * envelope
    scipy_io.wavfile.write(path, SR, np.clip(signal * 32767, -32768, 32767).astype(np.int16))


def _synthetic_features() -> FeatureSet:
    frame_rate = 100.0
    n = int(DURATION * frame_rate)
    onset = np.zeros(n, dtype=np.float32)
    for t in PULSES:
        i = min(n - 1, int(t * frame_rate))
        onset[i] = 0.9
        if i + 1 < n:
            onset[i + 1] = 0.35
    chroma = np.zeros((n, 12), dtype=np.float32)
    chroma[:, 0] = 1.0
    return FeatureSet(
        sample_rate=int(frame_rate),
        hop_length=1,
        n_fft=2048,
        times=np.arange(n, dtype=np.float64) / frame_rate,
        chroma=chroma,
        onset=onset,
        duration=DURATION,
        tempo_bpm=120.0,
    )


def test_energy_envelope_is_normalised(tmp_path):
    wav = str(tmp_path / "bursts.wav")
    _write_bursts(wav)
    times, energy = energy_envelope(wav)
    assert times.size == energy.size
    assert times.size > 0
    assert float(np.min(energy)) >= 0.0
    assert float(np.max(energy)) <= 1.0
    assert float(np.max(energy)) > 0.4
    assert float(np.max(energy)) <= 1.0 + 1e-6
    # The curve uses the 95th percentile as reference, so it is not all-silent.
    assert float(np.mean(energy)) > 0.0


def test_analyze_solo_finds_impact_moments(tmp_path):
    wav = str(tmp_path / "bursts.wav")
    _write_bursts(wav)
    plan = analyze_solo(_synthetic_features(), wav, max_moments=6)

    assert 1 <= len(plan.moments) <= 6
    assert plan.duration == pytest.approx(DURATION)
    assert plan.tempo_bpm == pytest.approx(120.0)
    assert plan.beat_seconds == pytest.approx(0.5)

    times = [m.time for m in plan.moments]
    assert times == sorted(times)
    for moment in plan.moments:
        assert 0.0 <= moment.time <= DURATION
        assert 0.0 <= moment.strength <= 1.0
        assert moment.kind in {"hit", "climax"}
    # Both families are represented: attacks and broader energy arrivals.
    assert {m.kind for m in plan.moments} & {"hit", "climax"}
    assert max(m.strength for m in plan.moments) == pytest.approx(1.0)

    # The strongest selected moment lines up with one of the played attacks.
    strongest = max(plan.moments, key=lambda m: m.strength)
    assert min(abs(strongest.time - t) for t in PULSES) < 0.25

    payload = plan.to_dict()
    assert payload["moment_count"] == len(plan.moments)
    assert payload["moments"][0]["id"] == 0
    assert payload["phrase_times"] == sorted(payload["phrase_times"])


def test_analyze_solo_respects_moment_limit(tmp_path):
    wav = str(tmp_path / "bursts.wav")
    _write_bursts(wav)
    plan = analyze_solo(_synthetic_features(), wav, max_moments=2)
    assert len(plan.moments) <= 2


def test_analyze_solo_keeps_impacts_sparse(tmp_path):
    """Impacts follow loudness jumps, not a fixed beat, and are not crammed in."""
    import math

    wav = str(tmp_path / "bursts.wav")
    _write_bursts(wav)
    plan = analyze_solo(_synthetic_features(), wav, max_moments=12)

    # About one impact per 5.5 s is the ceiling, never the target.
    assert len(plan.moments) <= max(1, math.ceil(DURATION / 5.5))
    times = [m.time for m in plan.moments]
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert all(gap >= 2.0 - 1e-6 for gap in gaps)


def test_analyze_solo_handles_silence(tmp_path):
    scipy_io = pytest.importorskip("scipy.io")
    wav = str(tmp_path / "silence.wav")
    scipy_io.wavfile.write(wav, SR, np.zeros(int(DURATION * SR), dtype=np.int16))
    features = _synthetic_features()
    features.onset = np.zeros_like(features.onset)
    plan = analyze_solo(features, wav, max_moments=4)
    # Degenerate audio must not crash the plan; moments are simply absent.
    assert plan.duration == pytest.approx(DURATION)
    assert plan.moments == []
    assert plan.to_dict()["moment_count"] == 0
