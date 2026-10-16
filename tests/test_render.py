"""End-to-end render test using tiny FFmpeg-generated clips."""

from __future__ import annotations

import os
import shutil
import subprocess

import numpy as np
import pytest

from pianosync.audio.extract import extract_audio, find_ffmpeg, probe_media
from pianosync.config import AlignmentConfig, OutputConfig, TransitionConfig
from pianosync.core.alignment import align_featuresets
from pianosync.core.transitions import generate_transitions
from conftest import make_feature_set, onset_train, random_chroma

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not installed"
)


def _make_video(path, size, fps, duration, freq):
    subprocess.run(
        [
            find_ffmpeg(),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size={size}:rate={fps}:duration={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={freq}:duration={duration}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            "-y",
            path,
        ],
        check=True,
    )


def _alignment(duration_frames=600):
    ta = duration_frames
    tb = duration_frames
    ca = random_chroma(ta, seed=11)
    oa = onset_train(ta, period=10)
    cb = random_chroma(tb, seed=11)
    ob = onset_train(tb, period=10)
    fa = make_feature_set(ca, oa)
    fb = make_feature_set(cb, ob)
    return align_featuresets(fa, fb, AlignmentConfig())


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_render_produces_playable_video(tmp_path):
    a_path = str(tmp_path / "a.mp4")
    b_path = str(tmp_path / "b.mp4")
    _make_video(a_path, "320x240", 15, 6, 440)
    _make_video(b_path, "300x300", 24, 6, 330)

    a_wav = str(tmp_path / "a.wav")
    b_wav = str(tmp_path / "b.wav")
    extract_audio(a_path, a_wav, sample_rate=22050)
    extract_audio(b_path, b_wav, sample_rate=22050)

    media_a = probe_media(a_path)
    media_b = probe_media(b_path)
    result = _alignment()
    plan = generate_transitions(result, TransitionConfig(interval_seconds=2.0, effect="cut"))
    output = OutputConfig(path=str(tmp_path / "out.mp4"), width=320, height=240, fps=15, crf=28)

    from pianosync.video.render import render_final_video

    rendered = render_final_video(
        plan,
        result,
        media_a,
        media_b,
        a_wav,
        b_wav,
        output,
        TransitionConfig(interval_seconds=2.0, effect="cut"),
    )
    assert os.path.isfile(rendered.path)
    out_info = probe_media(rendered.path)
    assert out_info.has_video
    assert out_info.has_audio
    assert out_info.width == 320 and out_info.height == 240
    assert abs(out_info.duration - 6.0) < 0.6


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_render_crossfade_shortens(tmp_path):
    a_path = str(tmp_path / "a.mp4")
    b_path = str(tmp_path / "b.mp4")
    _make_video(a_path, "320x240", 15, 6, 440)
    _make_video(b_path, "320x240", 15, 6, 330)
    a_wav = str(tmp_path / "a.wav")
    b_wav = str(tmp_path / "b.wav")
    extract_audio(a_path, a_wav, sample_rate=22050)
    extract_audio(b_path, b_wav, sample_rate=22050)
    media_a = probe_media(a_path)
    media_b = probe_media(b_path)
    result = _alignment()
    cfg = TransitionConfig(interval_seconds=2.0, effect="crossfade", effect_duration=0.3)
    plan = generate_transitions(result, cfg)
    output = OutputConfig(path=str(tmp_path / "out.mp4"), width=320, height=240, fps=15)

    from pianosync.video.render import render_final_video

    rendered = render_final_video(
        plan, result, media_a, media_b, a_wav, b_wav, output, cfg
    )
    out_info = probe_media(rendered.path)
    assert out_info.duration < 6.0


def _prepare_ab(tmp_path):
    a_path = str(tmp_path / "a.mp4")
    b_path = str(tmp_path / "b.mp4")
    _make_video(a_path, "320x240", 15, 6, 440)
    _make_video(b_path, "320x240", 15, 6, 330)
    a_wav = str(tmp_path / "a.wav")
    b_wav = str(tmp_path / "b.wav")
    extract_audio(a_path, a_wav, sample_rate=22050)
    extract_audio(b_path, b_wav, sample_rate=22050)
    return a_path, b_path, a_wav, b_wav


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
@pytest.mark.parametrize("effect", ["fadewhite", "fadeblack"])
def test_render_dip_effect_keeps_length(tmp_path, effect):
    a_path, b_path, a_wav, b_wav = _prepare_ab(tmp_path)
    media_a = probe_media(a_path)
    media_b = probe_media(b_path)
    result = _alignment()
    cfg = TransitionConfig(interval_seconds=2.0, effect=effect, effect_duration=0.3)
    plan = generate_transitions(result, cfg)
    output = OutputConfig(path=str(tmp_path / f"out_{effect}.mp4"), width=320, height=240, fps=15)

    from pianosync.video.render import render_final_video

    rendered = render_final_video(plan, result, media_a, media_b, a_wav, b_wav, output, cfg)
    out_info = probe_media(rendered.path)
    assert out_info.has_video and out_info.has_audio
    assert abs(out_info.duration - 6.0) < 0.6


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
@pytest.mark.parametrize("effect", ["dissolve", "slideleft", "circleopen", "fadegrays"])
def test_render_xfade_effect_shortens(tmp_path, effect):
    a_path, b_path, a_wav, b_wav = _prepare_ab(tmp_path)
    media_a = probe_media(a_path)
    media_b = probe_media(b_path)
    result = _alignment()
    cfg = TransitionConfig(interval_seconds=2.0, effect=effect, effect_duration=0.3)
    plan = generate_transitions(result, cfg)
    output = OutputConfig(path=str(tmp_path / f"out_{effect}.mp4"), width=320, height=240, fps=15)

    from pianosync.video.render import render_final_video

    rendered = render_final_video(plan, result, media_a, media_b, a_wav, b_wav, output, cfg)
    out_info = probe_media(rendered.path)
    assert out_info.has_video and out_info.has_audio
    assert out_info.duration < 6.0
