"""Feature extraction sanity tests."""

from __future__ import annotations

import numpy as np
import pytest

from pianosync.audio.features import (
    compute_features,
    downsample_features,
    estimate_tempo,
    onset_strength,
    stft_magnitude,
)


def test_stft_shape():
    sr = 22050
    y = np.zeros(sr, dtype=np.float32)
    mag = stft_magnitude(y, n_fft=2048, hop_length=512)
    assert mag.shape[0] == 1025
    assert mag.shape[1] > 1


def test_onset_detects_impulses():
    sr = 22050
    hop = 512
    y = np.zeros(sr * 2, dtype=np.float32)
    for t in (0.5, 1.0):
        idx = int(t * sr)
        y[idx : idx + 64] += np.hanning(64).astype(np.float32)
    mag = stft_magnitude(y, n_fft=2048, hop_length=hop)
    flux = onset_strength(mag)
    assert flux.max() > 0
    peak_frame = int(np.argmax(flux))
    peak_time = peak_frame * hop / sr
    assert 0.7 < peak_time < 1.3


def test_chroma_tracks_pitch_class():
    sr = 22050
    duration = 2.0
    t = np.arange(int(sr * duration)) / sr
    # A4 = 440 Hz -> pitch class 9
    y = 0.5 * np.sin(2 * np.pi * 440.0 * t).astype(np.float32)
    feats = compute_features(y, sr, hop_length=512, n_fft=4096)
    dominant = int(np.argmax(feats.chroma.mean(axis=0)))
    assert dominant == 9


def test_tempo_estimate_periodic():
    frame_rate = 100.0
    onset = np.zeros(600, dtype=np.float32)
    # 120 BPM => 0.5 s period => 50 frames
    onset[::50] = 1.0
    bpm = estimate_tempo(onset, frame_rate)
    assert 110 < bpm < 130


def test_downsample_reduces_frames():
    sr = 22050
    t = np.arange(sr) / sr
    y = (0.3 * np.sin(2 * np.pi * 330 * t)).astype(np.float32)
    feats = compute_features(y, sr, hop_length=256, n_fft=1024)
    reduced = downsample_features(feats, 4)
    assert reduced.n_frames == feats.n_frames // 4
    assert reduced.chroma.shape[1] == 12
