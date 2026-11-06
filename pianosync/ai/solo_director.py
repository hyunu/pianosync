"""Optional Gemini "director" for the Solo Cinema editor.

The director never invents timestamps and never touches the audio timeline.  It
only receives a compact summary of the *local* analysis and may choose:

* one of the existing style presets and a global intensity,
* which already-detected impacts to keep,
* a per-impact emphasis multiplier (how hard that hit should punch),
* camera/flash/glow/letterbox switches,
* an optional title/subtitle (only used when the user left them empty).

Everything is validated and clamped before it reaches the renderer, so a bad
model answer degrades to the local plan instead of breaking the render.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np

from ..core.drama import DramaPlan
from ..errors import GeminiError, GeminiUnavailableError, PianoSyncError
from ..video.solo import SOLO_STYLE_KEYS
from .gemini import GeminiClient

STYLE_KEYS = tuple(SOLO_STYLE_KEYS)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _as_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on"}
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _as_text(value: Any, limit: int) -> str:
    if value is None:
        return ""
    text = " ".join(str(value).split())
    return text[:limit]


def analysis_summary(plan: DramaPlan) -> Dict[str, Any]:
    """Numeric-only summary sent to Gemini (no paths, audio or video)."""
    energy = np.asarray(plan.energy, dtype=np.float64)
    sections: List[float] = []
    if energy.size >= 9:
        for chunk in np.array_split(energy, 3):
            sections.append(round(float(np.mean(chunk)), 3))
    return {
        "duration_seconds": round(float(plan.duration), 2),
        "tempo_bpm": round(float(plan.tempo_bpm), 1),
        "mean_energy": round(float(plan.mean_energy), 3),
        "peak_energy": round(float(plan.peak_energy), 3),
        "section_energy": sections,
        "moments": [
            {
                "id": index,
                "time": round(float(moment.time), 3),
                "strength": round(float(moment.strength), 3),
                "kind": moment.kind,
            }
            for index, moment in enumerate(plan.moments)
        ],
    }


def build_prompt(summary: Dict[str, Any], note: str = "") -> str:
    user_note = (note or "").strip()[:240]
    note_line = (
        f'\nThe user asked for this mood (follow it if reasonable): "{user_note}"\n'
        if user_note
        else ""
    )
    return (
        "You are a film director editing a single continuous solo piano "
        "performance. The timestamps below were detected locally from the "
        "audio; you must NOT invent or move them.\n"
        "Choose a direction that feels cinematic and dynamic rather than "
        "frantic. Impacts are already sparse, so keep most of them, and drop "
        "one only when two hits would feel repetitive.\n"
        f"{note_line}"
        "Rules:\n"
        "- style must be one of: " + ", ".join(STYLE_KEYS) + "\n"
        "- intensity, emphasis: numbers between 0 and 1.4\n"
        "- keep_moments: subset of the given ids, never new ids\n"
        "- emphasis maps kept ids to a punch multiplier (0.6-1.4)\n"
        "- camera = slow push-in + pan + hand-held motion; punch = impact "
        "push-in; flash = short brightness hit\n"
        "- reason: one or two short Korean sentences explaining the choice\n"
        "Respond with STRICT JSON only, no markdown:\n"
        '{"style": "cinematic", "intensity": 0.7, "camera": true, '
        '"punch": true, "flash": true, "glow": true, "letterbox": false, '
        '"keep_moments": [0, 1], "emphasis": {"0": 1.0, "1": 0.8}, '
        '"reason": ""}\n'
        f"Analysis: {summary}"
    )


def parse_direction(parsed: Dict[str, Any], plan: DramaPlan) -> Dict[str, Any]:
    """Validate a model answer against the local plan."""
    ids = list(range(len(plan.moments)))

    style = str(parsed.get("style", "")).strip()
    if style not in STYLE_KEYS:
        style = "cinematic"

    intensity = _as_float(parsed.get("intensity"), 0.65)
    intensity = _clamp(intensity if intensity is not None else 0.65, 0.0, 1.0)

    raw_keep = parsed.get("keep_moments")
    keep: List[int] = []
    if isinstance(raw_keep, list):
        for value in raw_keep:
            try:
                index = int(value)
            except (TypeError, ValueError):
                continue
            if index in ids and index not in keep:
                keep.append(index)
    # A model must not be able to delete every impact.
    if not keep:
        keep = ids

    emphasis: Dict[int, float] = {}
    raw_emphasis = parsed.get("emphasis")
    if isinstance(raw_emphasis, dict):
        for key, value in raw_emphasis.items():
            try:
                index = int(key)
            except (TypeError, ValueError):
                continue
            if index not in ids:
                continue
            number = _as_float(value)
            if number is None:
                continue
            emphasis[index] = round(_clamp(number, 0.5, 1.5), 3)

    return {
        "style": style,
        "intensity": round(intensity, 3),
        "camera": _as_bool(parsed.get("camera"), True),
        "punch": _as_bool(parsed.get("punch"), True),
        "flash": _as_bool(parsed.get("flash"), True),
        "glow": _as_bool(parsed.get("glow"), True),
        "letterbox": _as_bool(parsed.get("letterbox"), False),
        "keep_moments": keep,
        "emphasis": emphasis,
        "reason": _as_text(parsed.get("reason"), 400),
    }


def request_direction(
    plan: DramaPlan,
    note: str = "",
    client: Optional[GeminiClient] = None,
) -> Dict[str, Any]:
    """Ask Gemini for a validated direction; raises ``GeminiError`` on failure."""
    if not plan.moments:
        raise PianoSyncError("임팩트 지점이 없어 AI 연출 제안을 만들 수 없습니다.")
    gemini = client or GeminiClient()
    if not gemini.configured:
        raise GeminiUnavailableError(
            "Gemini API 키가 설정되지 않았습니다. 설정에서 키를 추가하면 AI 연출 제안을 쓸 수 있습니다."
        )
    summary = analysis_summary(plan)
    parsed = gemini._generate_json(build_prompt(summary, note), attempts=2)
    if not parsed:
        raise GeminiError("Gemini가 연출 제안을 반환하지 않았습니다. 잠시 후 다시 시도해 주세요.")
    direction = parse_direction(parsed, plan)
    direction["model"] = gemini.model
    return direction
