from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from api.schemas import ActionResponse, BotConfigUpdate
from decision_engine import CONFIG_PATH, DecisionEngineError, load_bot_config, update_bot_config
from runtime.bot_runtime import BotRuntime
from runtime.runtime_state import RuntimeState


def create_router(runtime: BotRuntime, *, config_path: Path = CONFIG_PATH) -> APIRouter:
    router = APIRouter(prefix="/api")

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

    @router.get("/config")
    def get_config() -> dict:
        try:
            load_bot_config(config_path)
        except DecisionEngineError as error:
            raise HTTPException(status_code=400, detail=f"Invalid configuration: {error}") from error
        return json.loads(config_path.read_text(encoding="utf-8"))

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
            update_bot_config(updates, config_path)
        except DecisionEngineError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return json.loads(config_path.read_text(encoding="utf-8"))

    @router.post("/session/start", response_model=ActionResponse)
    def start() -> ActionResponse:
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
