from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class BotConfigUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    minimum_gold: int | None = Field(default=None, alias="minimumGold", ge=0)
    minimum_elixir: int | None = Field(default=None, alias="minimumElixir", ge=0)
    minimum_dark_elixir: int | None = Field(default=None, alias="minimumDarkElixir", ge=0)
    require_all_resources: bool | None = Field(default=None, alias="requireAllResources")
    max_bases_to_check: int | None = Field(default=None, alias="maxBasesToCheck", ge=1, le=20)
    max_runtime_seconds: float | None = Field(default=None, alias="maxRuntimeSeconds", gt=0, le=3600)
    battles_per_session: Literal[1, 5, 10] | None = Field(default=None, alias="battlesPerSession")
    farm_mode: Literal["home_village", "builder_base"] | None = Field(default=None, alias="farmMode")
    max_ocr_attempts_per_base: int | None = Field(default=None, alias="maxOcrAttemptsPerBase", ge=1, le=10)
    strategy: str | None = Field(default=None, alias="strategy", pattern="^sneaky_goblin$")
    dry_run: bool | None = Field(default=None, alias="dryRun")

class ActionResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    success: bool
    message: str
    runtime_state: str = Field(alias="runtimeState")


class HistorySessionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(alias="sessionId")
    started_at: str = Field(alias="startedAt")
    ended_at: str | None = Field(alias="endedAt")
    duration_seconds: float | None = Field(alias="durationSeconds")
    runtime_state: str | None = Field(alias="runtimeState")
    terminal_result: str | None = Field(alias="terminalResult")
    terminal_message: str | None = Field(alias="terminalMessage")
    dry_run: bool | None = Field(alias="dryRun")
    strategy: str | None = None
    bases_checked: int = Field(alias="basesChecked")
    max_bases: int = Field(alias="maxBases")
    next_taps: int = Field(alias="nextTaps")


class HistoryListResponse(BaseModel):
    items: list[HistorySessionSummary]
    total: int
    limit: int
    offset: int


class HistorySessionDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(alias="sessionId")
    started_at: str = Field(alias="startedAt")
    ended_at: str | None = Field(alias="endedAt")
    duration_seconds: float | None = Field(alias="durationSeconds")
    runtime_state: str | None = Field(alias="runtimeState")
    terminal_result: str | None = Field(alias="terminalResult")
    terminal_message: str | None = Field(alias="terminalMessage")
    last_error: str | None = Field(alias="lastError")
    phase: str | None = None
    dry_run: bool | None = Field(alias="dryRun")
    strategy: str | None = None
    device_serial: str | None = Field(alias="deviceSerial")
    bases_checked: int = Field(alias="basesChecked")
    max_bases: int = Field(alias="maxBases")
    next_taps: int = Field(alias="nextTaps")
    preflight: dict[str, Any] | None
    telemetry: dict[str, Any] | None
    attack_plan: dict[str, Any] | None = Field(alias="attackPlan")
    artifact_paths: list[str] = Field(alias="artifactPaths")
    events: list[dict[str, Any]]
