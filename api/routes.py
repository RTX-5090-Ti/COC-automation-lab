from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from api.schemas import ActionResponse, BotConfigUpdate, HistoryListResponse, HistorySessionDetail
from decision_engine import (
    CONFIG_PATH,
    DecisionEngineError,
    load_bot_config,
    serialize_bot_config,
    update_bot_config,
)
from runtime.bot_runtime import BotRuntime
from runtime.history_store import HistoryStore, HistoryStoreError
from runtime.preflight_service import PreflightService
from runtime.runtime_state import RuntimeState


def create_router(
    runtime: BotRuntime,
    *,
    config_path: Path = CONFIG_PATH,
    preflight_service: PreflightService | None = None,
    history_store: HistoryStore | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api")
    service = preflight_service or PreflightService(config_path=config_path, log=runtime.log)
    history = history_store or runtime.history_store

    @router.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/status")
    def status() -> dict:
        return runtime.status()

    @router.get("/telemetry")
    def telemetry() -> dict:
        return runtime.telemetry()

    @router.get("/logs")
    def logs(limit: int = Query(default=100, ge=1, le=500)) -> dict:
        return {"entries": runtime.logs(limit)}

    @router.get("/history/sessions", response_model=HistoryListResponse)
    def history_sessions(limit: int = Query(default=25, ge=1, le=100), offset: int = Query(default=0, ge=0)) -> dict:
        try:
            return history.list_sessions(limit=limit, offset=offset)
        except HistoryStoreError as error:
            raise HTTPException(status_code=503, detail=f"History storage is unavailable: {error}") from error

    @router.get("/history/sessions/{session_id}", response_model=HistorySessionDetail)
    def history_session(session_id: str) -> dict:
        try:
            session = history.get_session(session_id)
        except HistoryStoreError as error:
            raise HTTPException(status_code=503, detail=f"History storage is unavailable: {error}") from error
        if session is None:
            raise HTTPException(status_code=404, detail="Session history record was not found.")
        return session

    @router.get("/preflight")
    def get_preflight() -> dict:
        return {"report": service.latest()}

    @router.post("/preflight/run")
    def run_preflight() -> dict:
        report = service.run()
        runtime.set_preflight_report(report)
        return report

    @router.get("/config")
    def get_config() -> dict:
        try:
            config = load_bot_config(config_path)
        except DecisionEngineError as error:
            raise HTTPException(status_code=400, detail=f"Invalid configuration: {error}") from error
        return serialize_bot_config(config)

    @router.put("/config")
    def put_config(update: BotConfigUpdate) -> dict:
        if runtime.status()["runtimeState"] in {
            RuntimeState.STARTING.value,
            RuntimeState.RUNNING.value,
            RuntimeState.PAUSED.value,
            RuntimeState.STOPPING.value,
        }:
            raise HTTPException(status_code=409, detail="Configuration cannot change while a session is active.")
        updates = update.model_dump(by_alias=True, exclude_none=True)
        if not updates:
            raise HTTPException(status_code=400, detail="At least one configuration value is required.")
        try:
            config = update_bot_config(updates, config_path)
        except DecisionEngineError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return serialize_bot_config(config)

    @router.post("/session/start", response_model=ActionResponse)
    def start() -> ActionResponse:
        report = service.latest()
        if report and report["overallStatus"] == "blocked":
            failures = [check["title"] for check in report["checks"] if check["status"] == "fail"]
            raise HTTPException(status_code=409, detail="Preflight is blocked: " + ", ".join(failures))
        success, message = runtime.start()
        if not success:
            raise HTTPException(status_code=409, detail=message)
        return ActionResponse(success=True, message=message, runtimeState=RuntimeState.STARTING.value)

    @router.post("/session/pause", response_model=ActionResponse)
    def pause() -> ActionResponse:
        success, message = runtime.pause()
        if not success:
            raise HTTPException(status_code=409, detail=message)
        return ActionResponse(success=True, message=message, runtimeState=runtime.status()["runtimeState"])

    @router.post("/session/resume", response_model=ActionResponse)
    def resume() -> ActionResponse:
        success, message = runtime.resume()
        if not success:
            raise HTTPException(status_code=409, detail=message)
        return ActionResponse(success=True, message=message, runtimeState=runtime.status()["runtimeState"])

    @router.post("/session/stop", response_model=ActionResponse)
    def stop() -> ActionResponse:
        success, message = runtime.stop()
        return ActionResponse(success=success, message=message, runtimeState=runtime.status()["runtimeState"])

    return router
