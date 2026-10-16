"""Optional Google Gemini assistant client.

All Gemini output is treated as *auxiliary*: timestamps suggested by the model
are candidates only and must be verified against the local DSP analysis before
being used. A missing key, network failure, or quota error never breaks the
local pipeline — every method here either returns data or raises a
``GeminiError`` that the caller can present as a non-fatal message.

The model id is configurable (environment ``GEMINI_MODEL`` or the UI setting).
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, Optional

import requests

from ..config import DEFAULT_GEMINI_MODEL, GEMINI_API_BASE, get_api_key
from ..errors import GeminiError, GeminiUnavailableError


def _extract_json_object(text: str) -> Optional[dict]:
    """Extract the first JSON object from a model response (tolerates fences)."""
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    cleaned = re.sub(r"```", "", cleaned).strip()
    for candidate in (cleaned, _repair_json_literals(cleaned)):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    pattern = re.compile(r"\{.*\}", re.DOTALL)
    match = pattern.search(text)
    if not match:
        return None
    for candidate in (match.group(0), _repair_json_literals(match.group(0))):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    return None


def _repair_json_literals(text: str) -> str:
    """Escape literal control characters that models sometimes emit inside JSON
    string values (e.g. an unescaped newline in a ``reason`` field)."""
    out = []
    in_string = False
    escaped = False
    for char in text:
        if escaped:
            out.append(char)
            escaped = False
            continue
        if char == "\\":
            out.append(char)
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            out.append(char)
            continue
        if in_string and char in ("\n", "\r", "\t"):
            out.append("\\n" if char != "\t" else "\\t")
            continue
        out.append(char)
    return "".join(out)


class GeminiClient:
    """Minimal REST client for ``:generateContent`` with input validation."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        api_base: Optional[str] = None,
        timeout: int = 30,
        backoff: float = 5.0,
    ):
        self.api_key = api_key or get_api_key()
        self.model = model or DEFAULT_GEMINI_MODEL
        self.api_base = (api_base or GEMINI_API_BASE).rstrip("/")
        self.timeout = timeout
        self.backoff = backoff

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    def _require_key(self) -> None:
        if not self.configured:
            raise GeminiUnavailableError(
                "Gemini is disabled: no API key is set. Add one in Settings "
                "or set the GEMINI_API_KEY environment variable."
            )

    def verify_key(self) -> bool:
        """Cheaply validate the configured key by listing available models.

        Returns ``True`` when the key is accepted and ``False`` when Gemini
        rejects it. Raises :class:`GeminiError` for transport failures so
        callers can tell "wrong key" apart from "couldn't reach the service".
        """
        self._require_key()
        url = f"{self.api_base}/v1beta/models"
        try:
            resp = requests.get(url, params={"key": self.api_key}, timeout=self.timeout)
        except requests.RequestException as exc:
            raise GeminiError(f"Network error while calling Gemini: {exc}") from exc
        if resp.status_code in (400, 401, 403):
            return False
        if resp.status_code == 429:
            raise GeminiError("Gemini rate limit exceeded (HTTP 429).")
        if resp.status_code != 200:
            raise GeminiError(f"Gemini rejected the request (HTTP {resp.status_code}).")
        return True

    # -- transport ------------------------------------------------------
    def _generate_content(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        json_mode: bool = False,
    ) -> str:
        self._require_key()
        url = f"{self.api_base}/v1beta/models/{self.model}:generateContent"
        contents: list = []
        if system_instruction:
            contents.append({"role": "user", "parts": [{"text": system_instruction}]})
        contents.append({"role": "user", "parts": [{"text": prompt}]})
        generation: dict = {"temperature": 0.2, "maxOutputTokens": 4096 if json_mode else 2048}
        if json_mode:
            generation["responseMimeType"] = "application/json"
        payload = {
            "contents": contents,
            "generationConfig": generation,
        }
        last: Optional[requests.Response] = None
        for attempt in range(3):
            try:
                last = requests.post(
                    url,
                    params={"key": self.api_key},
                    json=payload,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                raise GeminiError(f"Network error while calling Gemini: {exc}") from exc
            if last.status_code == 429 and attempt < 2:
                headers = getattr(last, "headers", None) or {}
                retry_after = headers.get("Retry-After")
                try:
                    wait = float(retry_after) if retry_after else self.backoff * (attempt + 1)
                except ValueError:
                    wait = self.backoff * (attempt + 1)
                time.sleep(min(wait, 60.0))
                continue
            break
        return self._parse_response(last)

    @staticmethod
    def _parse_response(response: requests.Response) -> str:
        if response.status_code == 429:
            raise GeminiError(
                "Gemini rate limit exceeded (HTTP 429). Wait a moment or check "
                "your quota, then try again."
            )
        if response.status_code in (401, 403):
            raise GeminiError(
                "Gemini rejected the API key (HTTP "
                f"{response.status_code}). Check the key and the model id."
            )
        if response.status_code == 404:
            raise GeminiError(
                "Gemini API or model not found (HTTP 404). Check the model id: "
                f"{response.request.url if response.request else ''}"
            )
        if response.status_code != 200:
            detail = response.text[:400]
            raise GeminiError(
                f"Gemini request failed (HTTP {response.status_code}): {detail}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise GeminiError("Gemini returned an unparseable response.") from exc
        try:
            candidates = data.get("candidates") or []
            parts = (candidates[0].get("content") or {}).get("parts") or []
            return "\n".join(p.get("text", "") for p in parts).strip()
        except (IndexError, AttributeError, KeyError) as exc:
            raise GeminiError("Gemini response had an unexpected structure.") from exc

    def _generate_json(self, prompt: str, attempts: int = 2) -> dict:
        """Generate a JSON object, retrying once on a transiently incomplete
        or unparseable response (models occasionally truncate the output)."""
        for _ in range(max(1, attempts)):
            raw = self._generate_content(prompt, json_mode=True)
            parsed = _extract_json_object(raw)
            if parsed:
                return parsed
        return {}

    # -- high-level features ---------------------------------------------
    def verify_same_piece(
        self,
        summary_a: Dict[str, Any],
        summary_b: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Ask whether two analysed recordings are of the same piece.

        Returns ``{"same": float 0..1, "confidence": float 0..1, "reason": str}``
        after validation. Whatever the answer, local DTW remains authoritative.
        """
        prompt = (
            "You are assisting a music-alignment tool. Two recordings were "
            "analysed locally. Decide whether they are most likely the same piece "
            "of music. Respond with STRICT JSON only:\n"
            '{"same": 0.0-1.0, "confidence": 0.0-1.0, "reason": "<short text>"}\n'
            f"Recording A: {json.dumps(summary_a, ensure_ascii=False)}\n"
            f"Recording B: {json.dumps(summary_b, ensure_ascii=False)}"
        )
        parsed = self._generate_json(prompt)
        same = _coerce_bounded(parsed.get("same"), "same")
        confidence = _coerce_bounded(parsed.get("confidence"), "confidence")
        reason = str(parsed.get("reason", ""))[:500]
        return {"same": same, "confidence": confidence, "reason": reason}

    def analyze_low_confidence(
        self, ranges: list, summary: Dict[str, Any]
    ) -> str:
        """Ask for a *non-timestamp* assessment of unreliable regions."""
        prompt = (
            "We found alignment regions with low confidence in two performances "
            "of the same piece. Describe possible musical reasons and what to "
            "check, without inventing exact timestamps.\n"
            f"Low-confidence regions (in seconds): {json.dumps(ranges)}\n"
            f"Analysis summary: {json.dumps(summary, ensure_ascii=False)}"
        )
        return self._generate_content(prompt)[:2000]

    def locate_section_candidate(
        self, description: str, summary: Dict[str, Any], duration: float
    ) -> Optional[float]:
        """Ask the model for a *candidate* time of a described musical section.

        The returned value is only a candidate: the caller must snap it to a
        locally detected musical boundary and check confidence before use.
        """
        prompt = (
            "You are assisting a music-alignment tool. The user is looking for a "
            f"musical section described as: \"{description}\". The recording is "
            f"{duration:.1f}s long. Based ONLY on this rough audio summary, give "
            "one approximate time in LISTENING seconds where that section most "
            "likely occurs, or null if you cannot. Respond with STRICT JSON "
            'only: {"time": <seconds or null>}\n'
            f"Summary: {json.dumps(summary, ensure_ascii=False)}"
        )
        parsed = self._generate_json(prompt)
        raw = parsed.get("time")
        if raw is None or raw is False:
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        if value < 0.0 or value > duration:
            return None
        return value


def _coerce_bounded(number, name: str) -> float:
    try:
        value = float(number)
    except (TypeError, ValueError):
        return -1.0  # unknown / missing
    return max(0.0, min(1.0, value))