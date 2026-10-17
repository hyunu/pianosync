"""Text overlay: layout, filter graph, and burn-in end-to-end.

The burn-in test renders onto a *solid black* source, so any bright pixel in the
result must have come from the caption — no visual inspection needed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Optional

import pytest

from pianosync.audio.extract import extract_audio, find_ffmpeg, probe_media
from pianosync.config import AlignmentConfig, OutputConfig, TransitionConfig
from pianosync.core.alignment import align_featuresets
from pianosync.core.transitions import generate_transitions
from pianosync.errors import PianoSyncError
from pianosync.video.render import _build_filter_complex
from pianosync.video import overlay
from conftest import make_feature_set, onset_train, random_chroma

# The pure layout/graph tests must run even without ffmpeg, so only the burn-in
# tests carry this marker.
requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not installed"
)


def _first_font(hangul: bool) -> Optional[str]:
    """Family name of an installed Hangul-capable (or Latin-only) font."""
    for face in overlay.list_fonts():
        if face.hangul is hangul:
            return face.family
    return None



# ── layout helpers ───────────────────────────────────────────────────────────


def test_normalizers_fall_back_to_safe_values():
    assert overlay.normalize_position("nope") == "bottom"
    assert overlay.normalize_size(None) == "medium"
    assert overlay.normalize_color("purple") == "white"
    assert overlay.normalize_position("top") == "top"
    assert overlay.normalize_size("large") == "large"
    assert overlay.normalize_color("black") == "black"
    assert overlay.normalize_overlay_duration("7") == "fixed"
    assert overlay.normalize_overlay_duration("5") == "5"
    assert overlay.overlay_duration_seconds("fixed") is None
    assert overlay.overlay_duration_seconds("3") == 3.0


def test_blank_text_draws_nothing(tmp_path):
    out = str(tmp_path / "blank.png")
    assert overlay.render_overlay_png("   \n  ", 320, 240, out) is None
    assert overlay.render_overlay_png("", 320, 240, out) is None


@pytest.mark.parametrize(
    "position,band",
    [("top", (0.0, 0.35)), ("center", (0.3, 0.7)), ("bottom", (0.65, 1.0))],
)
def test_text_lands_in_the_requested_band(tmp_path, position, band):
    """The rendered ink must fall inside the band the position promises."""
    PIL = pytest.importorskip("PIL")
    out = str(tmp_path / f"{position}.png")
    path = overlay.render_overlay_png(
        "자막 테스트", 400, 1000, out, position=position, size="medium"
    )
    assert path == out
    alpha = PIL.Image.open(out).convert("RGBA").getchannel("A")
    top, bottom = alpha.getbbox()[1], alpha.getbbox()[3]
    assert 0 <= top < bottom <= 1000
    assert top >= band[0] * 1000 - 1 and bottom <= band[1] * 1000 + 1


def test_smaller_size_draws_smaller_ink(tmp_path):
    PIL = pytest.importorskip("PIL")
    sizes = {}
    for size in ("small", "medium", "large"):
        path = overlay.render_overlay_png(
            "크기", 600, 600, str(tmp_path / f"{size}.png"), size=size
        )
        alpha = PIL.Image.open(path).convert("RGBA").getchannel("A")
        sizes[size] = alpha.getbbox()[3] - alpha.getbbox()[1]
    assert sizes["small"] < sizes["medium"] < sizes["large"]


def test_long_text_is_wrapped_not_clipped(tmp_path):
    PIL = pytest.importorskip("PIL")
    path = overlay.render_overlay_png(
        "아주 긴 문장이 한 줄에 다 들어가지 않으면 자동으로 줄바꿈되어야 합니다 " * 2,
        600,
        600,
        str(tmp_path / "wrap.png"),
    )
    alpha = PIL.Image.open(path).convert("RGBA").getchannel("A")
    box = alpha.getbbox()
    assert box[0] >= 0 and box[2] <= 600  # never wider than the frame
    assert box[3] - box[1] > 100  # wrapped onto several lines


# ── font selection ───────────────────────────────────────────────────────────


def test_font_menu_starts_with_automatic_and_lists_hangul_fonts():
    options = overlay.font_choices()
    assert options[0][0] == ""  # 자동 first
    values = [value for value, _label in options]
    assert len(values) == len(set(values))

    faces = overlay.list_fonts()
    assert faces, "expected at least one selectable font on this machine"
    hangul = [face for face in faces if face.hangul]
    assert hangul, "expected at least one Hangul-capable font on this machine"
    # Hangul-capable families come first, so the menu opens on the useful ones.
    assert [f.family for f in faces[: len(hangul)]] == [f.family for f in hangul]
    assert list(faces) == sorted(faces, key=lambda f: (not f.hangul, f.family.casefold()))
    assert all(value in values for value in (face.family for face in faces))

    # Latin-only faces are labelled so the choice is not a surprise.
    latin = [face for face in faces if not face.hangul]
    if latin:
        labels = dict(options)
        assert labels[faces[-1].family].endswith("· 영문")
        assert options[values.index(faces[0].family)][1] == faces[0].family


def test_internal_system_faces_are_hidden():
    faces = overlay.list_fonts()
    assert faces, "expected at least one selectable font"
    for face in faces:
        assert not face.family.startswith(".")
        assert "Fallback" not in face.family
        assert os.path.isfile(face.path)


def test_resolve_font_accepts_family_path_and_unknown_values(tmp_path):
    auto_path, auto_index = overlay.resolve_font("")
    assert auto_path and os.path.isfile(auto_path)
    assert overlay.resolve_font(None) == (auto_path, auto_index)

    faces = overlay.list_fonts()
    assert faces
    face = faces[0]
    assert overlay.resolve_font(face.family) == (face.path, face.index)
    assert overlay.resolve_font(face.path) == (face.path, None)

    # A family that is not installed falls back to the automatic default…
    assert overlay.resolve_font("No Such Family 12345")[0] == auto_path
    # …but a mistyped path is reported instead of being silently ignored.
    with pytest.raises(PianoSyncError):
        overlay.resolve_font(str(tmp_path / "missing.ttf"))


def test_caption_font_can_be_chosen(tmp_path):
    family = _first_font(True)
    if family is None:
        pytest.skip("no Hangul-capable font installed")
    PIL = pytest.importorskip("PIL")
    path = overlay.render_overlay_png(
        "글꼴 지정", 400, 200, str(tmp_path / "font.png"), font=family
    )
    alpha = PIL.Image.open(path).convert("RGBA").getchannel("A")
    assert alpha.getbbox() is not None


def test_latin_only_font_rejects_hangul_text(tmp_path):
    family = _first_font(False)
    if family is None:
        pytest.skip("no Latin-only font installed")
    with pytest.raises(PianoSyncError) as err:
        overlay.render_overlay_png("안녕", 400, 200, str(tmp_path / "bad.png"), font=family)
    assert "한글" in str(err.value)
    # The same font is perfectly fine for Latin text.
    assert overlay.render_overlay_png(
        "Hello", 400, 200, str(tmp_path / "latin.png"), font=family
    )


# ── filter graph ─────────────────────────────────────────────────────────────


class _Timing:
    """Minimal SegmentTiming stand-in (only the fields the builder reads)."""

    def __init__(self, index):
        self.input_index = index
        self.out_duration = 2.0
        self.src_start = 0.0


def test_filter_graph_overlays_only_when_requested():
    timings = [_Timing(0), _Timing(1)]
    plain = _build_filter_complex(timings, [5.0, 5.0], 320, 240, 15, "cut", 0.0)
    assert plain.endswith("[vout]")
    assert "overlay" not in plain
    assert "concat=n=2:v=1:a=0[vout]" in plain

    with_text = _build_filter_complex(
        timings, [5.0, 5.0], 320, 240, 15, "cut", 0.0, overlay_input_index=2
    )
    assert "[vbase]" in with_text
    assert "[2:v]format=rgba[ov]" in with_text
    assert "[vbase][ov]overlay=0:0:format=auto,format=yuv420p[vout]" in with_text
    # Exactly one final output label.
    assert with_text.count("[vout]") == 1

    timed = _build_filter_complex(
        timings,
        [5.0, 5.0],
        320,
        240,
        15,
        "cut",
        0.0,
        overlay_input_index=2,
        overlay_duration_seconds=5.0,
    )
    assert "enable='between(t\\,0\\,5.000000)'" in timed


# ── end-to-end burn-in ───────────────────────────────────────────────────────


def _make_black_video(path, size, fps, duration, freq):
    """Solid black video + tone, so any bright pixel is overlay ink."""
    subprocess.run(
        [
            find_ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c=black:size={size}:rate={fps}:duration={duration}",
            "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={duration}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            "-y", path,
        ],
        check=True,
    )


def _gray_frame(path, t=1.0):
    """Decode one frame as 8-bit grayscale pixels (no Pillow needed)."""
    proc = subprocess.run(
        [
            find_ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error",
            "-ss", str(t), "-i", path, "-frames:v", "1",
            "-f", "rawvideo", "-pix_fmt", "gray", "-",
        ],
        check=True,
        capture_output=True,
    )
    return proc.stdout


def _render_ab(tmp_path, output, name="out.mp4"):
    a_path = str(tmp_path / f"{name}.a.mp4")
    b_path = str(tmp_path / f"{name}.b.mp4")
    _make_black_video(a_path, "320x240", 15, 6, 440)
    _make_black_video(b_path, "320x240", 15, 6, 330)
    a_wav = str(tmp_path / f"{name}.a.wav")
    b_wav = str(tmp_path / f"{name}.b.wav")
    extract_audio(a_path, a_wav, sample_rate=22050)
    extract_audio(b_path, b_wav, sample_rate=22050)

    ta, tb = 600, 600
    fa = make_feature_set(random_chroma(ta, seed=11), onset_train(ta, period=10))
    fb = make_feature_set(random_chroma(tb, seed=11), onset_train(tb, period=10))
    alignment = align_featuresets(fa, fb, AlignmentConfig())
    cfg = TransitionConfig(interval_seconds=2.0, effect="cut")
    plan = generate_transitions(alignment, cfg)

    from pianosync.video.render import render_final_video

    return render_final_video(
        plan, alignment, probe_media(a_path), probe_media(b_path), a_wav, b_wav,
        output, cfg,
    )


def _bright_pixels(gray, width, height, y_from, y_to):
    band = gray[y_from * width : y_to * width]
    return sum(1 for value in band if value > 180)


@requires_ffmpeg
def test_caption_is_burned_into_the_video(tmp_path):
    plain = _render_ab(
        tmp_path, OutputConfig(path=str(tmp_path / "plain.mp4"), width=320, height=240, fps=15),
        name="plain",
    )
    captioned = _render_ab(
        tmp_path,
        OutputConfig(
            path=str(tmp_path / "cap.mp4"),
            width=320,
            height=240,
            fps=15,
            overlay_text="자막 TEST",
            overlay_position="bottom",
            overlay_size="large",
            # An explicitly chosen font has to reach the rasteriser too.
            overlay_font=_first_font(True) or "",
        ),
        name="cap",
    )

    plain_info = probe_media(plain.path)
    cap_info = probe_media(captioned.path)
    assert (cap_info.width, cap_info.height) == (plain_info.width, plain_info.height)
    assert abs(cap_info.duration - plain_info.duration) < 0.3

    width, height = cap_info.width, cap_info.height
    plain_gray = _gray_frame(plain.path)
    cap_gray = _gray_frame(captioned.path)
    assert len(plain_gray) == len(cap_gray) == width * height

    # The black source stays black without a caption…
    assert _bright_pixels(plain_gray, width, height, 0, height) < 40
    # …and the caption's bright outline-fringed glyphs appear along the bottom.
    assert _bright_pixels(cap_gray, width, height, int(height * 0.6), height) > 200
    # While the upper part of the frame is untouched on both.
    assert _bright_pixels(cap_gray, width, height, 0, int(height * 0.5)) < 40


@pytest.mark.parametrize(("position", "band"), [("top", (0.0, 0.4)), ("bottom", (0.6, 1.0))])
@requires_ffmpeg
def test_caption_position_is_respected_in_the_video(tmp_path, position, band):
    output = OutputConfig(
        path=str(tmp_path / f"pos_{position}.mp4"),
        width=320,
        height=240,
        fps=15,
        overlay_text="위치 테스트",
        overlay_position=position,
        overlay_size="medium",
    )
    rendered = _render_ab(tmp_path, output, name=f"pos_{position}")
    info = probe_media(rendered.path)
    gray = _gray_frame(rendered.path)
    other_band = (int(info.height * 0.5), info.height) if band[0] == 0.0 else (0, int(info.height * 0.5))
    assert _bright_pixels(gray, info.width, info.height, int(band[0] * info.height), int(band[1] * info.height)) > 100
    assert _bright_pixels(gray, info.width, info.height, other_band[0], other_band[1]) < 40


# ── web wiring ───────────────────────────────────────────────────────────────


def test_web_config_exposes_the_caption_options():
    pytest.importorskip("fastapi")
    from pianosync.web.server import CONFIG

    assert {i["value"] for i in CONFIG["overlay_position"]} == set(overlay.POSITIONS)
    assert {i["value"] for i in CONFIG["overlay_size"]} == set(overlay.SIZES)
    assert {i["value"] for i in CONFIG["overlay_color"]} == set(overlay.COLORS)
    assert CONFIG["defaults"]["overlay_text"] == ""
    assert CONFIG["defaults"]["overlay_position"] in overlay.POSITIONS
    assert CONFIG["defaults"]["overlay_font"] == ""
    assert CONFIG["defaults"]["overlay_duration"] == "fixed"
    assert {i["value"] for i in CONFIG["overlay_duration"]} == set(overlay.OVERLAY_DURATIONS)


def test_web_config_includes_automatic_preview_family():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from pianosync.web.server import create_app

    client = TestClient(create_app())
    defaults = client.get("/api/config").json()["defaults"]
    assert "overlay_font_preview_family" in defaults
    if overlay.find_font_path():
        assert defaults["overlay_font_preview_family"]


def test_overlay_font_endpoint_rejects_paths_outside_font_dirs(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from pianosync.web.server import create_app

    secret = tmp_path / "secret.ttf"
    secret.write_bytes(b"not a font")
    client = TestClient(create_app())
    res = client.get("/api/overlay-font", params={"path": str(secret)})
    assert res.status_code == 400


def test_overlay_font_endpoint_serves_known_font():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from pianosync.web.server import create_app

    faces = overlay.list_fonts()
    if not faces:
        pytest.skip("no fonts on this machine")
    client = TestClient(create_app())
    res = client.get("/api/overlay-font", params={"path": faces[0].path})
    assert res.status_code == 200
    assert res.content[:4] in (b"\x00\x01\x00\x00", b"OTTO", b"ttcf")


def test_web_config_lists_fonts_plus_a_custom_option():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from pianosync.web.server import FONT_CUSTOM, create_app

    client = TestClient(create_app())
    fonts = client.get("/api/config").json()["overlay_fonts"]
    values = [option["value"] for option in fonts]

    assert values[0] == ""
    assert values[-1] == FONT_CUSTOM
    assert len(values) == len(set(values))
    # Every scanned family is offered, so the menu matches what the renderer can use.
    assert {face.family for face in overlay.list_fonts()} <= set(values)


def test_workspace_applies_caption_settings(tmp_path):
    from pianosync.web.workspace import Workspace

    ws = Workspace(cache_dir=str(tmp_path / "cache"), output_dir=str(tmp_path / "out"))
    assert ws.output.overlay_text == ""  # off by default

    ws._apply_output_settings(
        {
            "overlay_text": "  2026 봄 연주회  ",
            "overlay_position": "top",
            "overlay_size": "large",
            "overlay_color": "black",
            "overlay_font": "  AppleGothic  ",
            "overlay_duration": "10",
        }
    )
    assert ws.output.overlay_text == "2026 봄 연주회"
    assert ws.output.overlay_duration == "10"
    assert ws.output.overlay_position == "top"
    assert ws.output.overlay_size == "large"
    assert ws.output.overlay_color == "black"
    assert ws.output.overlay_font == "AppleGothic"

    # A font file path is kept as-is; "" means the automatic default again.
    ws._apply_output_settings({"overlay_font": "/tmp/MyFont.ttf"})
    assert ws.output.overlay_font == "/tmp/MyFont.ttf"
    ws._apply_output_settings({"overlay_font": ""})
    assert ws.output.overlay_font == ""

    # Unknown values degrade to the defaults instead of breaking the render.
    ws._apply_output_settings({"overlay_position": "diagonal", "overlay_size": "huge"})
    assert ws.output.overlay_position == "bottom"
    assert ws.output.overlay_size == "medium"

    # An empty field turns the caption back off.
    ws._apply_output_settings({"overlay_text": ""})
    assert ws.output.overlay_text == ""
