"""Shared note geometry for the web SVG and the MP4 score overlay."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from PIL import Image, ImageDraw

from .model import ScoreDocument, ScoreNote

_STEP_INDEX = {"C": 0, "D": 1, "E": 2, "F": 3, "G": 4, "A": 5, "B": 6}
_TREBLE_TOP = 4 * 7 + 5  # F5
_BASS_TOP = 3 * 7 + 5    # A3
_LINE_GAP = 8.0


@dataclass
class LayoutNote:
    index: int
    note: ScoreNote
    x: float
    y: float
    w: float
    h: float
    staff: int
    accidental: str
    staff_top: float
    staff_bottom: float

    def box(self, sx: float = 1.0, sy: float = 1.0) -> tuple:
        return (
            self.x * sx - self.w * sx / 2 - 3,
            self.y * sy - self.h * sy / 2 - 3,
            self.x * sx + self.w * sx / 2 + 3,
            self.y * sy + self.h * sy / 2 + 3,
        )


@dataclass
class LayoutSystem:
    measure_start: int
    measure_end: int
    y: float
    notes: List[LayoutNote] = field(default_factory=list)


@dataclass
class ScoreLayout:
    width: int
    height: int
    systems: List[LayoutSystem]
    notes: List[LayoutNote]

    @classmethod
    def from_document(
        cls,
        doc: ScoreDocument,
        width: int = 1200,
        height: int = 320,
        measures_per_system: int = 4,
    ) -> "ScoreLayout":
        width = max(320, int(width))
        height = max(120, int(height))
        groups: List[List[tuple]] = []
        current_measure = None
        for index, note in enumerate(doc.notes):
            key = int(note.measure)
            if current_measure is None or key - current_measure >= max(1, measures_per_system):
                groups.append([])
                current_measure = key
            groups[-1].append((index, note))
        if not groups:
            groups.append([])

        systems: List[LayoutSystem] = []
        all_notes: List[LayoutNote] = []
        system_h = height / len(groups)
        margin_x = 54.0

        for system_index, group in enumerate(groups):
            top = system_index * system_h + 18.0
            system = LayoutSystem(
                measure_start=group[0][1].measure if group else 1,
                measure_end=group[-1][1].measure if group else 1,
                y=top,
            )
            # Notes with the same onset (chords) share a horizontal slot.
            slots: List[List[tuple]] = []
            for item in group:
                if slots and abs(float(item[1].start_sec) - float(slots[-1][0][1].start_sec)) < 0.001:
                    slots[-1].append(item)
                else:
                    slots.append([item])
            usable = max(1.0, width - 2 * margin_x)
            step = usable / max(1, len(slots))
            for slot_index, slot in enumerate(slots):
                x = margin_x + step * (slot_index + 0.5)
                for index, note in slot:
                    staff = max(1, min(2, int(note.staff)))
                    staff_top = top + (0.0 if staff == 1 else system_h * 0.52)
                    staff_bottom = staff_top + 4 * _LINE_GAP
                    pitch_index = cls._diatonic(note)
                    top_index = _TREBLE_TOP if staff == 1 else _BASS_TOP
                    if note.is_rest or pitch_index is None:
                        y = staff_top + 2 * _LINE_GAP
                    else:
                        y = staff_top + (top_index - pitch_index) * (_LINE_GAP / 2.0)
                        y = max(staff_top - 12.0, min(staff_bottom + 12.0, y))
                    item = LayoutNote(
                        index=index,
                        note=note,
                        x=x,
                        y=y,
                        w=min(16.0, max(8.0, step * 0.42)),
                        h=9.0,
                        staff=staff,
                        accidental=note.accidental,
                        staff_top=staff_top,
                        staff_bottom=staff_bottom,
                    )
                    system.notes.append(item)
                    all_notes.append(item)
            systems.append(system)

        return cls(width=width, height=height, systems=systems, notes=all_notes)

    @staticmethod
    def _diatonic(note: ScoreNote) -> Optional[int]:
        if note.is_rest or note.midi <= 0:
            return None
        # Reconstruct step/octave from MIDI (enharmonic spelling is not needed
        # for vertical placement).
        names = ["C", "C", "D", "D", "E", "F", "F", "G", "G", "A", "A", "B"]
        midi = int(note.midi)
        step = names[midi % 12]
        octave = midi // 12 - 1
        return octave * 7 + _STEP_INDEX[step]


def render_svg(layout: ScoreLayout, current_index: Optional[int] = None) -> str:
    """Return a self-contained SVG with ``data-note-index`` on every note."""
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{layout.width}" '
        f'height="{layout.height}" viewBox="0 0 {layout.width} {layout.height}" '
        f'role="img" aria-label="악보 진행">'
    ]
    for system in layout.systems:
        for staff in (1, 2):
            top = system.y + (0.0 if staff == 1 else (layout.height / max(1, len(layout.systems))) * 0.52)
            for line in range(5):
                y = top + line * _LINE_GAP
                parts.append(
                    f'<line x1="24" y1="{y:.1f}" x2="{layout.width - 24:.1f}" y2="{y:.1f}" '
                    f'stroke="currentColor" stroke-opacity="0.32" stroke-width="1"/>'
                )
    for item in layout.notes:
        active = current_index is not None and item.index == current_index
        cls = "score-note is-current" if active else "score-note"
        if item.note.is_rest:
            parts.append(
                f'<rect class="{cls}" data-note-index="{item.index}" '
                f'x="{item.x - item.w / 2:.1f}" y="{item.y - item.h / 2:.1f}" '
                f'width="{item.w:.1f}" height="{item.h:.1f}" fill="none" '
                f'stroke="currentColor" stroke-width="1.6"/>'
            )
        else:
            parts.append(
                f'<ellipse class="{cls}" data-note-index="{item.index}" '
                f'cx="{item.x:.1f}" cy="{item.y:.1f}" rx="{item.w / 2:.1f}" '
                f'ry="{item.h / 2:.1f}" transform="rotate(-18 {item.x:.1f} {item.y:.1f})" '
                f'fill="currentColor"/>'
            )
            stem_y = item.y - 26 if item.staff == 1 else item.y + 26
            parts.append(
                f'<line x1="{item.x + item.w / 2:.1f}" y1="{item.y:.1f}" '
                f'x2="{item.x + item.w / 2:.1f}" y2="{stem_y:.1f}" '
                f'stroke="currentColor" stroke-width="1.4"/>'
            )
        if item.accidental:
            text = {"#": "♯", "b": "♭", "##": "𝄪", "bb": "♭♭"}.get(
                item.accidental, item.accidental
            )
            parts.append(
                f'<text x="{item.x - item.w - 5:.1f}" y="{item.y + 4:.1f}" '
                f'font-size="14" fill="currentColor">{text}</text>'
            )
    if current_index is not None:
        current = next((item for item in layout.notes if item.index == current_index), None)
        if current is not None:
            parts.append(
                f'<line class="score-cursor" x1="{current.x:.1f}" y1="{current.staff_top - 10:.1f}" '
                f'x2="{current.x:.1f}" y2="{current.staff_bottom + 10:.1f}" '
                f'stroke="#f5c451" stroke-width="2"/>'
            )
    parts.append("</svg>")
    return "".join(parts)


def render_panel_png(
    layout: ScoreLayout,
    current_index: Optional[int],
    path: str,
    width: int,
    height: int,
) -> str:
    """Draw the same layout into a transparent RGBA PNG for FFmpeg overlay."""
    image = Image.new("RGBA", (max(1, int(width)), max(1, int(height))), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (0, 0, image.width - 1, image.height - 1),
        radius=10,
        fill=(14, 16, 20, 226),
        outline=(245, 196, 81, 150),
        width=1,
    )
    sx = image.width / float(layout.width)
    sy = image.height / float(layout.height)
    line_color = (222, 228, 236, 150)
    for system in layout.systems:
        system_h = layout.height / max(1, len(layout.systems))
        for staff in (1, 2):
            top = system.y + (0.0 if staff == 1 else system_h * 0.52)
            for line in range(5):
                y = (top + line * _LINE_GAP) * sy
                draw.line(
                    (24 * sx, y, (layout.width - 24) * sx, y),
                    fill=line_color,
                    width=1,
                )
    current = None
    for item in layout.notes:
        x, y = item.x * sx, item.y * sy
        rx, ry = max(2.0, item.w * sx / 2), max(2.0, item.h * sy / 2)
        color = (245, 196, 81, 255) if current_index == item.index else (238, 242, 248, 230)
        if item.note.is_rest:
            draw.rectangle(
                (x - rx, y - ry, x + rx, y + ry), outline=color, width=2
            )
        else:
            draw.ellipse((x - rx, y - ry, x + rx, y + ry), fill=color)
            stem_y = y - 26 * sy if item.staff == 1 else y + 26 * sy
            draw.line((x + rx, y, x + rx, stem_y), fill=color, width=2)
        if current_index == item.index:
            current = item
    if current is not None:
        x0, y0, x1, y1 = current.box(sx, sy)
        draw.rounded_rectangle(
            (x0 - 2, y0 - 2, x1 + 2, y1 + 2),
            radius=4,
            outline=(245, 196, 81, 255),
            width=2,
        )
        draw.line(
            (current.x * sx, current.staff_top * sy - 10 * sy,
             current.x * sx, current.staff_bottom * sy + 10 * sy),
            fill=(245, 196, 81, 220),
            width=2,
        )
    image.save(path, "PNG")
    return str(path)
