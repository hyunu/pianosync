"""Global configuration defaults and environment handling.

Secrets (API keys) are never stored in project files or hard-coded here.
They are read from the environment / .env, or supplied by the user at runtime.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

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


def get_api_key() -> Optional[str]:
    """Return the Gemini API key from the environment, if any."""
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    return key.strip() if key and key.strip() else None


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


@dataclass
class OutputConfig:
    """Controls final rendering."""

    path: str = ""
    width: int = 1920
    height: int = 1080
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
