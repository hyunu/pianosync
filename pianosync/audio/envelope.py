"""Lightweight waveform envelope for display (RMS per window)."""

from __future__ import annotations

import numpy as np

from .extract import load_wav


def envelope_from_wav(wav_path: str, points_per_second: int = 200):
    """Return ``(times, rms)`` arrays suitable for drawing a waveform strip."""
    signal, sample_rate = load_wav(wav_path)
    if signal.size == 0:
        return [], []
    window = max(1, int(sample_rate // points_per_second))
    n_windows = signal.size // window
    if n_windows == 0:
        n_windows = 1
    trimmed = signal[: n_windows * window].reshape(n_windows, window)
    rms = np.sqrt(np.mean(trimmed.astype(np.float64) ** 2, axis=1))
    rms = np.minimum(rms / 0.2, 1.0)  # normalise reference level
    times = np.arange(n_windows) * window / float(sample_rate)
    return times.tolist(), rms.astype(np.float32).tolist()