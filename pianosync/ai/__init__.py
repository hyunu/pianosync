"""AI subpackage: optional Gemini assistant (never required for core features)."""

from .gemini import GeminiClient

__all__ = ["GeminiClient"]