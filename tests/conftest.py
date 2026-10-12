"""Shared test helpers for building synthetic feature sets and signals."""

from __future__ import annotations

import numpy as np

from pianosync.audio.features import FeatureSet


def make_feature_set(
    chroma: np.ndarray,
    onset: np.ndarray,
    frame_rate: float = 100.0,
    n_fft: int = 2048,
) -> FeatureSet:
    chroma = np.asarray(chroma, dtype=np.float32)
    onset = np.asarray(onset, dtype=np.float32)
    n = chroma.shape[0]
    return FeatureSet(
        sample_rate=int(frame_rate),
        hop_length=1,
        n_fft=n_fft,
        times=np.arange(n, dtype=np.float64) / frame_rate,
        chroma=chroma,
        onset=onset,
        duration=n / frame_rate,
        tempo_bpm=0.0,
    )


def random_chroma(n_frames: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    raw = rng.random((n_frames, 12)).astype(np.float32)
    # Smooth a little so neighbouring frames are musically similar.
    kernel = np.array([0.25, 0.5, 0.25], dtype=np.float32)
    smoothed = np.apply_along_axis(
        lambda row: np.convolve(row, kernel, mode="same"), 0, raw
    )
    norms = np.linalg.norm(smoothed, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (smoothed / norms).astype(np.float32)


def onset_train(n_frames: int, period: int = 10, seed: int = 0) -> np.ndarray:
    onset = np.zeros(n_frames, dtype=np.float32)
    onset[::period] = 1.0
    return onset
