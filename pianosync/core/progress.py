"""Progress reporting and cooperative cancellation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from ..errors import CancelledError

ProgressCallback = Callable[[float, str], None]
CancelFn = Callable[[], bool]


@dataclass
class Progress:
    callback: Optional[ProgressCallback] = None
    is_cancelled: Optional[CancelFn] = None
    label: str = ""

    def update(self, fraction: float, message: str = "") -> None:
        if self.is_cancelled and self.is_cancelled():
            raise CancelledError(message or "Operation cancelled")
        if self.callback:
            self.callback(max(0.0, min(1.0, fraction)), message)

    def check(self) -> None:
        if self.is_cancelled and self.is_cancelled():
            raise CancelledError("Operation cancelled")
