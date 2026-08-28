from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any


class StopRequested(Exception):
    """Raised at a safe controller checkpoint after a stop request."""


class RuntimeControl:
    """Cooperative pause/stop and telemetry hooks shared with controllers."""

    def __init__(
        self,
        stop_event: threading.Event | None = None,
        pause_event: threading.Event | None = None,
        reporter: Callable[[dict[str, Any]], None] | None = None,
        logger: Callable[[int, str], None] | None = None,
    ) -> None:
        self._stop_event = stop_event or threading.Event()
        self._pause_event = pause_event or threading.Event()
        self._reporter = reporter or (lambda _: None)
        self._logger = logger or (lambda _level, _message: None)

    def checkpoint(self, phase: str | None = None) -> None:
        if phase:
            self.report(phase=phase)
        while self._pause_event.is_set():
            if self._stop_event.wait(0.1):
                raise StopRequested()
        if self._stop_event.is_set():
            raise StopRequested()

    def report(self, **values: Any) -> None:
        self._reporter(values)

    def log(self, level: int, message: str) -> None:
        self._logger(level, message)


NULL_RUNTIME_CONTROL = RuntimeControl()
