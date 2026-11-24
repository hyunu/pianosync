"""Align score-note times to the real performance with existing DTW code."""

from __future__ import annotations

import os
import tempfile
from typing import Optional, Sequence, Tuple

from ..audio.extract import load_wav
from ..audio.features import FeatureSet, compute_features
from ..config import AlignmentConfig
from ..core.alignment import TimeMap, align_featuresets
from .model import NoteTiming, ScoreDocument, ScoreSync
from .synth import synthesize_reference


def _update(progress, value: float, message: str) -> None:
    if progress is None:
        return
    if hasattr(progress, "update"):
        progress.update(value, message)
    elif callable(progress):
        progress(value, message)


def map_timings(
    doc: ScoreDocument,
    time_map: TimeMap,
    video_duration: float,
) -> Sequence[NoteTiming]:
    """Map every score note through ``time_map`` and keep times monotonic."""
    limit = max(0.0, float(video_duration))
    events = []
    previous = 0.0
    for index, note in enumerate(doc.notes):
        raw_start = time_map.b_for_a(float(note.start_sec))
        raw_end = time_map.b_for_a(float(note.end_sec))
        start = max(previous, min(limit, max(0.0, float(raw_start))))
        end = max(start, min(limit, max(0.0, float(raw_end))))
        previous = start
        events.append(
            NoteTiming(
                index=index,
                note=note,
                score_time=float(note.start_sec),
                video_time=float(start),
                video_end=float(end),
            )
        )
    return events


def align_score(
    performance: FeatureSet,
    doc: ScoreDocument,
    cfg: Optional[AlignmentConfig] = None,
    progress=None,
) -> ScoreSync:
    """Synthesize the score, align it to ``performance`` and return timings."""
    cfg = cfg or AlignmentConfig()
    with tempfile.TemporaryDirectory(prefix="pianosync_score_") as tmp:
        wav_path = os.path.join(tmp, "reference.wav")
        _update(progress, 0.25, "악보 기준음을 만드는 중…")
        synthesize_reference(doc, wav_path, sample_rate=int(cfg.sample_rate))
        samples, sample_rate = load_wav(wav_path)
        _update(progress, 0.45, "악보 특징을 추출하는 중…")
        reference = compute_features(
            samples,
            sample_rate,
            hop_length=int(cfg.hop_length),
            n_fft=int(cfg.n_fft),
        )
    _update(progress, 0.62, "연주와 악보를 정렬하는 중…")
    result = align_featuresets(reference, performance, cfg)
    time_map = result.time_map
    events = map_timings(doc, time_map, performance.duration)
    low: list[Tuple[float, float]] = []
    for start, end in result.low_confidence_segments_seconds():
        low.append(
            (time_map.b_for_a(float(start)), time_map.b_for_a(float(end)))
        )
    confidence = float(result.confidence.mean)
    warnings = list(doc.warnings)
    if confidence < 0.5:
        warnings.append("연주와 악보의 정렬 신뢰도가 낮습니다. 결과를 확인해 주세요.")
    _update(progress, 1.0, "악보 정렬 완료")
    return ScoreSync(
        events=list(events),
        confidence=confidence,
        low_confidence=low,
        warnings=warnings,
        time_map=time_map,
    )
