from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import cv2
import httpx
import numpy as np
import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    target = tmp_path / "bot_config.json"
    shutil.copyfile(PROJECT_ROOT / "config" / "bot_config.json", target)
    return target


@pytest.fixture
def builder_enemy_base_screenshot(tmp_path: Path) -> Path:
    """Create an isolated Builder Base screenshot fixture without runtime artifacts."""
    screenshot = np.full((1080, 1920, 3), (32, 48, 32), dtype=np.uint8)
    banner = cv2.imread(
        str(PROJECT_ROOT / "templates" / "builder_base" / "enemy_base_banner.png"),
        cv2.IMREAD_UNCHANGED,
    )
    assert banner is not None

    top, left = 24, 760
    height, width = banner.shape[:2]
    alpha = banner[:, :, 3:4] / 255.0
    target = screenshot[top : top + height, left : left + width]
    target[:] = (banner[:, :, :3] * alpha + target * (1.0 - alpha)).astype(np.uint8)

    output = tmp_path / "builder_enemy_base.png"
    assert cv2.imwrite(str(output), screenshot)
    return output


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
