"""Background workers with cooperative cancellation and progress signals."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QThread, Signal

from ..core.progress import Progress
from ..errors import CancelledError, PianoSyncError


def friendly_error(exc: BaseException) -> str:
    """Convert any exception into a concise, user-presentable message."""
    if isinstance(exc, PianoSyncError):
        return str(exc)
    if isinstance(exc, KeyboardInterrupt):
        return "Interrupted."
    return f"{type(exc).__name__}: {exc}"


class TaskWorker(QThread):
    """Runs a callable in a thread, emitting progress, result, error or cancel.

    The callable must accept a ``progress=`` keyword (a ``Progress`` object).
    """

    progress_ = Signal(float, str)
    finished_ = Signal(object)
    error_ = Signal(str)
    cancelled_ = Signal()

    def __init__(self, fn: Callable, *args, **kwargs):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._cancelled_flag = False

    def run(self) -> None:
        progress = Progress(
            callback=lambda f, m: self.progress_.emit(float(f), str(m)),
            is_cancelled=lambda: self._cancelled_flag,
        )
        try:
            result = self._fn(*self._args, **self._kwargs, progress=progress)
        except CancelledError:
            self.cancelled_.emit()
            return
        except Exception as exc:  # noqa: BLE001 - report to UI
            self.error_.emit(friendly_error(exc))
            return
        self.finished_.emit(result)

    def cancel(self) -> None:
        self._cancelled_flag = True


def run_task(
    widget,
    fn: Callable,
    on_result: Callable,
    on_error: Optional[Callable] = None,
    on_progress: Optional[Callable] = None,
    *args,
    **kwargs,
) -> TaskWorker:
    """Create and start a TaskWorker wired to the caller's slots.

    The worker is kept referenced on the owner widget until it finishes so it is
    never garbage-collected mid-run.
    """
    worker = TaskWorker(fn, *args, **kwargs)
    holders = getattr(widget, "_workers", None)
    if holders is None:
        holders = []
        widget._workers = holders
    holders.append(worker)

    def _cleanup(*_args):
        if worker in holders:
            holders.remove(worker)

    worker.finished_.connect(lambda result: on_result(result))
    if on_error:
        worker.error_.connect(lambda text: on_error(text))
    else:
        worker.error_.connect(lambda text: _popup_error(widget, text))
    if on_progress:
        worker.progress_.connect(on_progress)
    worker.finished_.connect(_cleanup)
    worker.error_.connect(_cleanup)
    worker.cancelled_.connect(_cleanup)
    widget.destroyed.connect(worker.quit)
    worker.start()
    return worker


def _popup_error(widget, text: str) -> None:
    from PySide6.QtWidgets import QMessageBox

    QMessageBox.critical(widget, "작업 실패", text)