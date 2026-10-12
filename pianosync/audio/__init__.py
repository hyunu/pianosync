"""Audio subpackage: extraction and musical feature analysis."""

from .extract import (
    MediaInfo,
    extract_audio,
    find_ffmpeg,
    find_ffprobe,
    load_wav,
    probe_media,
)

__all__ = [
    "MediaInfo",
    "extract_audio",
    "find_ffmpeg",
    "find_ffprobe",
    "load_wav",
    "probe_media",
]
