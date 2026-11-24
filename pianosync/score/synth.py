"""Synthesis of a reference audio track used only for score alignment.

The generated sound is never heard in the output video; it simply gives the
existing chroma+onset DTW pipeline something to compare against the real
performance.
"""

from __future__ import annotations

import wave

import numpy as np

from .model import ScoreDocument

_HARMONICS = ((1, 1.0), (2, 0.32), (3, 0.12), (4, 0.05))


def synthesize_reference(doc: ScoreDocument, path: str, sample_rate: int = 22050) -> str:
    """Write a deterministic piano-ish mono WAV for ``doc``."""
    duration = max(float(doc.duration_sec), 0.1) + 0.2
    for note in doc.playable_notes:
        duration = max(duration, float(note.end_sec) + 0.2)
    total = max(1, int(round(duration * sample_rate)))
    audio = np.zeros(total, dtype=np.float64)

    for note in doc.playable_notes:
        start = int(max(0.0, float(note.start_sec)) * sample_rate)
        length = int(max(0.03, float(note.duration_sec)) * sample_rate)
        if start >= total:
            continue
        length = min(length, total - start)
        if length <= 0:
            continue
        t = np.arange(length, dtype=np.float64) / float(sample_rate)
        frequency = 440.0 * 2.0 ** ((int(note.midi) - 69) / 12.0)
        tone = np.zeros(length, dtype=np.float64)
        for harmonic, amplitude in _HARMONICS:
            tone += amplitude * np.sin(2.0 * np.pi * frequency * harmonic * t)
        attack = max(1, int(length * 0.02))
        release = max(1, int(length * 0.28))
        envelope = np.minimum(t * sample_rate / attack, 1.0)
        envelope *= np.exp(-t * 1.6)
        envelope *= np.minimum((length - np.arange(length)) / release, 1.0)
        audio[start:start + length] += tone * envelope

    peak = float(np.abs(audio).max()) if audio.size else 0.0
    if peak > 1e-6:
        audio *= 0.85 / peak
    pcm = np.clip(audio, -1.0, 1.0)
    samples = (pcm * 32767.0).astype("<i2")
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(int(sample_rate))
        handle.writeframes(samples.tobytes())
    return path
