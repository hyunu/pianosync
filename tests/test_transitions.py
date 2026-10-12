"""Transition-point generation tests."""

from __future__ import annotations

import numpy as np
import pytest

from pianosync.config import AlignmentConfig, TransitionConfig
from pianosync.core.alignment import align_featuresets
from pianosync.core.transitions import generate_transitions
from conftest import make_feature_set, onset_train, random_chroma


def _alignment(ta=400, tb=480, seed=7):
    chroma_a = random_chroma(ta, seed=seed)
    onset_a = onset_train(ta, period=12)
    old_axis = np.arange(ta)
    new_axis = np.linspace(0, ta - 1, tb)
    chroma_b = np.empty((tb, 12), dtype=np.float32)
    for d in range(12):
        chroma_b[:, d] = np.interp(new_axis, old_axis, chroma_a[:, d])
    norms = np.linalg.norm(chroma_b, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    chroma_b = (chroma_b / norms).astype(np.float32)
    onset_b = np.interp(new_axis, old_axis, onset_a).astype(np.float32)
    fa = make_feature_set(chroma_a, onset_a)
    fb = make_feature_set(chroma_b, onset_b)
    return align_featuresets(fa, fb, AlignmentConfig())


def test_musical_mode_alternates_and_snaps():
    result = _alignment()
    plan = generate_transitions(
        result, TransitionConfig(interval_seconds=1.0, mode="musical", snap_to_onset=True)
    )
    assert len(plan.segments) >= 2
    sources = [s.source for s in plan.segments]
    assert sources[0] == "A"
    assert all(a != b for a, b in zip(sources, sources[1:])), "sources should alternate"
    interior = plan.segments[:-1]
    for seg in interior:
        assert 0.5 < seg.a_end < 4.5
    assert any("onset_snap" in s.reason for s in plan.segments)
    assert abs(plan.total_duration - (result.feat_a.times[-1] - plan.segments[0].a_start)) < 0.05


def test_fixed_output_mode_keeps_constant_cadence():
    result = _alignment()
    interval = 1.0
    plan = generate_transitions(
        result,
        TransitionConfig(interval_seconds=interval, mode="fixed_output", snap_to_onset=True),
    )
    boundaries = [s.a_start for s in plan.segments]
    diffs = np.diff(boundaries)
    assert np.allclose(diffs, interval, atol=1e-6)
    assert all(s.reason == "interval" for s in plan.segments)


def test_start_with_b_and_b_mapping():
    result = _alignment()
    plan = generate_transitions(
        result, TransitionConfig(interval_seconds=1.0, start_with="B", mode="fixed_output")
    )
    assert plan.segments[0].source == "B"
    for seg in plan.segments:
        expected = result.time_map.b_for_a(seg.a_start)
        assert abs(seg.b_start - expected) < 1e-6


def test_interval_clamped_to_bounds():
    result = _alignment()
    plan = generate_transitions(
        result, TransitionConfig(interval_seconds=0.1, min_interval=0.5, max_interval=10, mode="fixed_output")
    )
    assert any("below the minimum" in w for w in plan.warnings)
    diffs = np.diff([s.a_start for s in plan.segments])
    assert np.all(diffs >= 0.5 - 1e-6)


def test_user_points_anchor_mapping():
    result = _alignment()
    anchor_a = result.feat_a.times[50]
    plan = generate_transitions(
        result,
        TransitionConfig(interval_seconds=1.0, mode="fixed_output", snap_tolerance=2.0),
        user_points=[(float(anchor_a), 1.234)],
    )
    matched = [s for s in plan.segments if abs(s.a_start - anchor_a) <= 2.0]
    assert matched
