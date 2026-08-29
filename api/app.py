from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.routes import create_router
from decision_engine import CONFIG_PATH
from project_paths import dashboard_dist_path
from runtime.bot_runtime import BotRuntime


def create_app(*, config_path: str | Path = CONFIG_PATH, runtime: BotRuntime | None = None) -> FastAPI:
    application = FastAPI(title="CoC Automation Local API", version="0.1.0")
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Content-Type"],
    )
    application.state.runtime = runtime or BotRuntime(config_path=config_path)
    application.include_router(create_router(application.state.runtime, config_path=Path(config_path)))
    dashboard_directory = dashboard_dist_path()
    if dashboard_directory.is_dir():
        # Mount last so API and FastAPI's built-in docs keep their existing routes.
        application.mount("/", StaticFiles(directory=dashboard_directory, html=True), name="dashboard")
    return application


app = create_app()
