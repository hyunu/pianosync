"""Score overlay frame generation and filter wiring tests."""

from __future__ import annotations

import os

import pytest
from PIL import Image

from pianosync.score.layout import ScoreLayout
from pianosync.score.model import NoteTiming, ScoreDocument, ScoreNote
from pianosync.video.score_overlay import (
    ScoreOverlaySpec,
    build_score_filter,
    panel_geometry,
    render_overlay_frames,
)

pytestmark = pytest.mark.skipif(
    not os.path.isfile(os.path.join(os.path.dirname(__file__), "..", "samples", "A.MP4")),
    reason="sample video not available",
)


def _fixtures():
    notes = [
        ScoreNote(midi=60, start_beat=0, duration_beats=1,
                  start_sec=0, duration_sec=1, measure=1, staff=1),
        ScoreNote(midi=67, start_beat=1, duration_beats=1,
                  start_sec=1, duration_sec=1, measure=2, staff=1),
    ]
    doc = ScoreDocument(title="t", notes=notes, duration_sec=2.0, source="t.musicxml")
    layout = ScoreLayout.from_document(doc, width=900, height=300)
    timings = [
        NoteTiming(index=0, note=notes[0], score_time=0.0, video_time=0.0, video_end=1.0),
        NoteTiming(index=1, note=notes[1], score_time=1.0, video_time=1.0, video_end=2.0),
    ]
    return layout, timings


def test_render_overlay_frames_writes_transparent_pngs(tmp_path):
    layout, timings = _fixtures()

    spec = render_overlay_frames(
        layout, timings, str(tmp_path / "frames"), fps=4, width=320, height=180
    )

    files = sorted(os.listdir(spec.frames_dir))
    assert len(files) >= 8
    assert files[0].startswith("frame_")
    assert (os.path.join(spec.frames_dir, files[0])).endswith(".png")
    with open(os.path.join(spec.frames_dir, files[0]), "rb") as handle:
        assert handle.read(8) == b"\x89PNG\r\n\x1a\n"
    assert 0 <= spec.x < 320 and 0 <= spec.y < 180
    assert spec.fps == 4


def test_overlay_frames_are_panel_sized_and_positioned_by_the_filter(tmp_path):
    layout, timings = _fixtures()

    spec = render_overlay_frames(
        layout, timings, str(tmp_path / "frames"), fps=2, width=320, height=180
    )
    panel_w, panel_h, x, y = panel_geometry(320, 180, "bottom", 12, 0.9)

    # The PNG must not bake in the position: FFmpeg places it at (x, y).
    with Image.open(os.path.join(spec.frames_dir, "frame_000000.png")) as image:
        assert image.mode == "RGBA"
        assert image.size == (panel_w, panel_h)
    assert (spec.x, spec.y) == (x, y)
    assert f"x={spec.x}:y={spec.y}" in build_score_filter(spec, 1)


def test_build_score_filter_offsets_for_intro():
    spec = ScoreOverlaySpec(frames_dir="/tmp/frames", fps=12, x=10, y=20,
                            width=640, height=360)

    filt = build_score_filter(spec, input_index=2, intro_seconds=1.5)

    assert "[2:v]" in filt
    assert "setpts=PTS+1.5/TB" in filt
    assert "overlay=" in filt
    assert "enable='gte(t,1.5)'" in filt
    assert "[vscored]" in filt
