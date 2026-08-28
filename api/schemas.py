from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class BotConfigUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    minimum_gold: int | None = Field(default=None, alias="minimumGold", ge=0)
    minimum_elixir: int | None = Field(default=None, alias="minimumElixir", ge=0)
    minimum_dark_elixir: int | None = Field(default=None, alias="minimumDarkElixir", ge=0)
    require_all_resources: bool | None = Field(default=None, alias="requireAllResources")
    max_bases_to_check: int | None = Field(default=None, alias="maxBasesToCheck", ge=1, le=20)
    max_runtime_seconds: float | None = Field(default=None, alias="maxRuntimeSeconds", gt=0, le=3600)
    max_ocr_attempts_per_base: int | None = Field(default=None, alias="maxOcrAttemptsPerBase", ge=1, le=10)
    strategy: str | None = Field(default=None, alias="strategy", pattern="^sneaky_goblin$")
    dry_run: bool | None = Field(default=None, alias="dryRun")

class ActionResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    success: bool
    message: str
    runtime_state: str = Field(alias="runtimeState")
