"""Waveform + alignment + transitions pan, painted with QPainter.

Rendered as two engraved staves on paper: ink traces, hairline staff rules,
a time axis ruled at the foot, and cinnabar rehearsal marks at every cut.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..core.alignment import AlignmentResult
from ..core.transitions import TransitionPlan
from .theme import WAVE


def _c(key: str, alpha: Optional[int] = None) -> QColor:
    color = QColor(WAVE[key])
    if alpha is not None:
        color.setAlpha(alpha)
    return color


class WaveformWidget(QWidget):
    """Two-lane waveform view with mapping, confidence and transitions."""

    time_selected = Signal(float)
    anchor_requested = Signal(float, float)
    anchor_removed = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(320)
        self.setMouseTracking(False)
        self._env_a: Optional[Tuple[List[float], List[float]]] = None
        self._env_b: Optional[Tuple[List[float], List[float]]] = None
        self._alignment: Optional[AlignmentResult] = None
        self._plan: Optional[TransitionPlan] = None
        self._anchors: List[Tuple[float, float]] = []
        self._current_time = 0.0
        self._map_lines: List[Tuple[float, float]] = []

    # -- data ------------------------------------------------------------
    def set_data(
        self,
        env_a: Optional[Tuple[List[float], List[float]]],
        env_b: Optional[Tuple[List[float], List[float]]],
        alignment: Optional[AlignmentResult],
        plan: Optional[TransitionPlan],
        anchors: List[Tuple[float, float]],
    ) -> None:
        self._env_a = env_a
        self._env_b = env_b
        self._alignment = alignment
        self._plan = plan
        self._anchors = anchors or []
        self._map_lines = []
        if alignment is not None:
            try:
                import numpy as np

                n = 80
                xs = np.linspace(float(alignment.feat_a.times[0]), float(alignment.feat_a.times[-1]), n)
                ys = np.interp(xs, alignment.time_map.a_times, alignment.time_map.a_to_b_sec)
                self._map_lines = list(zip(xs.tolist(), ys.tolist()))
            except Exception:  # noqa: BLE001
                self._map_lines = []
        self.update()

    def set_current_time(self, time_sec: float) -> None:
        self._current_time = max(0.0, time_sec)
        self.update()

    # -- geometry ---------------------------------------------------------
    def _lane_bounds(self):
        width = max(1, self.width())
        height = max(1, self.height())
        x0 = 34
        x1 = width - 16
        w = max(10, x1 - x0)
        top = 12
        bottom = height - 28
        gap = 10
        usable = max(40, bottom - top - gap)
        lane_h = usable / 2.0
        lane_a = (x0, top, w, lane_h)
        lane_b = (x0, top + lane_h + gap, w, lane_h)
        return lane_a, lane_b

    def _axis_y(self) -> float:
        return max(1, self.height()) - 16

    def _draw_lane(self, painter, envelope, color, x0, y0, w, h, duration):
        if not envelope or duration <= 0 or len(envelope[1]) < 2:
            return
        xs, ys = envelope
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)

        def x_of(t):
            return x0 + (t / duration) * w

        y_center = y0 + h / 2.0
        amp = h * 0.46
        points = []
        # Downsample envelope to a bounded polyline.
        step = max(1, len(ys) // (int(w) // 3 + 1))
        for i in range(0, len(xs), step):
            t = xs[i]
            if t < 0 or t > duration:
                continue
            px = x_of(t)
            py = y_center - ys[i] * amp
            points.append((px, py))
        if not points:
            painter.restore()
            return
        poly = [(points[0][0], y_center)]
        poly += points
        poly.append((points[-1][0], y_center))
        painter.drawPolygon([QPointF(p[0], p[1]) for p in poly])
        painter.restore()

    def _draw_staff_rule(self, painter, x0, y, w, color):
        pen = QPen(color, 1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.drawLine(int(x0), int(y), int(x0 + w), int(y))

    def _draw_lane_label(self, painter, letter, x0, y0, h, duration):
        painter.save()
        font = QFont(WAVE["display_font"])
        font.setPixelSize(15)
        painter.setFont(font)
        painter.setPen(QPen(_c("text")))
        painter.drawText(int(x0) - 26, int(y0 + h / 2.0 + 5), letter)
        painter.restore()

    # -- painting ---------------------------------------------------------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), _c("paper"))

        lane_a, lane_b = self._lane_bounds()
        x0, top, w, ha = lane_a
        _, mid0, _, hb = lane_b
        axis_y = self._axis_y()
        alignment = self._alignment
        dur_a = self._time_at_end(self._env_a) if self._env_a else 0.0
        dur_b = self._time_at_end(self._env_b) if self._env_b else 0.0

        if alignment is None:
            font = QFont(WAVE["text_font"])
            font.setPixelSize(13)
            painter.setFont(font)
            painter.setPen(QPen(_c("text")))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "분석 후 정렬 결과가 여기에 표시됩니다")
            painter.end()
            return

        # Segment tint as a slim bar along the head of lane A.
        if self._plan:
            for seg in self._plan.segments:
                sa, ea = seg.a_start, seg.a_end
                color = _c("a", 46) if seg.source == "A" else _c("b", 46)
                painter.fillRect(
                    int(x0 + (sa / dur_a) * w),
                    int(top - 6),
                    max(2, int(((ea - sa) / dur_a) * w)),
                    3,
                    color,
                )

        # Staff rules + engraved traces.
        self._draw_staff_rule(painter, x0, top + ha / 2.0, w, _c("staff"))
        self._draw_staff_rule(painter, x0, mid0 + hb / 2.0, w, _c("staff"))
        self._draw_lane(painter, self._env_a, _c("a"), x0, top, w, ha, dur_a)
        self._draw_lane(painter, self._env_b, _c("b"), x0, mid0, w, hb, dur_b)
        self._draw_lane_label(painter, "A", x0, top, ha, dur_a)
        self._draw_lane_label(painter, "B", x0, mid0, hb, dur_b)

        # Low-confidence regions: a cinnabar band ruled under lane A.
        conf = alignment.confidence
        times = alignment.feat_a.times
        for (cs, ce) in conf.segments:
            if cs < times.size and ce < times.size:
                xx0 = int(x0 + (times[cs] / dur_a) * w)
                xx1 = int(x0 + (times[ce] / dur_a) * w)
                painter.fillRect(xx0, int(top + ha - 2), max(2, xx1 - xx0), 2, _c("low", 200))

        # Mapping lines between the staves: hairlines.
        pen = QPen(_c("map", 150), 1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        for (ta, tb) in self._map_lines:
            xa = int(x0 + (ta / dur_a) * w)
            xb = int(x0 + (tb / dur_b) * w)
            painter.drawLine(xa, int(top + ha), xb, int(mid0))

        # User anchors: cinnabar proof marks at both ends of a rule.
        pen = QPen(_c("anchor"), 1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(_c("anchor"))
        for (ta, tb) in self._anchors:
            xa = int(x0 + (ta / dur_a) * w)
            xb = int(x0 + (tb / dur_b) * w)
            painter.drawLine(xa, int(top + ha), xb, int(mid0))
            painter.drawRect(xa - 3, int(top + ha / 2.0) - 3, 6, 6)
            painter.drawRect(xb - 3, int(mid0 + hb / 2.0) - 3, 6, 6)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # Transition marks: a rehearsal bar on each staff at the cut.
        if self._plan:
            pen = QPen(_c("cut"), 2)
            pen.setCosmetic(True)
            painter.setPen(pen)
            for seg in self._plan.segments:
                xa = int(x0 + (seg.a_start / dur_a) * w)
                xb = int(x0 + (seg.b_start / dur_b) * w)
                painter.drawLine(xa, int(top), xa, int(top + ha))
                painter.drawLine(xb, int(mid0), xb, int(mid0 + hb))
                painter.setBrush(_c("cut"))
                painter.drawRect(xa - 2, int(top), 5, 5)
                painter.drawRect(xb - 2, int(mid0), 5, 5)
                painter.setBrush(Qt.BrushStyle.NoBrush)

        # Playhead: ink rule with a small head.
        pen = QPen(_c("playhead"), 1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        px = int(x0 + (self._current_time / dur_a) * w) if dur_a > 0 else int(x0)
        painter.drawLine(px, int(top), px, int(mid0 + hb))
        painter.setBrush(_c("playhead"))
        painter.drawPolygon([QPointF(px - 4, int(top)), QPointF(px + 4, int(top)), QPointF(px, int(top) + 6)])
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # Time axis ruled at the foot, mono figures.
        font = QFont(WAVE["mono_font"])
        font.setPixelSize(11)
        painter.setFont(font)
        painter.setPen(QPen(_c("text")))
        self._draw_staff_rule(painter, x0, axis_y, w, _c("rule"))
        step = 1.0
        while dur_a / step > 12:
            step *= (2.0 if step in (1.0, 5.0) else 2.5)
        if dur_a > 0:
            t = 0.0
            while t <= dur_a + 1e-6:
                xt = int(x0 + (t / dur_a) * w)
                painter.drawLine(xt, int(axis_y), xt, int(axis_y) + 4)
                painter.drawText(xt - 10, int(axis_y) + 16, f"{t:.0f}s")
                t += step
        painter.end()

    def _time_at_end(self, envelope):
        if not envelope:
            return 0.0
        xs, ys = envelope
        return float(xs[-1]) if len(xs) else 0.0

    # -- interaction -------------------------------------------------------
    def _time_from_pos(self, pos_x, pos_y):
        lane_a, lane_b = self._lane_bounds()
        x0, top, w, ha = lane_a
        dur_a = self._time_at_end(self._env_a)
        dur_b = self._time_at_end(self._env_b)
        if dur_a <= 0:
            return None, None
        rel = (pos_x - x0) / w
        if pos_y <= top + ha + 6:
            return max(0.0, rel * dur_a), "A"
        if pos_y >= lane_b[1] - 6:
            b_t = max(0.0, rel * dur_b)
            return b_t, "B"
        return None, None

    def mousePressEvent(self, event):
        alignment = self._alignment
        if alignment is None:
            return
        t, lane = self._time_from_pos(event.position().x(), event.position().y())
        if t is None:
            return
        if event.button() == Qt.MouseButton.RightButton:
            if self._anchors:
                nearest = min(self._anchors, key=lambda a: abs(a[0] - t) if lane == "A" else abs(a[1] - t))
                self.anchor_removed.emit(*nearest)
            return
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if lane == "A":
                a_t, b_t = t, alignment.time_map.b_for_a(t)
            else:
                b_t, a_t = t, alignment.time_map.a_for_b(t)
            self.anchor_requested.emit(float(a_t), float(b_t))
            return
        self.time_selected.emit(t if lane == "A" else alignment.time_map.a_for_b(t))
