"""DTW time-warping accuracy tests on synthetic sequences."""

from __future__ import annotations

import numpy as np
import pytest

from pianosync.audio.dtw import align_feature_sets
from conftest import make_feature_set, onset_train, random_chroma


def _resample_features(chroma, onset, new_len):
    old_len = chroma.shape[0]
    old_axis = np.arange(old_len)
    new_axis = np.linspace(0, old_len - 1, new_len)
    chroma_new = np.empty((new_len, chroma.shape[1]), dtype=np.float32)
    for d in range(chroma.shape[1]):
        chroma_new[:, d] = np.interp(new_axis, old_axis, chroma[:, d])
    onset_new = np.interp(new_axis, old_axis, onset)
    norms = np.linalg.norm(chroma_new, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (chroma_new / norms).astype(np.float32), onset_new.astype(np.float32)


def test_align_recovers_constant_tempo_ratio():
    ta = 300
    tb = 360
    chroma_a = random_chroma(ta, seed=1)
    onset_a = onset_train(ta, period=12)
    chroma_b, onset_b = _resample_features(chroma_a, onset_a, tb)

    feat_a = make_feature_set(chroma_a, onset_a)
    feat_b = make_feature_set(chroma_b, onset_b)

    result = align_feature_sets(feat_a, feat_b, target_frames=1200, refine=True)
    true_map = np.arange(ta) * (tb - 1) / (ta - 1)
    errors = np.abs(result.a_to_b - true_map)
    median_error = np.median(errors[20:-20])
    assert median_error < 4.0, f"median error {median_error} frames"


def test_align_tolerates_leading_silence():
    ta = 250
    tb = 300
    lead = 40
    chroma_a = random_chroma(ta, seed=2)
    onset_a = onset_train(ta, period=10)
    chroma_core, onset_core = _resample_features(chroma_a, onset_a, tb)

    silence = np.full((lead, 12), 1.0 / np.sqrt(12), dtype=np.float32)
    chroma_b = np.vstack([silence, chroma_core])
    onset_b = np.concatenate([np.zeros(lead, dtype=np.float32), onset_core])

    feat_a = make_feature_set(chroma_a, onset_a)
    feat_b = make_feature_set(chroma_b, onset_b)

    result = align_feature_sets(feat_a, feat_b, target_frames=1200, refine=True)
    true_map = lead + np.arange(ta) * (tb - 1) / (ta - 1)
    errors = np.abs(result.a_to_b - true_map)
    median_error = np.median(errors[30:-30])
    assert median_error < 6.0, f"median error {median_error} frames"


def test_monotonic_mapping():
    chroma_a = random_chroma(200, seed=3)
    onset_a = onset_train(200)
    chroma_b, onset_b = _resample_features(chroma_a, onset_a, 220)
    feat_a = make_feature_set(chroma_a, onset_a)
    feat_b = make_feature_set(chroma_b, onset_b)
    result = align_feature_sets(feat_a, feat_b)
    diffs = np.diff(result.a_to_b)
    assert np.all(diffs >= -1e-6), "mapping must be non-decreasing"
    assert np.isfinite(result.normalized_cost)
