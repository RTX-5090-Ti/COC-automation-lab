"""Stable paths for source development and packaged Electron runs."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _bundled_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")).resolve()
    return Path(__file__).resolve().parent


BACKEND_ROOT = _bundled_root()


def _environment_path(name: str, default: Path) -> Path:
    value = os.getenv(name)
    return Path(value).expanduser().resolve() if value else default


_default_runtime_data_dir = (
    Path(os.getenv("LOCALAPPDATA", Path.home())) / "CoC Field Console"
    if getattr(sys, "frozen", False)
    else BACKEND_ROOT
)
RUNTIME_DATA_DIR = _environment_path("COC_RUNTIME_DATA_DIR", _default_runtime_data_dir)
CONFIG_PATH = _environment_path("COC_CONFIG_PATH", RUNTIME_DATA_DIR / "config" / "bot_config.json")
SCREENSHOTS_DIRECTORY = RUNTIME_DATA_DIR / "screenshots"
CURRENT_SCREENSHOT_PATH = SCREENSHOTS_DIRECTORY / "current" / "current.png"
DEBUG_DIRECTORY = SCREENSHOTS_DIRECTORY / "debug"


def asset_path(*parts: str) -> Path:
    """Return a read-only bundled asset path such as a template image."""
    return BACKEND_ROOT.joinpath(*parts)


def dashboard_dist_path() -> Path:
    """Return the Vite output location in source and packaged layouts."""
    return BACKEND_ROOT / "frontend" / "dist"
