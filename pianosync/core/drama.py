"""Solo-performance analysis for the "Solo Cinema" editor.

The comparison studio aligns several performances; this module works with a
single performance and looks for the moments that deserve visual emphasis:
strong note attacks, loud arrivals and the overall climax of the piece.

The analysis is deliberately explainable and local (NumPy/SciPy only): an RMS
energy curve plus the existing onset envelope.  No AI service is required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import numpy as np

from ..audio.confidence import onset_peak_times
from ..audio.extract import load_wav
from ..audio.features import FeatureSet


@dataclass
class ImpactMoment:
    """One visually interesting point in the performance."""

    time: float
    strength: float  # 0..1, relative to the strongest moment in the plan
    kind: str = "hit"  # "hit" (attack) | "climax" (energy peak)

    def to_dict(self, index: Optional[int] = None) -> dict:
        data = {
            "time": round(float(self.time), 3),
            "strength": round(float(self.strength), 3),
            "kind": self.kind,
        }
        if index is not None:
            data["id"] = index
        return data


@dataclass
class DramaPlan:
    """Analysis result consumed by the renderer and the timeline UI."""

    duration: float
    tempo_bpm: float
    moments: List[ImpactMoment] = field(default_factory=list)
    energy_times: np.ndarray = field(default_factory=lambda: np.array([], dtype=np.float64))
    energy: np.ndarray = field(default_factory=lambda: np.array([], dtype=np.float32))
    onsets: np.ndarray = field(default_factory=lambda: np.array([], dtype=np.float64))
    phrase_times: List[float] = field(default_factory=list)
    mean_energy: float = 0.0
    peak_energy: float = 0.0
    beat_seconds: float = 0.5

    def to_dict(self) -> dict:
        return {
            "duration": round(float(self.duration), 3),
            "tempo_bpm": round(float(self.tempo_bpm), 1),
            "beat_seconds": round(float(self.beat_seconds), 3),
            "mean_energy": round(float(self.mean_energy), 4),
            "peak_energy": round(float(self.peak_energy), 4),
            "moment_count": len(self.moments),
            "moments": [m.to_dict(i) for i, m in enumerate(self.moments)],
            "phrase_times": [round(float(t), 3) for t in self.phrase_times],
        }


def _smooth(values: np.ndarray, window: int) -> np.ndarray:
    """Moving average with edge padding (keeps the input length)."""
    values = np.asarray(values, dtype=np.float32)
    window = max(1, int(window))
    if window <= 1 or values.size < window:
        return values
    kernel = np.ones(window, dtype=np.float32) / float(window)
    pad_left = window // 2
    pad_right = window - 1 - pad_left
    padded = np.pad(values, (pad_left, pad_right), mode="edge")
    return np.convolve(padded, kernel, mode="valid")[: values.size].astype(np.float32)


def _downsample(
    times: np.ndarray, values: np.ndarray, target: int
) -> Tuple[np.ndarray, np.ndarray]:
    """Resample a series onto at most ``target`` uniform points for JSON/UI."""
    times = np.asarray(times, dtype=np.float64)
    values = np.asarray(values, dtype=np.float32)
    if times.size == 0:
        return times, values
    if times.size <= target:
        return times, values
    grid = np.linspace(float(times[0]), float(times[-1]), int(target))
    return grid, np.interp(grid, times, values).astype(np.float32)


def energy_envelope(
    wav_path: str, points_per_second: int = 50
) -> Tuple[np.ndarray, np.ndarray]:
    """Return ``(times, rms)`` for a WAV file, normalised to 0..1.

    The 99th percentile is used as the reference level.  A lower reference
    clipped most of a loud performance to ``1.0``, which created a plateau and
    made every loud note look like an equally strong peak.
    """
    signal, sample_rate = load_wav(wav_path)
    if signal.size == 0 or sample_rate <= 0:
        return np.array([], dtype=np.float64), np.array([], dtype=np.float32)
    window = max(1, int(sample_rate / max(1, points_per_second)))
    n_windows = max(1, signal.size // window)
    trimmed = signal[: n_windows * window]
    if trimmed.size < window * n_windows:
        trimmed = np.pad(trimmed, (0, window * n_windows - trimmed.size))
    frames = trimmed.reshape(n_windows, window).astype(np.float64)
    rms = np.sqrt(np.mean(frames * frames, axis=1))
    reference = float(np.percentile(rms, 99)) if rms.size else 0.0
    if reference < 1e-6:
        reference = float(np.max(rms)) if rms.size else 1.0
    if reference < 1e-6:
        reference = 1.0
    energy = np.clip(rms / reference, 0.0, 1.0).astype(np.float32)
    times = (np.arange(n_windows, dtype=np.float64) * window + window / 2.0) / sample_rate
    return times, energy


def _nearest_value(times: np.ndarray, values: np.ndarray, t: float) -> float:
    if times.size == 0 or values.size == 0:
        return 0.0
    index = int(np.argmin(np.abs(times - t)))
    return float(values[index])


def _trailing_mean(values: np.ndarray, window: int) -> np.ndarray:
    """Mean of the *past* ``window`` samples (causal), same length as input."""
    values = np.asarray(values, dtype=np.float32)
    window = max(1, int(window))
    if window <= 1 or values.size < 2:
        return values
    kernel = np.ones(window, dtype=np.float32) / float(window)
    padded = np.pad(values, (window - 1, 0), mode="edge")
    return np.convolve(padded, kernel, mode="valid").astype(np.float32)[: values.size]


def _drama_peaks(
    energy_times: np.ndarray,
    energy: np.ndarray,
    onset_times: np.ndarray,
    onset: np.ndarray,
    rate: float,
) -> List[Tuple[float, float, float, float]]:
    """Return ``(time, score, energy, rise)`` for the few truly big arrivals.

    A plain energy maximum fires on almost every note of a piano recording, so
    the score mixes three cues:

    * how loud the moment is right now,
    * how fast it got there (loudness rise over the previous ~0.5 s),
    * how strong the attack is compared with the rest of the performance.

    Peaks are required to be at least 2.2 s apart, which keeps the edit from
    feeling like a metronome.
    """
    if energy_times.size < 5 or energy.size < 5:
        return []
    smooth = _smooth(energy, max(3, int(round(0.08 * rate))))
    baseline = _trailing_mean(smooth, max(2, int(round(0.55 * rate))))
    rise = np.clip(smooth - baseline, 0.0, None)
    onset_at = (
        np.interp(energy_times, onset_times, onset).astype(np.float32)
        if onset_times.size and onset.size
        else np.zeros_like(smooth)
    )
    onset_reference = float(np.percentile(onset_at, 95)) if onset_at.size else 0.0
    if onset_reference < 1e-6:
        onset_reference = float(np.max(onset_at)) if onset_at.size else 1.0
    onset_relative = np.clip(onset_at / max(onset_reference, 1e-6), 0.0, 1.0)
    score = (
        0.58 * smooth
        + 0.24 * onset_relative
        + 0.18 * np.clip(rise / 0.35, 0.0, 1.0)
    ).astype(np.float32)
    score = _smooth(score, max(3, int(round(0.06 * rate))))

    distance = max(1, int(round(2.2 * rate)))
    try:
        from scipy.signal import find_peaks

        indices, _props = find_peaks(
            score,
            distance=distance,
            prominence=0.08,
            height=0.24,
        )
    except Exception:  # pragma: no cover - SciPy is a declared dependency
        indices = []
        for i in range(1, score.size - 1):
            if score[i] >= score[i - 1] and score[i] > score[i + 1] and score[i] >= 0.24:
                indices.append(i)
        indices = np.asarray(indices, dtype=int)

    return [
        (
            float(energy_times[int(i)]),
            float(score[int(i)]),
            float(smooth[int(i)]),
            float(rise[int(i)]),
        )
        for i in indices
    ]


def _select_moments(
    candidates: Sequence[ImpactMoment],
    max_moments: int,
    min_gap: float,
    duration: float,
) -> List[ImpactMoment]:
    """Greedily keep the strongest candidates with a minimum time gap."""
    usable = [
        m
        for m in candidates
        if 0.15 <= m.time <= max(0.2, duration - 0.15) and m.strength > 0.05
    ]
    usable.sort(key=lambda m: m.strength, reverse=True)
    chosen: List[ImpactMoment] = []
    for candidate in usable:
        if any(abs(candidate.time - existing.time) < min_gap for existing in chosen):
            continue
        chosen.append(candidate)
        if len(chosen) >= max_moments:
            break
    chosen.sort(key=lambda m: m.time)
    if not chosen:
        return chosen
    peak = max(m.strength for m in chosen)
    if peak > 1e-6:
        for moment in chosen:
            moment.strength = float(np.clip(moment.strength / peak, 0.0, 1.0))
    return chosen


def analyze_solo(
    features: FeatureSet,
    wav_path: str,
    max_moments: int = 8,
    max_energy_points: int = 1200,
) -> DramaPlan:
    """Analyse one performance and return a dramatic-edit plan.

    ``max_moments`` is an upper bound, not a target.  The plan only keeps
    moments that stand out from their surroundings (loud arrival, fast rise,
    strong attack) and spaces them at least two seconds apart, so a short
    performance may legitimately end up with two or three impacts.
    """
    duration = float(features.duration)
    energy_times, energy = energy_envelope(wav_path)
    if energy_times.size == 0:
        # Degenerate audio: fall back to the onset envelope for a usable curve.
        energy_times = np.asarray(features.times, dtype=np.float64)
        energy = np.asarray(features.onset, dtype=np.float32)

    raw_times = np.asarray(features.times, dtype=np.float64)
    raw_onset = np.asarray(features.onset, dtype=np.float32)
    rate = float(features.frame_rate or 50.0)
    if rate <= 0:
        rate = 50.0

    peaks = _drama_peaks(energy_times, energy, raw_times, raw_onset, rate)
    candidates: List[ImpactMoment] = []
    if peaks:
        top_score = max(score for _t, score, _e, _r in peaks)
        # Only genuinely big moments survive; this is what stops the old
        # "one impact every N seconds" behaviour.
        cutoff = top_score * 0.62
        for t, score, _level, rise in peaks:
            if score < cutoff:
                continue
            kind = "hit" if rise >= 0.22 else "climax"
            candidates.append(ImpactMoment(float(t), float(score), kind))

    # Expected density: about one impact per ~5.5 s, never more than the user
    # asked for.  A 17 s clip therefore gets 3–4 moments instead of 12.
    density_limit = max(1, int(np.ceil(duration / 5.5)))
    limit = min(max(1, int(max_moments)), density_limit)

    moments = _select_moments(
        candidates,
        max_moments=limit,
        min_gap=2.0,
        duration=duration,
    )

    tempo = float(features.tempo_bpm or 0.0)
    beat_seconds = 60.0 / tempo if tempo > 0 else 0.5
    phrase_seconds = beat_seconds * 4.0
    phrase_times: List[float] = []
    if phrase_seconds > 0.2 and duration > phrase_seconds:
        t = phrase_seconds
        while t < duration - 0.05:
            phrase_times.append(round(float(t), 3))
            t += phrase_seconds

    display_times, display_energy = _downsample(energy_times, energy, max_energy_points)
    onsets = onset_peak_times(features, min_separation=0.08, threshold_ratio=0.22)
    if onsets.size > 600:
        step = int(np.ceil(onsets.size / 600.0))
        onsets = onsets[::step]

    return DramaPlan(
        duration=duration,
        tempo_bpm=tempo,
        moments=moments,
        energy_times=display_times,
        energy=display_energy,
        onsets=onsets.astype(np.float64),
        phrase_times=phrase_times,
        mean_energy=float(np.mean(energy)) if energy.size else 0.0,
        peak_energy=float(np.max(energy)) if energy.size else 0.0,
        beat_seconds=float(beat_seconds),
    )
