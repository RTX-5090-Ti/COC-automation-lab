from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.routes import create_router
from decision_engine import CONFIG_PATH
from project_paths import RUNTIME_DATA_DIR, dashboard_dist_path
from runtime.bot_runtime import BotRuntime
from runtime.history_store import HistoryStore
from runtime.preflight_service import PreflightService


def create_app(
    *,
    config_path: str | Path = CONFIG_PATH,
    runtime: BotRuntime | None = None,
    preflight_service: PreflightService | None = None,
    history_store: HistoryStore | None = None,
) -> FastAPI:
    application = FastAPI(title="CoC Automation Local API", version="0.1.0")
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Content-Type"],
    )
    application.state.history_store = history_store or (runtime.history_store if runtime else HistoryStore(RUNTIME_DATA_DIR))
    application.state.runtime = runtime or BotRuntime(config_path=config_path, history_store=application.state.history_store)
    application.state.preflight_service = preflight_service or PreflightService(
        config_path=config_path,
        log=application.state.runtime.log,
    )
    application.include_router(
        create_router(
            application.state.runtime,
            config_path=Path(config_path),
            preflight_service=application.state.preflight_service,
            history_store=application.state.history_store,
        )
    )
    dashboard_directory = dashboard_dist_path()
    if dashboard_directory.is_dir():
        # Mount last so API and FastAPI's built-in docs keep their existing routes.
        application.mount("/", StaticFiles(directory=dashboard_directory, html=True), name="dashboard")
    return application


app = create_app()
