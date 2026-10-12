"""Quick single-frame extraction from a video for previews."""

from __future__ import annotations

import subprocess
from typing import List, Optional

from ..audio.extract import find_ffmpeg
from ..errors import MediaError


def extract_frame_png(
    path: str, time_sec: float, width: int = 360
) -> Optional[bytes]:
    """Extract one frame as PNG bytes. Returns None on failure."""
    ffmpeg = find_ffmpeg()
    cmd = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{max(time_sec, 0.0):.6f}",
        "-i",
        path,
        "-frames:v",
        "1",
        "-vf",
        f"scale={width}:-2",
        "-f",
        "image2pipe",
        "-c:v",
        "png",
        "-",
    ]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        raise MediaError(f"Could not extract a preview frame: {exc}") from exc
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout


def extract_frames(
    path: str, times: List[float], width: int = 360, workers: int = 4
) -> List[Optional[bytes]]:
    """Extract several frames (sequential; simple and reliable)."""
    return [extract_frame_png(path, t, width) for t in times]