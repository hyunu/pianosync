"""Reference synthesis and score-to-video time mapping tests."""

from __future__ import annotations

import wave
from types import SimpleNamespace

import numpy as np
import pytest

from pianosync.core.alignment import TimeMap
from pianosync.score import alignment as score_alignment
from pianosync.score.model import ScoreDocument, ScoreNote
from pianosync.score.synth import synthesize_reference


def _feature_set(duration: float):
    n = max(2, int(round(duration * 100)))
    return SimpleNamespace(
        times=np.arange(n, dtype=np.float64) / 100.0,
        duration=float(duration),
        sample_rate=100,
        hop_length=1,
        n_fft=2048,
        chroma=np.ones((n, 12), dtype=np.float32) / np.sqrt(12),
        onset=np.zeros(n, dtype=np.float32),
    )


def _doc() -> ScoreDocument:
    notes = [
        ScoreNote(midi=60, start_beat=0.0, duration_beats=1.0,
                  start_sec=0.0, duration_sec=1.0, measure=1, staff=1),
        ScoreNote(midi=64, start_beat=1.0, duration_beats=1.0,
                  start_sec=1.0, duration_sec=1.0, measure=2, staff=1),
    ]
    return ScoreDocument(title="t", notes=notes, duration_sec=2.0, source="t.musicxml")


def _mapped_doc() -> ScoreDocument:
    notes = [
        ScoreNote(midi=60, start_beat=0.5, duration_beats=0.5,
                  start_sec=0.5, duration_sec=0.5, measure=1, staff=1),
        ScoreNote(midi=64, start_beat=1.5, duration_beats=0.5,
                  start_sec=1.5, duration_sec=0.5, measure=2, staff=1),
    ]
    return ScoreDocument(title="t", notes=notes, duration_sec=2.0, source="t.musicxml")


def _time_map(a_to_b=(0.0, 2.0, 4.0)) -> TimeMap:
    return TimeMap(
        a_times=np.array([0.0, 1.0, 2.0]),
        b_times=np.array([0.0, 2.0, 4.0]),
        a_to_b_sec=np.array(a_to_b, dtype=np.float64),
        b_to_a_sec=np.array([0.0, 1.0, 2.0], dtype=np.float64),
    )


def test_synthesize_reference_is_deterministic_and_audible(tmp_path):
    one = tmp_path / "one.wav"
    two = tmp_path / "two.wav"

    synthesize_reference(_doc(), str(one), sample_rate=22050)
    synthesize_reference(_doc(), str(two), sample_rate=22050)

    assert one.read_bytes() == two.read_bytes()
    with wave.open(str(one), "rb") as handle:
        assert handle.getframerate() == 22050
        assert handle.getnchannels() == 1
        assert handle.getnframes() / 22050 == pytest.approx(2.2, abs=0.1)
        audio = np.frombuffer(handle.readframes(handle.getnframes()), dtype=np.int16)
    assert np.abs(audio).max() > 1000


def test_map_timings_maps_score_seconds_to_video_seconds():
    from pianosync.score.alignment import map_timings

    events = map_timings(_mapped_doc(), _time_map(), video_duration=4.0)

    assert [round(e.video_time, 3) for e in events] == [1.0, 3.0]
    assert all(e.video_end >= e.video_time for e in events)
    assert all(0.0 <= e.video_time <= 4.0 for e in events)


def test_map_timings_forces_monotonic_video_times():
    from pianosync.score.alignment import map_timings

    events = map_timings(_mapped_doc(), _time_map((0.0, 2.0, 1.0)), video_duration=4.0)

    assert events[1].video_time >= events[0].video_time
    assert events[1].video_end >= events[1].video_time


def test_align_score_uses_synthesized_reference_features(monkeypatch):
    perf = _feature_set(4.0)
    captured: dict = {}

    def fake_features(samples, sample_rate, hop_length, n_fft):
        captured["ref_duration"] = len(samples) / sample_rate
        return _feature_set(4.0)

    def fake_align(ref_feat, perf_feat, cfg):
        captured["perf_duration"] = perf_feat.duration
        return SimpleNamespace(
            time_map=_time_map(),
            confidence=SimpleNamespace(mean=0.9, low_fraction=0.0, segments=[]),
            low_confidence_segments_seconds=lambda: [],
        )

    monkeypatch.setattr(score_alignment, "compute_features", fake_features)
    monkeypatch.setattr(score_alignment, "align_featuresets", fake_align)

    result = score_alignment.align_score(perf, _mapped_doc(), cfg=score_alignment.AlignmentConfig())

    assert captured["ref_duration"] == pytest.approx(2.2, abs=0.1)
    assert captured["perf_duration"] == pytest.approx(4.0)
    assert [round(e.video_time, 3) for e in result.events] == [1.0, 3.0]
    assert result.confidence == pytest.approx(0.9)
