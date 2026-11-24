"""Transparent score-panel frames composited onto a Solo Cinema render."""

from __future__ import annotations

import math
import os
from bisect import bisect_right
from dataclasses import dataclass
from typing import Optional, Sequence

from ..score.layout import ScoreLayout, render_panel_png
from ..score.model import NoteTiming


@dataclass(frozen=True)
class ScoreOverlaySpec:
    frames_dir: str
    fps: float
    x: int
    y: int
    width: int
    height: int

    @property
    def pattern(self) -> str:
        return os.path.join(self.frames_dir, "frame_%06d.png")


def panel_geometry(
    frame_width: int,
    frame_height: int,
    position: str = "bottom",
    margin: int = 12,
    scale: float = 0.9,
) -> tuple:
    """Return ``(panel_width, panel_height, x, y)`` for a frame."""
    width = max(64, int(frame_width))
    height = max(64, int(frame_height))
    if position == "top":
        panel_w = int(width * scale)
        panel_h = max(48, int(height * 0.26))
        return panel_w, panel_h, (width - panel_w) // 2, margin
    if position in ("left", "right"):
        panel_w = max(120, int(width * 0.34))
        panel_h = int(height * scale)
        x = margin if position == "left" else width - panel_w - margin
        return panel_w, panel_h, max(0, x), max(0, (height - panel_h) // 2)
    panel_w = int(width * scale)
    panel_h = max(48, int(height * 0.26))
    return panel_w, panel_h, (width - panel_w) // 2, max(0, height - panel_h - margin)


def render_overlay_frames(
    layout: ScoreLayout,
    timings: Sequence[NoteTiming],
    output_dir: str,
    fps: float = 12.0,
    width: int = 640,
    height: int = 360,
    position: str = "bottom",
    margin: int = 12,
    scale: float = 0.9,
) -> ScoreOverlaySpec:
    """Write one RGBA PNG per overlay frame; FFmpeg holds it until the next."""
    os.makedirs(output_dir, exist_ok=True)
    panel_w, panel_h, x, y = panel_geometry(width, height, position, margin, scale)
    rate = max(1.0, min(60.0, float(fps)))
    last_end = max((float(event.video_end) for event in timings), default=0.0)
    frame_count = max(1, int(math.ceil((last_end + 0.2) * rate)))
    starts = [float(event.video_time) for event in timings]

    # Frames contain only the panel: the FFmpeg overlay places them at (x, y).
    # Baking the position into the PNG as well would push the panel off-screen.
    for frame in range(frame_count):
        t = frame / rate
        index = bisect_right(starts, t) - 1
        current = timings[index].index if index >= 0 else None
        render_panel_png(
            layout,
            current,
            os.path.join(output_dir, f"frame_{frame:06d}.png"),
            panel_w,
            panel_h,
        )

    return ScoreOverlaySpec(
        frames_dir=output_dir,
        fps=rate,
        x=max(0, int(x)),
        y=max(0, int(y)),
        width=int(width),
        height=int(height),
    )


def build_score_filter(
    spec: ScoreOverlaySpec,
    input_index: int,
    intro_seconds: float = 0.0,
) -> str:
    """Overlay the score frames on ``[vout]`` and produce ``[vscored]``."""
    chain = [f"[{input_index}:v]format=rgba"]
    if intro_seconds > 0.001:
        chain.append(f"setpts=PTS+{intro_seconds:g}/TB")
    score_input = ",".join(chain) + "[score]"
    overlay = (
        f"[vout][score]overlay=x={spec.x}:y={spec.y}:format=auto:"
        "repeatlast=1:shortest=0"
    )
    if intro_seconds > 0.001:
        overlay += f":enable='gte(t,{intro_seconds:g})'"
    return score_input + ";" + overlay + ",format=yuv420p[vscored]"
