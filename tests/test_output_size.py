"""Output size handling: the source resolution, and vertical/square presets.

The default must be the *original* source resolution so a portrait phone
recording is not forced into a landscape frame, and the preset table must cover
the vertical / square formats used by YouTube Shorts and Instagram.
"""

from __future__ import annotations

import os

import pytest

from pianosync.config import (
    ORIGINAL_SIZE,
    SIZE_PRESETS,
    OutputConfig,
    normalize_size,
)
from pianosync.video.mapping import resolve_output_size


@pytest.mark.parametrize(
    "size,expected",
    [
        ("1080x1920", (1080, 1920)),  # 9:16 vertical
        ("720x1280", (720, 1280)),
        ("1920x1080", (1920, 1080)),
        ("1280x720", (1280, 720)),
        ("3840x2160", (3840, 2160)),
        ("1080x1080", (1080, 1080)),
        ("1080x1350", (1080, 1350)),
    ],
)
def test_preset_sizes(size, expected):
    assert resolve_output_size(OutputConfig(size=size), 1920, 1080) == expected


def test_original_is_the_default():
    cfg = OutputConfig()
    assert cfg.size == ORIGINAL_SIZE
    assert cfg.width == 0 and cfg.height == 0


def test_original_keeps_source_resolution_and_orientation():
    # A portrait phone recording must stay portrait, not be letterboxed.
    assert resolve_output_size(OutputConfig(), 1080, 1920) == (1080, 1920)
    assert resolve_output_size(OutputConfig(), 1920, 1080) == (1920, 1080)
    assert resolve_output_size(OutputConfig(size="original"), 720, 1280) == (720, 1280)


def test_original_evenises_odd_source_dimensions():
    # H.264 needs even dimensions.
    assert resolve_output_size(OutputConfig(), 1921, 1081) == (1920, 1080)


def test_unknown_size_falls_back_to_original():
    assert normalize_size("nope") == ORIGINAL_SIZE
    assert resolve_output_size(OutputConfig(size="nope"), 1280, 720) == (1280, 720)


def test_explicit_pixels_win_over_a_preset():
    cfg = OutputConfig(size="1080x1920", width=640, height=480)
    assert resolve_output_size(cfg, 1920, 1080) == (640, 480)


def test_partial_override_keeps_the_source_for_the_other_axis():
    # The desktop spin boxes allow leaving one axis at 0 ("원본 해상도").
    cfg = OutputConfig(width=1280, height=0)
    assert resolve_output_size(cfg, 1920, 1080) == (1280, 1080)


def test_workspace_applies_size_preset_and_clears_override(tmp_path):
    from pianosync.web.workspace import Workspace

    ws = Workspace(cache_dir=str(tmp_path / "cache"), output_dir=str(tmp_path / "out"))
    ws._apply_output_settings({"size": "1080x1920", "filename": "reel.mp4"})
    assert ws.output.size == "1080x1920"
    assert (ws.output.width, ws.output.height) == (0, 0)
    assert os.path.basename(ws.output.path) == "reel.mp4"

    # An explicit pixel request still wins…
    ws._apply_output_settings({"width": 640, "height": 480})
    assert ws.output.size == "custom"
    assert (ws.output.width, ws.output.height) == (640, 480)

    # …and picking a preset again clears it.
    ws._apply_output_settings({"size": "original"})
    assert ws.output.size == "original"
    assert (ws.output.width, ws.output.height) == (0, 0)


def test_web_config_lists_original_first_and_only_real_presets():
    pytest.importorskip("fastapi")
    from pianosync.web.server import CONFIG

    values = [item["value"] for item in CONFIG["resolution"]]
    assert values[0] == "original"
    assert "1080x1920" in values  # vertical for Shorts / Reels
    assert CONFIG["defaults"]["size"] == "original"
    # Every option the UI offers must actually be renderable.
    assert set(values) <= set(SIZE_PRESETS)
