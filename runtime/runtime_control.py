from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any


class StopRequested(Exception):
    """Raised at a safe controller checkpoint after a stop request."""


class SessionTimedOut(Exception):
    """Raised at a safe checkpoint when the session runtime budget expires."""


class RuntimeControl:
    """Cooperative pause/stop and telemetry hooks shared with controllers."""

    def __init__(
        self,
        stop_event: threading.Event | None = None,
        pause_event: threading.Event | None = None,
        reporter: Callable[[dict[str, Any]], None] | None = None,
        logger: Callable[[int, str], None] | None = None,
        started_monotonic: float | None = None,
        deadline_monotonic: float | None = None,
    ) -> None:
        self._stop_event = stop_event or threading.Event()
        self._pause_event = pause_event or threading.Event()
        self._reporter = reporter or (lambda _: None)
        self._logger = logger or (lambda _level, _message: None)
        self._started_monotonic = started_monotonic
        self._deadline_monotonic = deadline_monotonic
        self._last_reported_elapsed_second = -1

    def checkpoint(self, phase: str | None = None) -> None:
        if phase:
            self.report(phase=phase)
        self._check_runtime_budget()
        while self._pause_event.is_set():
            if self._stop_event.wait(0.1):
                raise StopRequested()
            self._check_runtime_budget()
        if self._stop_event.is_set():
            raise StopRequested()

    def _check_runtime_budget(self) -> None:
        if self._started_monotonic is None or self._deadline_monotonic is None:
            return
        now = time.monotonic()
        elapsed_seconds = max(0.0, now - self._started_monotonic)
        remaining_seconds = max(0.0, self._deadline_monotonic - now)
        elapsed_whole_seconds = int(elapsed_seconds)
        if elapsed_whole_seconds != self._last_reported_elapsed_second:
            self._last_reported_elapsed_second = elapsed_whole_seconds
            self.report(
                sessionElapsedSeconds=round(elapsed_seconds, 1),
                sessionRemainingSeconds=round(remaining_seconds, 1),
            )
        if now >= self._deadline_monotonic:
            raise SessionTimedOut()

    def report(self, **values: Any) -> None:
        self._reporter(values)

    def log(self, level: int, message: str) -> None:
        self._logger(level, message)


NULL_RUNTIME_CONTROL = RuntimeControl()
