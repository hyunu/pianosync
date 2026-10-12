"""FFmpeg / FFprobe wrappers: probe media and extract audio.

All external commands are invoked with an argument list and ``shell=False`` so
that file names are never interpreted by a shell.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from fractions import Fraction
from typing import List, Optional, Sequence

import numpy as np

from ..errors import (
    AudioExtractionError,
    FFmpegNotFoundError,
    MediaError,
    NoAudioTrackError,
)

# Common install locations used as a fallback when PATH is not set up.
_COMMON_DIRS = [
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
    "/snap/bin",
]


def _locate(env_var: str, name: str) -> Optional[str]:
    override = os.environ.get(env_var)
    if override and os.path.isfile(override) and os.access(override, os.X_OK):
        return override
    found = shutil.which(name)
    if found:
        return found
    for directory in _COMMON_DIRS:
        candidate = os.path.join(directory, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def find_ffmpeg() -> str:
    path = _locate("FFMPEG_BIN", "ffmpeg")
    if not path:
        raise FFmpegNotFoundError(
            "FFmpeg was not found. Install it (e.g. `brew install ffmpeg`) "
            "or set the FFMPEG_BIN environment variable to the ffmpeg binary."
        )
    return path


def find_ffprobe() -> str:
    path = _locate("FFPROBE_BIN", "ffprobe")
    if not path:
        raise FFmpegNotFoundError(
            "FFprobe was not found. Install FFmpeg (e.g. `brew install ffmpeg`) "
            "or set the FFPROBE_BIN environment variable to the ffprobe binary."
        )
    return path


def _run(cmd: Sequence[str], timeout: int = 120) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            list(cmd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:  # pragma: no cover - defensive
        raise FFmpegNotFoundError(str(exc)) from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"Command timed out: {' '.join(cmd[:3])} ...") from exc


def _parse_rate(value: Optional[str]) -> float:
    if not value or value in ("0/0", "N/A"):
        return 0.0
    try:
        return float(Fraction(value))
    except (ZeroDivisionError, ValueError):
        return 0.0


@dataclass
class MediaInfo:
    path: str
    duration: float = 0.0
    format_name: str = ""
    size_bytes: int = 0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    video_codec: str = ""
    has_video: bool = False
    has_audio: bool = False
    audio_codec: str = ""
    sample_rate: int = 0
    channels: int = 0
    audio_duration: float = 0.0
    raw_streams: List[dict] = field(default_factory=list)

    def summary(self) -> str:
        if not self.has_video:
            return "no video stream"
        res = f"{self.width}x{self.height}" if self.width else "?"
        return f"{res} @ {self.fps:.2f}fps, {self.duration:.2f}s"


def probe_media(path: str) -> MediaInfo:
    """Return basic metadata for a media file using ffprobe."""
    if not os.path.isfile(path):
        raise MediaError(f"File not found: {path}")
    ffprobe = find_ffprobe()
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        "-i",
        path,
    ]
    result = _run(cmd, timeout=60)
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise MediaError(
            f"Could not read '{os.path.basename(path)}'. "
            f"It may be corrupt or use an unsupported codec.\n{message}"
        )
    try:
        payload = json.loads(result.stdout.decode("utf-8", "replace") or "{}")
    except json.JSONDecodeError as exc:
        raise MediaError(f"Could not parse media metadata for {path}") from exc

    fmt = payload.get("format", {}) or {}
    info = MediaInfo(
        path=path,
        format_name=fmt.get("format_name", ""),
        size_bytes=int(fmt.get("size", 0) or 0),
        raw_streams=payload.get("streams", []) or [],
    )
    try:
        info.duration = float(fmt.get("duration", 0.0) or 0.0)
    except (TypeError, ValueError):
        info.duration = 0.0

    for stream in payload.get("streams", []) or []:
        codec_type = stream.get("codec_type")
        if codec_type == "video" and not info.has_video:
            # Skip cover-art / attached picture streams.
            if stream.get("disposition", {}).get("attached_pic"):
                continue
            info.has_video = True
            info.video_codec = stream.get("codec_name", "")
            info.width = int(stream.get("width", 0) or 0)
            info.height = int(stream.get("height", 0) or 0)
            info.fps = _parse_rate(
                stream.get("avg_frame_rate") or stream.get("r_frame_rate")
            )
            if not info.duration:
                try:
                    info.duration = float(stream.get("duration", 0.0) or 0.0)
                except (TypeError, ValueError):
                    pass
        elif codec_type == "audio" and not info.has_audio:
            info.has_audio = True
            info.audio_codec = stream.get("codec_name", "")
            info.sample_rate = int(stream.get("sample_rate", 0) or 0)
            info.channels = int(stream.get("channels", 0) or 0)
            try:
                info.audio_duration = float(stream.get("duration", 0.0) or 0.0)
            except (TypeError, ValueError):
                info.audio_duration = 0.0

    if info.has_audio and not info.audio_duration:
        info.audio_duration = info.duration
    return info


def extract_audio(
    input_path: str,
    output_wav: str,
    sample_rate: int = 22050,
    start: Optional[float] = None,
    duration: Optional[float] = None,
    channels: int = 1,
) -> str:
    """Extract (optionally a slice of) the audio track to a mono PCM WAV file."""
    if not os.path.isfile(input_path):
        raise MediaError(f"File not found: {input_path}")
    ffmpeg = find_ffmpeg()
    os.makedirs(os.path.dirname(os.path.abspath(output_wav)) or ".", exist_ok=True)

    cmd: List[str] = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error"]
    if start is not None and start > 0:
        cmd += ["-ss", f"{start:.6f}"]
    cmd += ["-i", input_path]
    if duration is not None and duration > 0:
        cmd += ["-t", f"{duration:.6f}"]
    cmd += [
        "-vn",
        "-ac",
        str(channels),
        "-ar",
        str(sample_rate),
        "-acodec",
        "pcm_s16le",
        "-f",
        "wav",
        "-y",
        output_wav,
    ]
    result = _run(cmd, timeout=600)
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        if "does not contain any stream" in message or "matches no streams" in message:
            raise NoAudioTrackError(
                f"'{os.path.basename(input_path)}' has no audio track."
            )
        raise AudioExtractionError(
            f"Failed to extract audio from '{os.path.basename(input_path)}'.\n{message}"
        )
    if not os.path.isfile(output_wav) or os.path.getsize(output_wav) == 0:
        raise AudioExtractionError(
            f"Audio extraction produced no output for '{os.path.basename(input_path)}'."
        )
    return output_wav


def load_wav(path: str) -> tuple:
    """Load a PCM WAV file as a mono float32 signal in [-1, 1].

    Returns ``(samples, sample_rate)`` without loading any video data.
    """
    from scipy.io import wavfile

    sample_rate, data = wavfile.read(path)
    array = np.asarray(data)
    if array.ndim > 1:
        array = array.mean(axis=1)
    if np.issubdtype(array.dtype, np.integer):
        info = np.iinfo(array.dtype)
        scale = float(max(abs(info.min), info.max))
        array = array.astype(np.float32) / scale
    else:
        array = array.astype(np.float32)
    return np.ascontiguousarray(array, dtype=np.float32), int(sample_rate)
