"""Score layout / SVG / panel rendering tests."""

from __future__ import annotations

from PIL import Image

from pianosync.score.layout import ScoreLayout, render_panel_png, render_svg
from pianosync.score.model import ScoreDocument, ScoreNote


def _doc() -> ScoreDocument:
    notes = []
    for index, (midi, start, measure) in enumerate(
        [(60, 0.0, 1), (67, 1.0, 1), (72, 2.0, 2)]
    ):
        notes.append(
            ScoreNote(
                midi=midi,
                start_beat=start,
                duration_beats=1.0,
                start_sec=start,
                duration_sec=0.8,
                measure=measure,
                staff=1,
            )
        )
    return ScoreDocument(title="t", notes=notes, duration_sec=3.0, source="t.musicxml")


def test_layout_assigns_positions_for_every_note():
    layout = ScoreLayout.from_document(_doc(), width=900, height=300)

    assert len(layout.notes) == 3
    assert len(layout.systems) >= 1
    for note in layout.notes:
        assert note.x >= 0 and note.x + note.w <= layout.width
        assert 0 < note.y < layout.height
        assert note.w > 0 and note.h > 0
    # Higher pitch must sit higher on the staff (smaller y).
    assert layout.notes[2].y < layout.notes[0].y


def test_svg_marks_the_current_note():
    layout = ScoreLayout.from_document(_doc(), width=900, height=300)

    svg = render_svg(layout, current_index=1)

    assert svg.startswith("<svg")
    assert 'data-note-index="1"' in svg
    assert "is-current" in svg
    assert 'data-note-index="0"' in svg


def test_panel_png_is_rendered_at_requested_size(tmp_path):
    layout = ScoreLayout.from_document(_doc(), width=900, height=300)
    path = tmp_path / "panel.png"

    result = render_panel_png(layout, 2, str(path), 640, 200)

    assert result == str(path)
    assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    with Image.open(path) as image:
        assert image.size == (640, 200)
