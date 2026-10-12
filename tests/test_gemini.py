"""Gemini client tests with a mocked HTTP transport."""

from __future__ import annotations

import pytest

from pianosync.ai.gemini import GeminiClient, _extract_json_object
from pianosync.errors import GeminiError, GeminiUnavailableError


class FakeResponse:
    def __init__(self, status_code=200, body=None, text=""):
        self.status_code = status_code
        self._body = body
        self.text = text
        self.request = type("Req", (), {"url": "https://example.test/model"})()

    def json(self):
        if callable(self._body):
            return self._body()
        return self._body


def _ok_response(text):
    return FakeResponse(
        status_code=200,
        body={
            "candidates": [{"content": {"parts": [{"text": text}]}}],
        },
    )


def test_extract_json_tolerates_fences():
    assert _extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json_object("Here: {\"a\": 2} done") == {"a": 2}
    assert _extract_json_object("nothing here") is None


def test_verify_same_piece(monkeypatch):
    captured = {}

    def fake_post(url, params=None, json=None, timeout=None):
        captured["url"] = url
        assert json is not None
        return _ok_response('```json\n{"same": 0.95, "confidence": 0.8, "reason": "same keys"}\n```')

    monkeypatch.setattr("pianosync.ai.gemini.requests.post", fake_post)
    client = GeminiClient(api_key="test-key", model="gemini-2.0-flash")
    out = client.verify_same_piece({"duration": 10}, {"duration": 11})
    assert out["same"] == pytest.approx(0.95)
    assert out["confidence"] == pytest.approx(0.8)
    assert "model=gemini-2.0-flash" not in captured["url"] or "key=" in captured["url"]


def test_verify_clamps_bad_numbers(monkeypatch):
    def fake_post(url, params=None, json=None, timeout=None):
        return _ok_response('{"same": 99, "confidence": -3}')

    monkeypatch.setattr("pianosync.ai.gemini.requests.post", fake_post)
    out = GeminiClient(api_key="k").verify_same_piece({}, {})
    assert out["same"] == 1.0
    assert out["confidence"] == 0.0


def test_rate_limit_raises(monkeypatch):
    def fake_post(url, params=None, json=None, timeout=None):
        return FakeResponse(status_code=429, text="quota")

    monkeypatch.setattr("pianosync.ai.gemini.requests.post", fake_post)
    with pytest.raises(GeminiError, match="429"):
        GeminiClient(api_key="k").verify_same_piece({}, {})


def test_missing_key_disabled():
    client = GeminiClient(api_key="", model="x")
    assert not client.configured
    with pytest.raises(GeminiUnavailableError):
        client.verify_same_piece({}, {})


def test_locate_section_candidate_validated(monkeypatch):
    def fake_post(url, params=None, json=None, timeout=None):
        return _ok_response('{"time": 12.0}')

    monkeypatch.setattr("pianosync.ai.gemini.requests.post", fake_post)
    client = GeminiClient(api_key="k")
    assert client.locate_section_candidate("climax", {}, 30.0) == 12.0
    # out-of-range candidate must be rejected
    client_high = GeminiClient(api_key="k")
    monkeypatch.setattr(
        "pianosync.ai.gemini.GeminiClient._generate_content",
        lambda self, prompt, system_instruction=None: '{"time": 999}',
    )
    assert client_high.locate_section_candidate("x", {}, 30.0) is None


def test_network_error_wrapped(monkeypatch):
    import requests as real_requests

    def boom(url, params=None, json=None, timeout=None):
        raise real_requests.ConnectionError("offline")

    monkeypatch.setattr("pianosync.ai.gemini.requests.post", boom)
    with pytest.raises(GeminiError, match="Network"):
        GeminiClient(api_key="k").verify_same_piece({}, {})