"""High-level analysis orchestration with on-disk caching.

Extraction and feature computation are cached by file identity and analysis
settings so that reopening a saved project does not repeat the expensive work.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Optional

import numpy as np

from ..audio.extract import MediaInfo, extract_audio, load_wav, probe_media
from ..audio.features import FeatureSet, compute_features
from ..config import AlignmentConfig
from ..errors import AudioExtractionError, MediaError
from .alignment import AlignmentResult, TimeMap, align_featuresets
from .progress import Progress

#: Display labels for up to eight simultaneous performances (A, B, C, …).
LABELS = "ABCDEFGH"

#: Maximum number of performances a project can combine.
MAX_TRACKS = 5


@dataclass
class PerformanceAnalysis:
    path: str
    wav_path: str
    media: MediaInfo
    features: FeatureSet


@dataclass
class Track:
    """One performance in a multi-video project.

    Index 0 is the reference ("A") that every other track is aligned against;
    :attr:`time_map` maps reference seconds to this track's own seconds and is
    ``None`` for the reference itself.
    """

    index: int
    label: str
    path: str
    wav_path: str
    media: MediaInfo
    features: FeatureSet
    alignment: Optional[AlignmentResult] = None  # vs. the reference; None for the reference
    time_map: Optional[TimeMap] = None

    @property
    def is_reference(self) -> bool:
        return self.index == 0

    def source_time(self, ref_time: float) -> float:
        """Return this track's own time for a given reference time."""
        if self.is_reference or self.time_map is None:
            return float(ref_time)
        return float(self.time_map.b_for_a(ref_time))


def _file_identity(path: str) -> str:
    try:
        stat = os.stat(path)
        raw = f"{os.path.abspath(path)}|{stat.st_size}|{int(stat.st_mtime)}".encode()
    except OSError:
        raw = os.path.abspath(path).encode()
    return hashlib.sha1(raw).hexdigest()[:16]


def _config_signature(cfg: AlignmentConfig) -> str:
    raw = json.dumps(
        {
            "sr": cfg.sample_rate,
            "hop": cfg.hop_length,
            "n_fft": cfg.n_fft,
            "v": 1,
        },
        sort_keys=True,
    ).encode()
    return hashlib.sha1(raw).hexdigest()[:8]


def save_features(features: FeatureSet, path: str) -> None:
    np.savez_compressed(
        path,
        sample_rate=features.sample_rate,
        hop_length=features.hop_length,
        n_fft=features.n_fft,
        times=features.times,
        chroma=features.chroma,
        onset=features.onset,
        duration=features.duration,
        tempo_bpm=features.tempo_bpm,
    )


def load_features(path: str) -> FeatureSet:
    data = np.load(path, allow_pickle=False)
    return FeatureSet(
        sample_rate=int(data["sample_rate"]),
        hop_length=int(data["hop_length"]),
        n_fft=int(data["n_fft"]),
        times=np.asarray(data["times"], dtype=np.float64),
        chroma=np.asarray(data["chroma"], dtype=np.float32),
        onset=np.asarray(data["onset"], dtype=np.float32),
        duration=float(data["duration"]),
        tempo_bpm=float(data["tempo_bpm"]),
    )


class AnalysisEngine:
    """Extracts audio, computes features and aligns two performances."""

    def __init__(self, cache_dir: str, cfg: Optional[AlignmentConfig] = None):
        self.cache_dir = cache_dir
        self.cfg = cfg or AlignmentConfig()
        os.makedirs(os.path.join(cache_dir, "audio"), exist_ok=True)
        os.makedirs(os.path.join(cache_dir, "features"), exist_ok=True)

    # -- cached paths -----------------------------------------------------
    def _audio_cache_path(self, path: str) -> str:
        name = f"{_file_identity(path)}_{self.cfg.sample_rate}.wav"
        return os.path.join(self.cache_dir, "audio", name)

    def _feature_cache_path(self, path: str) -> str:
        name = f"{_file_identity(path)}_{_config_signature(self.cfg)}.npz"
        return os.path.join(self.cache_dir, "features", name)

    # -- steps ------------------------------------------------------------
    def analyze_performance(
        self,
        path: str,
        progress: Optional[Progress] = None,
        use_cache: bool = True,
    ) -> PerformanceAnalysis:
        progress = progress or Progress()
        if not os.path.isfile(path):
            raise MediaError(f"Input file not found: {path}")

        progress.update(0.02, f"Reading media info: {os.path.basename(path)}")
        media = probe_media(path)
        if not media.has_audio:
            raise AudioExtractionError(
                f"'{os.path.basename(path)}' has no audio track to analyze."
            )

        wav_path = self._audio_cache_path(path)
        feat_path = self._feature_cache_path(path)

        if use_cache and os.path.isfile(feat_path):
            progress.update(0.5, f"Using cached analysis: {os.path.basename(path)}")
            features = load_features(feat_path)
            if not os.path.isfile(wav_path):
                extract_audio(path, wav_path, sample_rate=self.cfg.sample_rate)
            return PerformanceAnalysis(path, wav_path, media, features)

        if not (use_cache and os.path.isfile(wav_path)):
            progress.update(0.1, f"Extracting audio: {os.path.basename(path)}")
            extract_audio(path, wav_path, sample_rate=self.cfg.sample_rate)

        progress.update(0.35, f"Loading audio: {os.path.basename(path)}")
        samples, sample_rate = load_wav(wav_path)

        progress.update(0.5, f"Extracting musical features: {os.path.basename(path)}")
        features = compute_features(
            samples,
            sample_rate,
            hop_length=self.cfg.hop_length,
            n_fft=self.cfg.n_fft,
        )
        if features.n_frames < 2:
            raise AudioExtractionError(
                f"'{os.path.basename(path)}' is too short to analyze."
            )
        if use_cache:
            try:
                save_features(features, feat_path)
            except OSError:
                pass
        progress.update(0.75, f"Analyzed {os.path.basename(path)}")
        return PerformanceAnalysis(path, wav_path, media, features)

    def align(
        self,
        perf_a: PerformanceAnalysis,
        perf_b: PerformanceAnalysis,
        progress: Optional[Progress] = None,
    ) -> AlignmentResult:
        progress = progress or Progress()
        progress.update(0.8, "Aligning performances (DTW)…")
        result = align_featuresets(perf_a.features, perf_b.features, self.cfg)
        progress.update(1.0, "Alignment complete")
        return result

    def analyze_many(
        self,
        paths: list,
        progress: Optional[Progress] = None,
    ) -> "list[Track]":
        """Analyze two to :data:`MAX_TRACKS` performances and align each to the first.

        The first path is the reference; every other performance is mapped onto
        it with the same hierarchical DTW used by the two-video flow.
        """
        progress = progress or Progress()
        paths = list(paths)
        if len(paths) < 2:
            raise MediaError("At least two performances are required.")
        if len(paths) > MAX_TRACKS:
            raise MediaError(f"At most {MAX_TRACKS} performances are supported.")
        n = len(paths)

        perfs: list[PerformanceAnalysis] = []
        for i, path in enumerate(paths):
            def make_cb(i=i):
                def cb(fraction: float, message: str) -> None:
                    progress.update(0.02 + 0.52 * (i + fraction) / n, message)
                return cb

            perfs.append(
                self.analyze_performance(
                    path,
                    progress=Progress(callback=make_cb(), is_cancelled=progress.is_cancelled),
                )
            )

        ref = perfs[0]
        tracks: list[Track] = [
            Track(0, LABELS[0], ref.path, ref.wav_path, ref.media, ref.features, None, None)
        ]
        for i in range(1, n):
            def make_align_cb(i=i):
                def cb(fraction: float, message: str) -> None:
                    progress.update(0.56 + 0.42 * ((i - 1) + fraction) / max(1, n - 1), message)
                return cb

            alignment = self.align(
                ref,
                perfs[i],
                progress=Progress(callback=make_align_cb(), is_cancelled=progress.is_cancelled),
            )
            label = LABELS[i] if i < len(LABELS) else str(i + 1)
            tracks.append(
                Track(
                    i,
                    label,
                    perfs[i].path,
                    perfs[i].wav_path,
                    perfs[i].media,
                    perfs[i].features,
                    alignment,
                    alignment.time_map,
                )
            )
        progress.update(1.0, "Analysis complete")
        return tracks
