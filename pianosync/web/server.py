"""FastAPI application for the web edition of Piano Sync Switcher.

Run with ``python -m pianosync.web`` or ``piano-sync-switcher-web`` and open the
printed URL in a browser. Analysis and rendering run on the local machine via
FFmpeg; the browser is only the interface.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import List, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ..ai.gemini import GeminiClient
from ..config import DEFAULT_GEMINI_MODEL, get_api_key, set_api_key
from ..core.analysis import MAX_TRACKS
from ..errors import PianoSyncError
from ..video.frames import extract_frame_png
from .jobs import JobManager
from .workspace import Workspace

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
HOME = os.path.expanduser("~")
DEFAULT_ROOT = os.path.join(HOME, ".pianosync", "web")


def _mask_key(key: Optional[str]) -> Optional[str]:
    if not key:
        return None
    key = key.strip()
    return "••••" + key[-4:] if len(key) > 8 else "••••"


def _load_settings(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_settings(path: str, data: dict) -> None:
    """Persist settings atomically with owner-only permissions (0600)."""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _ai_settings_payload(verified: Optional[bool] = None) -> dict:
    key = get_api_key()
    out = {
        "ai_configured": bool(key),
        "api_key_masked": _mask_key(key),
        "gemini_model": DEFAULT_GEMINI_MODEL,
    }
    if verified is not None:
        out["verified"] = verified
    return out


#: Distinct, harmonious palette for up to MAX_TRACKS performances.
SOURCE_COLORS = ["#34d399", "#fbbf24", "#f472b6", "#38bdf8", "#a3e635"]

CONFIG = {
    "max_sources": MAX_TRACKS,
    "source_colors": SOURCE_COLORS,
    "quality": [
        {"value": "fast", "label": "빠르게"},
        {"value": "balanced", "label": "균형"},
        {"value": "accurate", "label": "정확하게"},
    ],
    "mode": [
        {"value": "musical", "label": "음악 위치 기준 (모드 1)"},
        {"value": "fixed_output", "label": "출력 시간 기준 (모드 2)"},
    ],
    "effect": [
        {"value": "cut", "label": "하드 컷"},
        {"value": "crossfade", "label": "크로스페이드"},
        {"value": "fadeblack", "label": "블랙 페이드"},
        {"value": "fadewhite", "label": "화이트 페이드"},
        {"value": "fadegrays", "label": "그레이 페이드"},
        {"value": "dissolve", "label": "디졸브"},
        {"value": "slideleft", "label": "좌측 슬라이드"},
        {"value": "slideright", "label": "우측 슬라이드"},
        {"value": "circleopen", "label": "원형 열기"},
    ],
    "audio_mode": [
        {"value": "A", "label": "기준 영상(A) 연속"},
        {"value": "B", "label": "두 번째 영상(B)"},
        {"value": "aligned", "label": "화면 따라 전환"},
        {"value": "none", "label": "오디오 없음"},
    ],
    "resolution": [
        {"value": "1920x1080", "label": "1080p (1920×1080)"},
        {"value": "1280x720", "label": "720p (1280×720)"},
        {"value": "854x480", "label": "480p (854×480)"},
        {"value": "1080x1920", "label": "세로 1080×1920"},
    ],
    "preset": [
        {"value": "ultrafast", "label": "ultrafast"},
        {"value": "veryfast", "label": "veryfast"},
        {"value": "fast", "label": "fast"},
        {"value": "medium", "label": "medium"},
        {"value": "slow", "label": "slow"},
    ],
    "defaults": {
        "interval_seconds": 5.0,
        "crf": 20,
        "fps": 30,
        "effect": "cut",
        "audio_mode": "A",
        "mode": "musical",
        "quality": "balanced",
        "gemini_model": DEFAULT_GEMINI_MODEL,
    },
}


def create_app(
    workspace: Optional[Workspace] = None,
    root: Optional[str] = None,
) -> FastAPI:
    root = root or os.environ.get("PIANOSYNC_WEB_ROOT", DEFAULT_ROOT)
    uploads_dir = os.path.join(root, "uploads")
    os.makedirs(uploads_dir, exist_ok=True)
    settings_path = os.path.join(root, "settings.json")
    settings = _load_settings(settings_path)
    if settings.get("gemini_api_key"):
        set_api_key(settings["gemini_api_key"])
    ws = workspace or Workspace(
        cache_dir=os.path.join(root, "cache"),
        output_dir=os.path.join(root, "outputs"),
    )
    jobs = JobManager()

    app = FastAPI(title="Piano Sync Switcher", version="0.2.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.workspace = ws
    app.state.jobs = jobs
    app.state.uploads_dir = uploads_dir

    # -- static ---------------------------------------------------------
    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))

    @app.get("/manifest.webmanifest")
    async def manifest() -> FileResponse:
        return FileResponse(
            os.path.join(STATIC_DIR, "manifest.webmanifest"),
            media_type="application/manifest+json",
        )

    @app.get("/sw.js")
    async def service_worker() -> FileResponse:
        resp = FileResponse(
            os.path.join(STATIC_DIR, "sw.js"),
            media_type="application/javascript",
        )
        resp.headers["Service-Worker-Allowed"] = "/"
        return resp

    @app.get("/api/config")
    async def config() -> dict:
        data = dict(CONFIG)
        data["defaults"] = dict(CONFIG["defaults"])
        data["defaults"]["ai_configured"] = bool(get_api_key())
        data["defaults"]["api_key_masked"] = _mask_key(get_api_key())
        return data

    @app.get("/api/settings")
    async def read_settings() -> dict:
        return _ai_settings_payload()

    @app.post("/api/settings/api_key")
    async def update_api_key(payload: dict) -> dict:
        key = str(payload.get("api_key") or "").strip()
        verified: Optional[bool] = None
        if key:
            try:
                verified = GeminiClient(api_key=key).verify_key()
            except PianoSyncError:
                verified = None  # couldn't reach Gemini; store and let the user retry
            if verified is False:
                raise HTTPException(
                    status_code=400,
                    detail="키가 유효하지 않습니다. 값을 다시 확인해 주세요.",
                )
        settings["gemini_api_key"] = key
        set_api_key(key or None)
        _save_settings(settings_path, settings)
        return _ai_settings_payload(verified)

    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True}

    # -- uploads --------------------------------------------------------
    def _safe_name(name: str) -> str:
        name = os.path.basename(name or "upload") or "upload"
        return "".join(ch for ch in name if ch.isalnum() or ch in "._- ") or "upload"

    @app.post("/api/upload")
    async def upload(file: UploadFile = File(...)) -> dict:
        target_dir = os.path.join(uploads_dir, uuid.uuid4().hex[:10])
        os.makedirs(target_dir, exist_ok=True)
        safe = _safe_name(file.filename or "upload")
        path = os.path.join(target_dir, safe)
        size = 0
        with open(path, "wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                size += len(chunk)
        if size == 0:
            os.remove(path)
            raise HTTPException(status_code=400, detail="빈 파일은 업로드할 수 없습니다.")
        return {"path": path, "name": safe, "size": size}

    # -- analysis jobs --------------------------------------------------
    def _require_file(path: Optional[str]) -> str:
        if not path or not os.path.isfile(path):
            raise HTTPException(status_code=400, detail=f"파일을 찾을 수 없습니다: {path}")
        return path

    @app.post("/api/analyze")
    async def analyze(payload: dict) -> dict:
        raw_paths = payload.get("paths")
        if raw_paths is None:
            raw_paths = [payload.get("path_a"), payload.get("path_b")]
        paths = [_require_file(p) for p in raw_paths if p]
        if len(paths) < 2:
            raise HTTPException(status_code=400, detail="최소 2개의 영상이 필요합니다.")
        if len(paths) > MAX_TRACKS:
            raise HTTPException(status_code=400, detail=f"최대 {MAX_TRACKS}개까지 지원합니다.")
        quality = payload.get("quality", "balanced")
        use_ai = bool(payload.get("use_ai", True))
        ws.transition.interval_seconds = float(
            payload.get("interval_seconds", ws.transition.interval_seconds)
        )
        job = jobs.create("analyze")

        def work(progress) -> dict:
            return ws.analyze_paths(paths, quality=quality, use_ai=use_ai, progress=progress)

        jobs.run(job, work)
        return {"job_id": job.id}

    @app.post("/api/new")
    async def new_project() -> dict:
        ws.reset()
        return {"ok": True}

    # -- plan editing ---------------------------------------------------
    @app.get("/api/status")
    async def status() -> dict:
        return ws.status_payload()

    @app.get("/api/waveform")
    async def waveform() -> dict:
        return ws.waveform_payload()

    @app.post("/api/transitions")
    async def transitions(payload: dict) -> dict:
        try:
            return ws.regenerate(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    # -- render jobs ----------------------------------------------------
    @app.post("/api/render")
    async def render(payload: dict) -> dict:
        job = jobs.create("render")

        def work(progress) -> dict:
            result = ws.render(payload, progress=progress)
            return {
                "name": os.path.basename(result.path),
                "duration": round(result.duration, 2),
                "width": result.width,
                "height": result.height,
                "segments": result.segments,
                "audio_mode": result.audio_mode,
                "audio_included": result.audio_included,
            }

        jobs.run(job, work)
        return {"job_id": job.id}

    # -- job polling / SSE ---------------------------------------------
    @app.get("/api/jobs/{job_id}")
    async def job_status(job_id: str) -> dict:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
        return job.public()

    @app.post("/api/jobs/{job_id}/cancel")
    async def job_cancel(job_id: str) -> dict:
        return {"cancelled": jobs.cancel(job_id)}

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str) -> StreamingResponse:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")

        async def stream():
            last = None
            while True:
                data = json.dumps(job.public(), ensure_ascii=False)
                if data != last:
                    yield f"data: {data}\n\n"
                    last = data
                if job.finished:
                    break
                await asyncio.sleep(0.15)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # -- media / frames -------------------------------------------------
    def _source_path(index: int, side: str) -> str:
        if not ws.paths:
            raise HTTPException(status_code=404, detail="영상이 없습니다.")
        if index >= 0:
            if index < len(ws.paths):
                return ws.paths[index]
            raise HTTPException(status_code=404, detail="영상이 없습니다.")
        idx = 0 if side == "a" else 1
        if 0 <= idx < len(ws.paths):
            return ws.paths[idx]
        raise HTTPException(status_code=404, detail="영상이 없습니다.")

    @app.get("/api/media")
    async def media(side: str = "a", index: int = -1) -> FileResponse:
        path = _source_path(index, side)
        if not os.path.isfile(path):
            raise HTTPException(status_code=404, detail="영상이 없습니다.")
        return FileResponse(path, media_type="video/mp4")

    @app.get("/api/frame")
    async def frame(side: str = "a", t: float = 0.0, w: int = 420, index: int = -1) -> Response:
        path = _source_path(index, side)
        if not os.path.isfile(path):
            raise HTTPException(status_code=404, detail="영상이 없습니다.")
        try:
            data = extract_frame_png(path, t, width=max(80, min(w, 960)))
        except PianoSyncError as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        if data is None:
            raise HTTPException(status_code=404, detail="프레임을 추출할 수 없습니다.")
        return Response(content=data, media_type="image/png")

    @app.get("/api/download")
    async def download() -> FileResponse:
        result = ws.last_render
        if result is None or not os.path.isfile(result.path):
            raise HTTPException(status_code=404, detail="생성된 영상이 없습니다.")
        return FileResponse(
            result.path,
            media_type="video/mp4",
            filename=os.path.basename(result.path),
        )

    # -- error helper ---------------------------------------------------
    @app.exception_handler(PianoSyncError)
    async def pianosync_error(_request, exc: PianoSyncError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    if os.path.isdir(STATIC_DIR):
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app


app = create_app()
