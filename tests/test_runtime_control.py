from __future__ import annotations

import time

import pytest

from runtime.runtime_control import RuntimeControl, SessionTimedOut


def test_runtime_control_reports_elapsed_time_and_stops_at_deadline() -> None:
    reports: list[dict] = []
    started = time.monotonic() - 2.0
    control = RuntimeControl(
        reporter=reports.append,
        started_monotonic=started,
        deadline_monotonic=time.monotonic() - 0.01,
    )

    with pytest.raises(SessionTimedOut):
        control.checkpoint("TEST_DEADLINE")

    assert reports[0]["phase"] == "TEST_DEADLINE"
    assert reports[-1]["sessionElapsedSeconds"] >= 2.0
    assert reports[-1]["sessionRemainingSeconds"] == 0.0
