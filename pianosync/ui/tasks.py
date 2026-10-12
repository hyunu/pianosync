"""Background-task callables used by the UI. All take a ``progress`` kwarg."""

from __future__ import annotations

from typing import Dict, Optional

import numpy as np

from ..ai.gemini import GeminiClient
from ..audio.envelope import envelope_from_wav
from ..config import OutputConfig, TransitionConfig
from ..core.analysis import AnalysisEngine
from ..core.transitions import generate_transitions
from ..errors import GeminiError
from ..audio.extract import MediaInfo
from ..video.render import RenderResult, render_final_video


def build_feature_summary(features) -> Dict[str, object]:
    return {
        "duration_seconds": round(features.duration, 3),
        "tempo_bpm": round(features.tempo_bpm, 1),
        "mean_chroma": [round(float(v), 4) for v in features.chroma.mean(axis=0)],
        "mean_onset": round(float(np.mean(features.onset)), 4),
        "onset_rate_hz": round(float(features.frame_rate), 2),
    }


def analyze_task(
    engine: AnalysisEngine,
    path_a: str,
    path_b: str,
    tcfg: TransitionConfig,
    ai_client: Optional[GeminiClient] = None,
    progress=None,
) -> dict:
    perf_a = engine.analyze_performance(path_a, progress=progress)
    perf_b = engine.analyze_performance(path_b, progress=progress)
    result = engine.align(perf_a, perf_b, progress=progress)
    plan = generate_transitions(result, tcfg)
    payload = {
        "perf_a": perf_a,
        "perf_b": perf_b,
        "alignment": result,
        "plan": plan,
        "ai_note": None,
        "env_a": envelope_from_wav(perf_a.wav_path),
        "env_b": envelope_from_wav(perf_b.wav_path),
    }
    if ai_client and ai_client.configured:
        try:
            verdict = ai_client.verify_same_piece(
                build_feature_summary(perf_a.features),
                build_feature_summary(perf_b.features),
            )
            if verdict.get("same") is not None and verdict["same"] < 0.5:
                payload["ai_note"] = (
                    "Gemini 보조 판단: 두 녹음이 같은 곡일 가능성이 낮다고 응답했습니다 "
                    f"(same={verdict['same']:.2f}). 로컬 정렬 결과를 주의해서 확인하세요."
                )
        except GeminiError as exc:
            payload["ai_note"] = f"Gemini 보조 분석을 사용할 수 없음: {exc}"
    return payload


def ai_assist_task(
    client: GeminiClient,
    summaries: Dict[str, Dict[str, object]],
    low_conf_ranges: list,
    alignment_summary: Dict,
    progress=None,
) -> dict:
    result = {"same": None, "analysis_text": "", "section_candidate": None}
    if not client.configured:
        return result
    result["same"] = client.verify_same_piece(
        summaries.get("a", {}), summaries.get("b", {})
    )
    try:
        result["analysis_text"] = client.analyze_low_confidence(
            low_conf_ranges, alignment_summary
        )
    except GeminiError:
        result["analysis_text"] = ""
    return result


def render_task(
    plan,
    alignment,
    media_a: MediaInfo,
    media_b: MediaInfo,
    wav_a: str,
    wav_b: str,
    ocfg: OutputConfig,
    tcfg: TransitionConfig,
    progress=None,
) -> RenderResult:
    return render_final_video(
        plan,
        alignment,
        media_a,
        media_b,
        wav_a,
        wav_b,
        ocfg,
        tcfg,
        progress=progress,
    )