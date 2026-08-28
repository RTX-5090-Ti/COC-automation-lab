from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import httpx
import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    target = tmp_path / "bot_config.json"
    shutil.copyfile(PROJECT_ROOT / "config" / "bot_config.json", target)
    return target


def request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def wait_for(predicate, timeout: float = 2.0) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Condition was not met before timeout.")
