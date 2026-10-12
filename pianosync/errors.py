"""Shared exception types with user-friendly messages."""

from __future__ import annotations


class PianoSyncError(Exception):
    """Base class for all application errors shown to the user."""


class FFmpegNotFoundError(PianoSyncError):
    """FFmpeg / FFprobe executables could not be located."""


class MediaError(PianoSyncError):
    """A media file could not be read, probed or decoded."""


class AudioExtractionError(MediaError):
    """Audio extraction from a media file failed."""


class NoAudioTrackError(MediaError):
    """The media file does not contain an audio track."""


class AlignmentError(PianoSyncError):
    """The musical alignment could not be computed."""


class LowConfidenceError(AlignmentError):
    """Alignment completed but the result is too unreliable to trust."""


class ProjectError(PianoSyncError):
    """Reading or writing a project file failed."""


class GeminiError(PianoSyncError):
    """Any failure while talking to the Gemini API."""


class GeminiUnavailableError(GeminiError):
    """Gemini features are disabled (no API key / offline)."""


class CancelledError(PianoSyncError):
    """The user cancelled a long-running operation."""
