"""Waveform + alignment + transitions pan, painted with QPainter."""

from __future__ import annotations

from typing import List, Optional, Tuple

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..core.alignment import AlignmentResult
from ..core.transitions import TransitionPlan

_BG = QColor(16, 18, 26)
_GRID = QColor(60, 66, 82)
_A_COLOR = QColor(72, 196, 245)
_B_COLOR = QColor(255, 178, 71)
_LOW = QColor(240, 90, 90)


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
        padding = 14
        top = padding
        mid = height / 2.0
        bottom = height - padding
        lane_a = (padding, top, width - 2 * padding, max(20, mid - top - 6))
        lane_b = (padding, mid + 6, width - 2 * padding, max(20, bottom - mid - 6))
        return lane_a, lane_b

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
        step = max(1, len(ys) // (w // 3 + 1))
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

    # -- painting ---------------------------------------------------------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), _BG)

        lane_a, lane_b = self._lane_bounds()
        x0, top, w, ha = lane_a
        _, mid0, _, hb = lane_b
        alignment = self._alignment
        dur_a = self._time_at_end(self._env_a) if self._env_a else 0.0
        dur_b = self._time_at_end(self._env_b) if self._env_b else 0.0

        if alignment is None:
            painter.setPen(QPen(QColor(150, 160, 180), 1))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "분석 후 정렬 결과가 여기에 표시됩니다")
            painter.end()
            return

        # Segment tint under lane A.
        if self._plan:
            for seg in self._plan.segments:
                sa = seg.a_start
                ea = seg.a_end
                color = QColor(72, 196, 245, 26) if seg.source == "A" else QColor(255, 178, 71, 26)
                painter.fillRect(
                    int(x0 + (sa / dur_a) * w),
                    int(top),
                    max(2, int(((ea - sa) / dur_a) * w)),
                    int(ha),
                    color,
                )

        # Lanes.
        self._draw_lane(painter, self._env_a, _A_COLOR, x0, top, w, ha, dur_a)
        self._draw_lane(painter, self._env_b, _B_COLOR, x0, mid0, w, hb, dur_b)

        painter.setPen(QPen(_GRID, 1))
        painter.drawLine(int(x0), int(top + ha), int(x0 + w), int(top + ha))
        painter.drawLine(int(x0), int(mid0), int(x0 + w), int(mid0))

        # Confidence shading on lane A.
        conf = alignment.confidence
        times = alignment.feat_a.times
        for (cs, ce) in conf.segments:
            if cs < times.size and ce < times.size:
                xx0 = int(x0 + (times[cs] / dur_a) * w)
                xx1 = int(x0 + (times[ce] / dur_a) * w)
                painter.fillRect(xx0, int(top), max(2, xx1 - xx0), int(ha), _LOW)

        # Mapping lines from A lane bottom to B lane top.
        pen = QPen(QColor(220, 220, 240, 42), 1)
        painter.setPen(pen)
        y_a_bottom = top + ha
        y_b_top = mid0
        for a_t, b_t in self._map_lines:
            xa = int(x0 + (a_t / dur_a) * w)
            xb = int(x0 + (b_t / dur_b) * w)
            painter.drawLine(xa, int(y_a_bottom) + 2, xb, int(y_b_top) - 2)

        # Anchors.
        pen = QPen(QColor(130, 255, 150, 220), 2)
        painter.setPen(pen)
        for a_t, b_t in self._anchors:
            xa = int(x0 + (a_t / dur_a) * w)
            xb = int(x0 + (b_t / dur_b) * w)
            painter.drawLine(xa, int(y_a_bottom) + 2, xb, int(y_b_top) - 2)
            painter.drawEllipse(xa - 4, int(top + ha / 2) - 4, 8, 8)
            painter.drawEllipse(xb - 4, int(mid0 + hb / 2) - 4, 8, 8)

        # Transition ticks: on A at a_start, on B at b_start.
        pen = QPen(QColor(255, 255, 255, 170), 1, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        if self._plan:
            for seg in self._plan.segments:
                xa = int(x0 + (seg.a_start / dur_a) * w)
                xb = int(x0 + (seg.b_start / dur_b) * w)
                painter.drawLine(xa, int(top), xa, int(top + ha))
                painter.drawLine(xb, int(mid0), xb, int(mid0 + hb))

        # Current-time cursor.
        pen = QPen(QColor(255, 90, 90), 2)
        painter.setPen(pen)
        xc = int(x0 + (self._current_time / dur_a) * w)
        painter.drawLine(xc, int(top - 4), xc, int(top + ha + 4))

        # Axis labels.
        painter.setPen(QPen(QColor(180, 188, 200), 1))
        major = 10 if dur_a >= 60 else 5
        if dur_a > 0:
            step = max(1.0, round((dur_a / major) / 5) * 5)
            t = 0.0
            while t <= dur_a:
                xt = int(x0 + (t / dur_a) * w)
                painter.drawLine(xt, int(top + ha + 3), xt, int(top + ha + 7))
                painter.drawText(xt - 12, int(top + ha + 22), f"{t:.0f}s")
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