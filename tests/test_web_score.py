"""End-to-end tests for the score-play mode on the main page."""

from __future__ import annotations

import os
import time

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from pianosync.audio.extract import find_ffmpeg  # noqa: E402
from pianosync.config import set_api_key  # noqa: E402
from pianosync.core.alignment import TimeMap  # noqa: E402
from pianosync.score.model import NoteTiming, ScoreSync  # noqa: E402
from pianosync.web.server import create_app  # noqa: E402

MUSICXML = """<?xml version="1.0" encoding="utf-8"?>
<score-partwise version="3.1">
  <work><work-title>테스트 곡</work-title></work>
  <part-list><score-part id="P1"><part-name>Piano</part-name></score-part></part-list>
  <part id="P1">
    <measure number="1">
      <attributes><divisions>1</divisions><time><beats>4</beats><beat-type>4</beat-type></time></attributes>
      <note><pitch><step>C</step><octave>4</octave></pitch><duration>1</duration></note>
      <note><pitch><step>G</step><octave>4</octave></pitch><duration>1</duration></note>
      <note><rest/><duration>2</duration></note>
    </measure>
  </part>
</score-partwise>
"""

SAMPLE_A = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "samples", "A.MP4")
)

pytestmark = pytest.mark.skipif(
    not os.path.isfile(SAMPLE_A), reason="sample video is not available"
)


def _has_ffmpeg() -> bool:
    try:
        find_ffmpeg()
        return True
    except Exception:
        return False


@pytest.fixture()
def client(tmp_path):
    set_api_key(None)
    app = create_app(root=str(tmp_path))
    with TestClient(app) as test_client:
        yield test_client
    set_api_key(None)


def _wait(client: TestClient, job_id: str, timeout: float = 180.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job.get("finished"):
            return job
        time.sleep(0.1)
    raise AssertionError("job did not finish in time")


def _fake_align(monkeypatch):
    import numpy as np

    from pianosync.web import score_workspace as sw

    def fake(perf_features, doc, cfg, progress=None):
        duration = float(perf_features.duration)
        timings = []
        for index, note in enumerate(doc.notes):
            start = min(float(note.start_sec), duration)
            end = min(start + max(float(note.duration_sec), 0.05), duration)
            timings.append(
                NoteTiming(index, note, float(note.start_sec), start, end)
            )
        return ScoreSync(
            events=timings,
            confidence=0.88,
            low_confidence=[],
            warnings=["테스트 정렬"],
            time_map=TimeMap(
                a_times=np.array([0.0, 1.0]),
                b_times=np.array([0.0, duration]),
                a_to_b_sec=np.array([0.0, duration]),
                b_to_a_sec=np.array([0.0, 1.0]),
            ),
        )

    monkeypatch.setattr(sw, "align_score", fake)


@pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg not installed")
def test_score_mode_workflow(client, tmp_path, monkeypatch):
    page = client.get("/")
    assert page.status_code == 200
    assert "피아노 싱크" in page.text
    assert "악보 플레이" in page.text
    assert "/static/score.js" in page.text

    _fake_align(monkeypatch)

    score_path = tmp_path / "score.musicxml"
    score_path.write_text(MUSICXML, encoding="utf-8")

    with open(SAMPLE_A, "rb") as handle:
        video = client.post("/api/upload", files={"file": ("A.MP4", handle, "video/mp4")}).json()
    with open(score_path, "rb") as handle:
        score = client.post(
            "/api/upload", files={"file": ("score.musicxml", handle, "application/xml")}
        ).json()

    analyze = client.post(
        "/api/score/analyze",
        json={"video_path": video["path"], "score_path": score["path"], "quality": "fast"},
    ).json()
    job = _wait(client, analyze["job_id"])
    assert job["status"] == "done", job.get("error")

    status = client.get("/api/score/status").json()
    assert status["ready"] is True
    assert status["score"]["title"] == "테스트 곡"
    assert status["sync"]["note_count"] == 3
    assert status["sync"]["confidence"] == pytest.approx(0.88)
    assert len(status["notes"]) == 3

    svg = client.get("/api/score/svg")
    assert svg.status_code == 200
    assert 'data-note-index="0"' in svg.text

    # The comparison workspace is untouched.
    assert client.get("/api/solo/status").json()["ready"] is False

    render = client.post(
        "/api/score/render",
        json={"width": 180, "height": 320, "fps": 15, "crf": 34, "filename": "score-test.mp4"},
    ).json()
    rjob = _wait(client, render["job_id"])
    assert rjob["status"] == "done", rjob.get("error")

    result = client.get("/api/score/result")
    assert result.status_code == 200
    assert result.content[:4] in (b"\x00\x00\x00\x18", b"\x00\x00\x00\x1c", b"\x00\x00\x00\x20")
    assert len(result.content) > 1000
    assert client.get("/api/score/download").status_code == 200
