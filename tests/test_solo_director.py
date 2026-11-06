"""Unit tests for the optional Gemini solo director (no network)."""

from __future__ import annotations

import numpy as np
import pytest

from pianosync.ai.solo_director import (
    analysis_summary,
    build_prompt,
    parse_direction,
    request_direction,
)
from pianosync.core.drama import DramaPlan, ImpactMoment
from pianosync.errors import GeminiUnavailableError


def _plan() -> DramaPlan:
    times = np.linspace(0.0, 16.8, 64)
    return DramaPlan(
        duration=16.8,
        tempo_bpm=120.0,
        moments=[
            ImpactMoment(5.0, 1.0, "hit"),
            ImpactMoment(9.0, 0.7, "climax"),
            ImpactMoment(14.0, 0.5, "hit"),
        ],
        energy_times=times,
        energy=np.linspace(0.2, 0.9, times.size).astype(np.float32),
        mean_energy=0.55,
        peak_energy=0.9,
    )


def test_summary_contains_only_numeric_analysis():
    summary = analysis_summary(_plan())
    assert summary["duration_seconds"] == pytest.approx(16.8)
    assert [m["id"] for m in summary["moments"]] == [0, 1, 2]
    assert len(summary["section_energy"]) == 3
    # No file paths, audio, or video ever leave the machine.
    assert "path" not in summary
    prompt = build_prompt(summary, note="더 웅장하게")
    assert "더 웅장하게" in prompt
    assert "cinematic" in prompt


def test_parse_direction_clamps_and_filters():
    parsed = {
        "style": "not-a-style",
        "intensity": 9,
        "camera": "false",
        "keep_moments": [1, 9, "x", 2],
        "emphasis": {"0": 3.0, "1": 0.1, "7": 1.2},
        "reason": "  큰  타건에만  힘을  준다. ",
    }
    direction = parse_direction(parsed, _plan())
    assert direction["style"] == "cinematic"
    assert direction["intensity"] == 1.0
    assert direction["camera"] is False
    assert direction["keep_moments"] == [1, 2]
    # Unknown ids are dropped; out-of-range emphasis is clamped.
    assert direction["emphasis"] == {0: 1.5, 1: 0.5}
    assert direction["reason"] == "큰 타건에만 힘을 준다."


def test_parse_direction_never_removes_every_impact():
    direction = parse_direction({"keep_moments": []}, _plan())
    assert direction["keep_moments"] == [0, 1, 2]


def test_request_direction_uses_client_and_validates():
    class FakeClient:
        configured = True
        model = "fake-model"

        def __init__(self):
            self.prompt = ""

        def _generate_json(self, prompt, attempts=2):
            self.prompt = prompt
            return {
                "style": "noir",
                "intensity": 0.8,
                "keep_moments": [0, 2],
                "reason": "대비를 살린다.",
            }

    client = FakeClient()
    direction = request_direction(_plan(), note="느와르", client=client)
    assert direction["style"] == "noir"
    assert direction["keep_moments"] == [0, 2]
    assert direction["model"] == "fake-model"
    assert "느와르" in client.prompt


def test_request_direction_without_key_is_clear():
    class Unconfigured:
        configured = False
        model = "fake"

    with pytest.raises(GeminiUnavailableError):
        request_direction(_plan(), client=Unconfigured())
