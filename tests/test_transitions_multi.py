"""Multi-video (up to five performances) transition generation tests."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from pianosync.config import AlignmentConfig, TransitionConfig
from pianosync.core.alignment import align_featuresets
from pianosync.core.analysis import Track
from pianosync.core.transitions import generate_transitions_multi
from conftest import make_feature_set, onset_train, random_chroma


def _features(frames, seed, period=12):
    return make_feature_set(random_chroma(frames, seed=seed), onset_train(frames, period=period))


def _track(index, label, features, alignment=None):
    media = SimpleNamespace(
        duration=features.duration, path=f"t{index}.mp4", width=640, height=360, fps=30.0
    )
    return Track(
        index,
        label,
        media.path,
        f"t{index}.wav",
        media,
        features,
        alignment,
        alignment.time_map if alignment is not None else None,
    )


def _tracks():
    ref = _features(400, seed=3)
    b = _features(420, seed=3)
    c = _features(380, seed=3)
    ab = align_featuresets(ref, b, AlignmentConfig())
    ac = align_featuresets(ref, c, AlignmentConfig())
    return [
        _track(0, "A", ref),
        _track(1, "B", b, ab),
        _track(2, "C", c, ac),
    ]


def test_round_robin_cycles_all_sources():
    tracks = _tracks()
    plan = generate_transitions_multi(
        tracks, TransitionConfig(interval_seconds=1.0, mode="fixed_output")
    )
    assert len(plan.segments) >= 3
    labels = [s.source for s in plan.segments]
    assert labels[0] == "A"
    assert labels[:3] == ["A", "B", "C"]
    assert [s.source_index for s in plan.segments[:4]] == [0, 1, 2, 0]


def test_start_with_second_source():
    tracks = _tracks()
    plan = generate_transitions_multi(
        tracks, TransitionConfig(interval_seconds=1.0, mode="fixed_output", start_with="C")
    )
    assert plan.segments[0].source == "C"
    assert [s.source_index for s in plan.segments[:3]] == [2, 0, 1]


def test_c_source_time_uses_its_mapping():
    tracks = _tracks()
    plan = generate_transitions_multi(
        tracks, TransitionConfig(interval_seconds=1.0, mode="fixed_output")
    )
    c_map = tracks[2].time_map
    for seg in plan.segments:
        if seg.source_index == 2:
            assert abs(seg.src_start - c_map.b_for_a(seg.a_start)) < 1e-6
        elif seg.source_index == 0:
            assert abs(seg.src_start - seg.a_start) < 1e-6


def test_alternate_false_keeps_source():
    tracks = _tracks()
    plan = generate_transitions_multi(
        tracks, TransitionConfig(interval_seconds=1.0, mode="fixed_output", alternate=False)
    )
    assert all(s.source == "A" for s in plan.segments)
