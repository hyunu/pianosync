"""End-to-end tests for the Solo Cinema analysis + FFmpeg renderer."""

from __future__ import annotations

import os
import shutil

import pytest

from pianosync.audio.extract import find_ffmpeg, probe_media
from pianosync.config import AlignmentConfig, OutputConfig
from pianosync.core.analysis import AnalysisEngine
from pianosync.core.drama import analyze_solo
from pianosync.video.solo import SoloConfig, render_solo

SAMPLE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "samples", "A.MP4"))

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or not os.path.isfile(SAMPLE),
    reason="ffmpeg or sample video is not available",
)


@pytest.fixture(scope="module")
def analyzed(tmp_path_factory):
    root = tmp_path_factory.mktemp("solo_render")
    engine = AnalysisEngine(str(root / "cache"), AlignmentConfig(quality="fast"))
    perf = engine.analyze_performance(SAMPLE)
    plan = analyze_solo(perf.features, perf.wav_path, max_moments=6)
    return perf, plan, root


def _output(root, name="solo.mp4", width=320, height=568, fps=15, crf=32) -> OutputConfig:
    return OutputConfig(
        path=str(root / name),
        width=width,
        height=height,
        fps=fps,
        crf=crf,
        preset="ultrafast",
    )


def test_render_solo_without_intro(analyzed):
    perf, plan, root = analyzed
    assert plan.moments, "analysis should find at least one impact moment"

    config = SoloConfig(
        style="noir",
        intensity=0.55,
        letterbox=True,
        grain=False,
        intro_text="",
    )
    result = render_solo(
        perf.media,
        perf.wav_path,
        plan,
        _output(root, "no_intro.mp4"),
        config,
        moments=plan.moments[:2],
    )

    assert os.path.isfile(result.path)
    assert os.path.getsize(result.path) > 2000
    assert result.moments == 2
    assert result.style == "noir"
    assert result.intro_seconds == 0.0
    assert result.duration == pytest.approx(perf.media.duration, abs=0.15)
    info = probe_media(result.path)
    assert (info.width, info.height) == (320, 568)
    assert info.has_audio is True
    assert info.duration == pytest.approx(perf.media.duration, abs=0.2)


def test_render_solo_with_title_card(analyzed):
    perf, plan, root = analyzed
    config = SoloConfig(
        style="neon",
        intensity=0.8,
        intro_text="Nocturne",
        intro_subtitle="Encore · 2026",
        intro_seconds=1.5,
        normalize_audio=True,
    )
    result = render_solo(
        perf.media,
        perf.wav_path,
        plan,
        _output(root, "with_intro.mp4"),
        config,
        moments=plan.moments,
    )

    assert os.path.isfile(result.path)
    assert result.intro_seconds == 1.5
    assert result.moments == min(len(plan.moments), 12)
    assert result.duration == pytest.approx(perf.media.duration + 1.5, abs=0.15)
    info = probe_media(result.path)
    assert info.width == 320 and info.height == 568
    assert info.duration == pytest.approx(perf.media.duration + 1.5, abs=0.25)


def test_render_solo_without_audio(analyzed):
    perf, plan, root = analyzed
    config = SoloConfig(style="clean", include_audio=False, camera=False, intro_text="")
    result = render_solo(
        perf.media,
        perf.wav_path,
        plan,
        _output(root, "silent.mp4", width=256, height=256),
        config,
        moments=[],
    )
    assert result.moments == 0
    info = probe_media(result.path)
    assert (info.width, info.height) == (256, 256)
    assert info.has_audio is False
