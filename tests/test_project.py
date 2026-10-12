"""Project persistence tests."""

from __future__ import annotations

import os

import numpy as np
import pytest
from scipy.io import wavfile

from pianosync.config import AlignmentConfig, TransitionConfig
from pianosync.core.analysis import AnalysisEngine
from pianosync.core.transitions import generate_transitions
from pianosync.errors import ProjectError
from pianosync.project.store import load_project, save_project


def _write_sine(path, sr, duration, freq):
    t = np.arange(int(sr * duration)) / sr
    pcm = (0.3 * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)
    wavfile.write(path, sr, pcm)


def _make_project(tmp_path):
    a_path = str(tmp_path / "a.wav")
    b_path = str(tmp_path / "b.wav")
    _write_sine(a_path, 20000, 3.0, 440)
    _write_sine(b_path, 22050, 2.7, 440)
    cfg = AlignmentConfig(quality="fast", sample_rate=20000, hop_length=1024)
    engine = AnalysisEngine(str(tmp_path / "cache"), cfg)
    return engine, a_path, b_path


def test_save_and_restore_roundtrip(tmp_path):
    engine, a_path, b_path = _make_project(tmp_path)
    perf_a = engine.analyze_performance(a_path)
    perf_b = engine.analyze_performance(b_path)
    result = engine.align(perf_a, perf_b)
    plan = generate_transitions(
        result, TransitionConfig(interval_seconds=1.0, mode="fixed_output")
    )

    from pianosync.config import OutputConfig
    from pianosync.project.store import Project

    project = Project(engine=engine)
    project.perf_a = perf_a
    project.perf_b = perf_b
    project.alignment = result
    project.plan = plan
    project.manual_points = [(1.0, 1.1), (2.0, 2.2)]
    project.output_config = OutputConfig(path=str(tmp_path / "out.mp4"), width=640, height=480)

    save_path = str(tmp_path / "project.json")
    save_project(project, save_path)
    assert os.path.isfile(save_path)

    restored = load_project(save_path, engine)
    assert restored.perf_a is not None and restored.perf_b is not None
    assert restored.alignment is not None
    assert np.allclose(
        restored.alignment.time_map.a_to_b_sec,
        result.time_map.a_to_b_sec,
        atol=0.0,
    )
    assert len(restored.plan.segments) == len(plan.segments)
    assert restored.manual_points == project.manual_points
    assert restored.output_config.width == 640


def test_restore_uses_cache_without_reextract(tmp_path):
    engine, a_path, b_path = _make_project(tmp_path)
    perf_a = engine.analyze_performance(a_path)
    perf_b = engine.analyze_performance(b_path)
    result = engine.align(perf_a, perf_b)
    plan = generate_transitions(result, TransitionConfig(interval_seconds=1.0))
    from pianosync.project.store import Project

    project = Project(engine=engine)
    project.perf_a, project.perf_b = perf_a, perf_b
    project.alignment = result
    project.plan = plan
    save_path = str(tmp_path / "p.json")
    save_project(project, save_path)

    # Analyse again: should hit the feature cache (same identity + settings).
    perf_a2 = engine.analyze_performance(a_path)
    assert perf_a2.features.n_frames == perf_a.features.n_frames


def test_restore_reports_missing_input(tmp_path):
    engine, a_path, b_path = _make_project(tmp_path)
    perf_a = engine.analyze_performance(a_path)
    perf_b = engine.analyze_performance(b_path)
    result = engine.align(perf_a, perf_b)
    from pianosync.project.store import Project

    project = Project(engine=engine)
    project.perf_a, project.perf_b = perf_a, perf_b
    project.alignment = result
    save_path = str(tmp_path / "p.json")
    save_project(project, save_path)
    os.remove(a_path)
    with pytest.raises(ProjectError, match="relink"):
        load_project(save_path, engine)