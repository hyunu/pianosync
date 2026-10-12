"""Musical feature extraction (chroma, onset strength, tempo).

Implemented with NumPy/SciPy only. The features are deliberately robust to the
characteristics of piano recordings: overlapping notes and long decay, which
make single-pitch detection unreliable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class FeatureSet:
    """Analyzed musical features for one performance."""

    sample_rate: int
    hop_length: int
    n_fft: int
    times: np.ndarray  # (T,) seconds at each frame centre
    chroma: np.ndarray  # (T, 12) float32, L2-normalised per frame
    onset: np.ndarray  # (T,) float32 spectral flux, normalised 0..1
    duration: float
    tempo_bpm: float = 0.0

    @property
    def frame_rate(self) -> float:
        return self.sample_rate / float(self.hop_length)

    @property
    def n_frames(self) -> int:
        return int(self.chroma.shape[0])

    def energy_frames_per_second(self) -> float:
        return self.frame_rate


def stft_magnitude(
    y: np.ndarray, n_fft: int = 2048, hop_length: int = 512
) -> np.ndarray:
    """Short-time Fourier transform magnitude, shape (n_bins, n_frames)."""
    if y.ndim > 1:
        y = y.mean(axis=1)
    y = np.asarray(y, dtype=np.float64)
    if y.size < n_fft:
        y = np.pad(y, (0, n_fft - y.size))
    window = np.hanning(n_fft)
    n_frames = 1 + (y.size - n_fft) // hop_length
    if n_frames < 1:
        n_frames = 1
    # Use stride tricks for an efficient, allocation-light STFT.
    frames = np.lib.stride_tricks.as_strided(
        y,
        shape=(n_frames, n_fft),
        strides=(y.strides[0] * hop_length, y.strides[0]),
        writeable=False,
    )
    windowed = frames * window
    spectrum = np.fft.rfft(windowed, n=n_fft, axis=1)
    magnitude = np.abs(spectrum).T  # (n_bins, n_frames)
    return magnitude.astype(np.float32)


def _chroma_filter(sample_rate: int, n_fft: int, fmin: float = 55.0, fmax: float = 5000.0) -> np.ndarray:
    """Build a (12, n_bins) mapping from FFT bins to pitch classes."""
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / sample_rate)
    mapping = np.zeros((12, freqs.shape[0]), dtype=np.float32)
    valid = (freqs >= fmin) & (freqs <= fmax)
    with np.errstate(divide="ignore"):
        midi = 69.0 + 12.0 * np.log2(np.where(freqs > 0, freqs, np.nan) / 440.0)
    pc = np.round(midi).astype(np.int64) % 12
    idx = np.where(valid & np.isfinite(midi))[0]
    for bin_index in idx:
        mapping[pc[bin_index], bin_index] += 1.0
    # Slight smoothing across octaves is already handled by collapsing to pc.
    return mapping


def chroma_features(
    magnitude: np.ndarray, sample_rate: int, n_fft: int
) -> np.ndarray:
    """Compute a per-frame chroma vector, shape (n_frames, 12)."""
    mapping = _chroma_filter(sample_rate, n_fft)
    # Log compression reduces the influence of loudness / mic position.
    compressed = np.log1p(magnitude * 10.0)
    chroma = mapping @ compressed  # (12, n_frames)
    chroma = chroma.T  # (n_frames, 12)
    norms = np.linalg.norm(chroma, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    chroma = chroma / norms
    return chroma.astype(np.float32)


def onset_strength(magnitude: np.ndarray) -> np.ndarray:
    """Positive spectral flux, shape (n_frames,)."""
    if magnitude.shape[1] < 2:
        return np.zeros(magnitude.shape[1], dtype=np.float32)
    log_mag = np.log1p(magnitude * 10.0)
    diff = np.diff(log_mag, axis=1)
    diff = np.maximum(diff, 0.0)
    flux = diff.sum(axis=0)
    flux = np.concatenate([[0.0], flux]).astype(np.float32)
    return flux


def _normalize01(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    if x.size == 0:
        return x
    lo = float(np.min(x))
    hi = float(np.max(x))
    if hi - lo < 1e-9:
        return np.zeros_like(x)
    return (x - lo) / (hi - lo)


def estimate_tempo(onset: np.ndarray, frame_rate: float) -> float:
    """Estimate a global tempo (BPM) from the onset envelope via autocorrelation."""
    if onset.size < 8 or frame_rate <= 0:
        return 0.0
    signal = onset - float(np.mean(onset))
    if not np.any(signal):
        return 0.0
    corr = np.correlate(signal, signal, mode="full")[signal.size - 1 :]
    min_bpm, max_bpm = 40.0, 240.0
    min_lag = max(1, int(round(frame_rate * 60.0 / max_bpm)))
    max_lag = min(corr.size - 1, int(round(frame_rate * 60.0 / min_bpm)))
    if max_lag <= min_lag:
        return 0.0
    window = corr[min_lag : max_lag + 1]
    lag = int(np.argmax(window)) + min_lag
    if lag <= 0:
        return 0.0
    return 60.0 * frame_rate / lag


def compute_features(
    y: np.ndarray,
    sample_rate: int,
    hop_length: int = 512,
    n_fft: int = 2048,
) -> FeatureSet:
    """Full feature extraction pipeline for one audio signal."""
    y = np.asarray(y, dtype=np.float32)
    duration = float(y.size) / float(sample_rate) if sample_rate else 0.0
    magnitude = stft_magnitude(y, n_fft=n_fft, hop_length=hop_length)
    chroma = chroma_features(magnitude, sample_rate, n_fft)
    onset = _normalize01(onset_strength(magnitude))
    n_frames = chroma.shape[0]
    times = (np.arange(n_frames, dtype=np.float64) * hop_length + n_fft / 2.0) / sample_rate
    tempo = estimate_tempo(onset, sample_rate / float(hop_length))
    return FeatureSet(
        sample_rate=sample_rate,
        hop_length=hop_length,
        n_fft=n_fft,
        times=times,
        chroma=chroma,
        onset=onset,
        duration=duration,
        tempo_bpm=tempo,
    )


def downsample_features(
    features: FeatureSet, factor: int
) -> FeatureSet:
    """Mean-pool a feature set along time by an integer factor."""
    factor = max(1, int(factor))
    if factor == 1:
        return features
    n = features.n_frames
    usable = (n // factor) * factor
    if usable < factor:
        return features
    chroma = features.chroma[:usable].reshape(-1, factor, 12).mean(axis=1)
    onset = features.onset[:usable].reshape(-1, factor).mean(axis=1)
    times = features.times[:usable].reshape(-1, factor).mean(axis=1)
    norms = np.linalg.norm(chroma, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    chroma = chroma / norms
    return FeatureSet(
        sample_rate=features.sample_rate,
        hop_length=features.hop_length * factor,
        n_fft=features.n_fft,
        times=times,
        chroma=chroma.astype(np.float32),
        onset=_normalize01(onset),
        duration=features.duration,
        tempo_bpm=features.tempo_bpm,
    )


def sequence_for_alignment(
    features: FeatureSet, onset_weight: float = 0.6
) -> np.ndarray:
    """Concatenate weighted chroma + onset into a single feature matrix (T, D).

    The result is z-normalised per dimension so the Euclidean distance used by
    the DTW cost is meaningful across recordings with different levels.
    """
    chroma = features.chroma
    onset = features.onset.reshape(-1, 1)
    weighted = np.concatenate(
        [chroma * np.sqrt(max(onset_weight, 1e-6)), onset * np.sqrt(max(1.0 - onset_weight, 1e-6)) * np.ones((1, 1))],
        axis=1,
    ).astype(np.float32)
    mean = weighted.mean(axis=0, keepdims=True)
    std = weighted.std(axis=0, keepdims=True)
    std[std < 1e-6] = 1.0
    return (weighted - mean) / std
