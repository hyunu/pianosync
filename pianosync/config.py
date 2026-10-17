"""Global configuration defaults and environment handling.

Secrets (API keys) are never stored in project files or hard-coded here.
They are read from the environment / .env, or supplied by the user at runtime.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

try:  # pragma: no cover - dotenv is optional at import time
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Audio / DSP defaults
# ---------------------------------------------------------------------------

SAMPLE_RATE = _env_int("PIANOSYNC_SAMPLE_RATE", 22050)
HOP_LENGTH = _env_int("PIANOSYNC_HOP_LENGTH", 512)
N_FFT = 2048

# Frame rate of the extracted feature sequences (frames per second).
FEATURE_FPS = SAMPLE_RATE / HOP_LENGTH

# ---------------------------------------------------------------------------
# Gemini defaults (model id is configurable, never hard-coded in logic)
# ---------------------------------------------------------------------------

DEFAULT_GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")
GEMINI_API_BASE = os.environ.get(
    "GEMINI_API_BASE", "https://generativelanguage.googleapis.com"
)


#: In-memory override populated by :func:`set_api_key` (never persisted here —
#: callers that need persistence store it themselves, e.g. the web settings file).
_runtime_api_key: Optional[str] = None


def get_api_key() -> Optional[str]:
    """Return the active Gemini API key.

    A key set at runtime (e.g. from the web UI) takes precedence over the
    ``GEMINI_API_KEY`` / ``GOOGLE_API_KEY`` environment variables.
    """
    if _runtime_api_key:
        return _runtime_api_key
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    return key.strip() if key and key.strip() else None


def set_api_key(key: Optional[str]) -> None:
    """Override the Gemini API key for the current process (set ``None``/empty
    to fall back to the environment)."""
    global _runtime_api_key
    _runtime_api_key = key.strip() if key and key.strip() else None


# ---------------------------------------------------------------------------
# Transition effects
# ---------------------------------------------------------------------------
# "dip" effects fade the outgoing clip through a solid colour and preserve the
# output length; "xfade" effects blend two clips together (FFmpeg xfade) and
# shorten the output by the overlap.
DIP_EFFECTS = ("fadeblack", "fadewhite")
XFADE_EFFECTS = (
    "crossfade",
    "dissolve",
    "slideleft",
    "slideright",
    "circleopen",
    "fadegrays",
)
EFFECTS = ("cut",) + DIP_EFFECTS + XFADE_EFFECTS

#: dip effect -> colour the frame fades through
DIP_COLORS = {"fadeblack": "black", "fadewhite": "white"}
#: our effect name -> FFmpeg xfade transition name
XFADE_TRANSITIONS = {
    "crossfade": "fade",
    "dissolve": "dissolve",
    "slideleft": "slideleft",
    "slideright": "slideright",
    "circleopen": "circleopen",
    "fadegrays": "fadegrays",
}


def normalize_effect(effect: str) -> str:
    """Return a valid effect name, falling back to a hard cut."""
    return effect if effect in EFFECTS else "cut"


# ---------------------------------------------------------------------------
# Alignment defaults
# ---------------------------------------------------------------------------


@dataclass
class AlignmentConfig:
    """Controls the feature extraction + DTW alignment.

    quality: "fast" | "balanced" | "accurate" -> maps to target coarse frames.
    """

    sample_rate: int = SAMPLE_RATE
    hop_length: int = HOP_LENGTH
    n_fft: int = N_FFT
    quality: str = "balanced"
    target_frames: int = 1200  # coarse resolution cap for the first DTW pass
    band_ratio: float = 0.25  # Sakoe-Chiba band width relative to length
    onset_weight: float = 0.6
    chroma_weight: float = 0.4
    refine: bool = True
    free_start: bool = True
    free_end: bool = True

    def downsample_target(self) -> int:
        return {
            "fast": 800,
            "balanced": 1200,
            "accurate": 2000,
        }.get(self.quality, 1200)


@dataclass
class TransitionConfig:
    """Controls transition-point generation."""

    interval_seconds: float = 5.0
    min_interval: float = 1.0
    max_interval: float = 60.0
    start_with: str = "A"  # "A" or "B"
    alternate: bool = True
    effect: str = "cut"  # see pianosync.config.EFFECTS
    effect_duration: float = 0.35
    mode: str = "musical"  # "musical" (mode 1) | "fixed_output" (mode 2)
    snap_to_onset: bool = True
    snap_tolerance: float = 0.20
    low_confidence_threshold: float = 0.35
    audio_mode: str = "A"  # "A" | "B" | "aligned" | "single_A" | "single_B"


#: Output size presets: id -> (width, height). ``"original"`` keeps the
#: reference source resolution *and its orientation*, so a vertical phone
#: recording stays vertical. The rest cover landscape 16:9 plus the vertical /
#: square formats used by YouTube Shorts, Reels and Instagram feed posts.
ORIGINAL_SIZE = "original"
SIZE_PRESETS: Dict[str, Optional[Tuple[int, int]]] = {
    ORIGINAL_SIZE: None,          # reference source resolution
    "1920x1080": (1920, 1080),    # 16:9 landscape (1080p)
    "1280x720": (1280, 720),      # 16:9 landscape (720p)
    "3840x2160": (3840, 2160),    # 16:9 landscape (4K)
    "1080x1920": (1080, 1920),    # 9:16 vertical — Shorts / Reels / TikTok
    "720x1280": (720, 1280),      # 9:16 vertical (720p)
    "1080x1080": (1080, 1080),    # 1:1 square — Instagram feed
    "1080x1350": (1080, 1350),    # 4:5 vertical — Instagram feed
}


def normalize_size(size: Optional[str]) -> str:
    """Return a valid size preset id, falling back to the source resolution."""
    return size if size in SIZE_PRESETS else ORIGINAL_SIZE


@dataclass
class OutputConfig:
    """Controls final rendering.

    ``size`` selects a preset from :data:`SIZE_PRESETS` (``"original"`` by
    default). ``width``/``height`` are an explicit pixel override — mainly used
    by the desktop app and the API — and win when both are non-zero.

    ``overlay_*`` burn a text caption into the video (see
    :mod:`pianosync.video.overlay`). An empty ``overlay_text`` disables it.
    """

    path: str = ""
    size: str = ORIGINAL_SIZE
    width: int = 0
    height: int = 0
    overlay_text: str = ""
    overlay_position: str = "bottom"  # "top" | "center" | "bottom"
    overlay_size: str = "medium"      # "small" | "medium" | "large"
    overlay_color: str = "white"      # "white" | "black"
    #: Family name from ``pianosync.video.overlay.list_fonts`` or a font file
    #: path; empty uses the automatic system default.
    overlay_font: str = ""
    #: ``fixed`` = whole video; ``3`` / ``5`` / ``10`` = seconds from t=0.
    overlay_duration: str = "fixed"
    fps: float = 30.0
    crf: int = 20
    preset: str = "medium"
    include_audio: bool = True
    video_codec: str = "libx264"
    audio_codec: str = "aac"
    audio_bitrate: str = "192k"


@dataclass
class AppConfig:
    alignment: AlignmentConfig = field(default_factory=AlignmentConfig)
    transitions: TransitionConfig = field(default_factory=TransitionConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
