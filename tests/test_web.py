"""End-to-end tests for the FastAPI web edition.

These exercise the real pipeline (FFmpeg + DTW) on the bundled sample videos, so
they are skipped automatically when FFmpeg is unavailable.
"""

from __future__ import annotations

import os
import time

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from pianosync.audio.extract import find_ffmpeg  # noqa: E402
from pianosync.web.server import create_app  # noqa: E402

SAMPLES = os.path.join(os.path.dirname(__file__), "..", "samples")
SAMPLE_A = os.path.abspath(os.path.join(SAMPLES, "A.MP4"))
SAMPLE_B = os.path.abspath(os.path.join(SAMPLES, "B.MP4"))

pytestmark = pytest.mark.skipif(
    not (os.path.isfile(SAMPLE_A) and os.path.isfile(SAMPLE_B)),
    reason="sample videos are not available",
)


def _has_ffmpeg() -> bool:
    try:
        find_ffmpeg()
        return True
    except Exception:
        return False


def _wait(client: TestClient, job_id: str, timeout: float = 180.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job.get("finished"):
            return job
        time.sleep(0.1)
    raise AssertionError("job did not finish in time")


@pytest.fixture()
def client(tmp_path):
    app = create_app(root=str(tmp_path))
    with TestClient(app) as test_client:
        yield test_client


def test_health_and_config(client):
    assert client.get("/api/health").json()["ok"] is True
    cfg = client.get("/api/config").json()
    assert {q["value"] for q in cfg["quality"]} == {"fast", "balanced", "accurate"}
    assert "defaults" in cfg


def test_index_served(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "Piano Sync Switcher" in res.text


def test_upload(client, tmp_path):
    payload = b"not-a-real-video"
    res = client.post(
        "/api/upload",
        files={"file": ("clip.mp4", payload, "video/mp4")},
    )
    assert res.status_code == 200
    data = res.json()
    assert os.path.isfile(data["path"])
    assert data["size"] == len(payload)
    assert data["name"] == "clip.mp4"


def test_analyze_requires_files(client):
    res = client.post("/api/analyze", json={"path_a": "/nope", "path_b": "/nope"})
    assert res.status_code == 400


@pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg not installed")
def test_full_workflow(client):
    body = {
        "path_a": SAMPLE_A,
        "path_b": SAMPLE_B,
        "quality": "fast",
        "interval_seconds": 4,
        "use_ai": False,
    }
    job = _wait(client, client.post("/api/analyze", json=body).json()["job_id"])
    assert job["status"] == "done", job.get("error")

    status = client.get("/api/status").json()
    assert status["ready"] is True
    assert status["a"]["name"] == "A.MP4"
    assert status["plan"]["segments"]

    wave = client.get("/api/waveform").json()
    assert wave["ready"] is True
    assert len(wave["env_a"]["t"]) > 0
    assert wave["map"]["a"] and wave["map"]["b"]
    assert wave["segments"]

    plan = client.post("/api/transitions", json={"interval_seconds": 3, "effect": "cut"}).json()
    assert plan["plan"]["segments"]

    added = client.post("/api/anchors", json={"action": "add", "a": 3.0}).json()
    assert added["anchor_count"] == 1
    removed = client.post("/api/anchors", json={"action": "remove", "index": 0}).json()
    assert removed["anchor_count"] == 0

    frame = client.get("/api/frame", params={"side": "a", "t": 1.0, "w": 200})
    assert frame.status_code == 200
    assert frame.headers["content-type"] == "image/png"
    assert frame.content[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg not installed")
def test_render_and_download(client):
    analyze = client.post(
        "/api/analyze",
        json={"path_a": SAMPLE_A, "path_b": SAMPLE_B, "quality": "fast", "use_ai": False},
    ).json()
    assert _wait(client, analyze["job_id"])["status"] == "done"

    render = client.post(
        "/api/render",
        json={
            "filename": "out.mp4",
            "width": 640,
            "height": 360,
            "fps": 24,
            "crf": 28,
            "effect": "cut",
            "audio_mode": "A",
            "include_audio": True,
        },
    ).json()
    job = _wait(client, render["job_id"])
    assert job["status"] == "done", job.get("error")
    assert job["result"]["segments"] >= 1

    download = client.get("/api/download")
    assert download.status_code == 200
    assert download.headers["content-type"] == "video/mp4"
    assert len(download.content) > 1000
