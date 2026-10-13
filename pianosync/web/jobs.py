"""Background job manager for long-running analysis and rendering.

Each job runs its callable in a daemon thread and exposes progress/status that
the SSE endpoint polls. Only one heavy job is allowed at a time.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Callable, Dict, Optional

from ..core.progress import Progress
from ..errors import CancelledError, PianoSyncError


class Job:
    def __init__(self, kind: str):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.status = "queued"  # queued | running | done | error | cancelled
        self.progress = 0.0
        self.message = "대기 중…"
        self.result: Optional[dict] = None
        self.error: Optional[str] = None
        self.cancel_event = threading.Event()
        self.created = time.time()
        self.finished = False

    def public(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "progress": round(self.progress, 4),
            "message": self.message,
            "error": self.error,
            "result": self.result,
            "finished": self.finished,
        }


class JobManager:
    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()
        self._active: Optional[str] = None

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def active_id(self) -> Optional[str]:
        return self._active

    def create(self, kind: str) -> Job:
        job = Job(kind)
        with self._lock:
            self._jobs[job.id] = job
            self._active = job.id
            # Trim old finished jobs.
            if len(self._jobs) > 40:
                for jid, j in list(self._jobs.items()):
                    if j.finished and jid != job.id:
                        self._jobs.pop(jid, None)
        return job

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job and not job.finished:
            job.cancel_event.set()
            return True
        return False

    def run(self, job: Job, fn: Callable[[Progress], dict]) -> None:
        def worker() -> None:
            job.status = "running"
            job.message = "시작 중…"
            progress = Progress(
                callback=self._make_callback(job),
                is_cancelled=job.cancel_event.is_set,
            )
            try:
                job.result = fn(progress) or {}
                job.progress = 1.0
                job.status = "done"
            except CancelledError:
                job.status = "cancelled"
                job.message = "취소됨"
            except PianoSyncError as exc:
                job.status = "error"
                job.error = str(exc)
            except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
                job.status = "error"
                job.error = f"{type(exc).__name__}: {exc}"
            finally:
                job.finished = True

        threading.Thread(target=worker, name=f"job-{job.id}", daemon=True).start()

    @staticmethod
    def _make_callback(job: Job) -> Callable[[float, str], None]:
        lock = threading.Lock()

        def callback(fraction: float, message: str) -> None:
            with lock:
                job.progress = max(0.0, min(1.0, float(fraction)))
                if message:
                    job.message = str(message)

        return callback
