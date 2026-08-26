from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from resource_reader import ResourceReadResult


CONFIG_PATH = Path("config/bot_config.json")


class DecisionEngineError(Exception):
    """Raised when bot configuration is missing or invalid."""


class Decision(str, Enum):
    """Possible outcomes for resource-based filtering."""

    ATTACK = "ATTACK"
    SKIP = "SKIP"
    UNDECIDED = "UNDECIDED"


@dataclass(frozen=True)
class BotConfig:
    """Validated decision thresholds."""

    minimum_gold: int
    minimum_elixir: int
    minimum_dark_elixir: int
    require_all_resources: bool
    dry_run: bool
    max_bases_to_check: int
    max_next_taps: int
    max_ocr_attempts_per_base: int
    max_unknown_state_retries: int
    unknown_retry_delay_seconds: float
    new_base_timeout_seconds: float
    max_runtime_seconds: float
    strategy: str
    sneaky_goblin_mode: str
    sneaky_goblin_slot_threshold: float
    battlefield_left_ratio: float
    battlefield_right_ratio: float
    battlefield_top_ratio: float
    battlefield_bottom_ratio: float
    battlefield_diamond_top_x_ratio: float
    battlefield_diamond_top_y_ratio: float
    battlefield_diamond_right_x_ratio: float
    battlefield_diamond_right_y_ratio: float
    battlefield_diamond_bottom_x_ratio: float
    battlefield_diamond_bottom_y_ratio: float
    battlefield_diamond_left_x_ratio: float
    battlefield_diamond_left_y_ratio: float
    next_button_exclude_left_ratio: float
    next_button_exclude_right_ratio: float
    next_button_exclude_top_ratio: float
    next_button_exclude_bottom_ratio: float
    top_ui_exclude_top_ratio: float
    top_ui_exclude_bottom_ratio: float
    bottom_ui_exclude_top_ratio: float
    planned_deployment_points: int
    deployment_edge_inset_pixels: int
    goblins_per_point: int
    delay_between_groups_seconds: float
    maximum_planned_actions: int


@dataclass(frozen=True)
class DecisionResult:
    """Typed result returned by the decision engine."""

    decision: Decision
    detected_gold: int | None
    detected_elixir: int | None
    detected_dark_elixir: int | None
    minimum_gold: int
    minimum_elixir: int
    minimum_dark_elixir: int
    gold_passed: bool | None
    elixir_passed: bool | None
    dark_elixir_passed: bool | None
    reasons: list[str]


DEFAULT_CONFIG = {
    "minimumGold": 500000,
    "minimumElixir": 500000,
    "minimumDarkElixir": 5000,
    "requireAllResources": True,
    "dryRun": True,
    "maxBasesToCheck": 5,
    "maxNextTaps": 4,
    "maxOcrAttemptsPerBase": 2,
    "maxUnknownStateRetries": 3,
    "unknownRetryDelaySeconds": 1.0,
    "newBaseTimeoutSeconds": 20.0,
    "maxRuntimeSeconds": 180.0,
    "strategy": "sneaky_goblin",
    "sneakyGoblinMode": "perimeter_sweep",
    "sneakyGoblinSlotThreshold": 0.85,
    "battlefieldLeftRatio": 0.08,
    "battlefieldRightRatio": 0.92,
    "battlefieldTopRatio": 0.15,
    "battlefieldBottomRatio": 0.72,
    "battlefieldDiamondTopXRatio": 0.50,
    "battlefieldDiamondTopYRatio": 0.02,
    "battlefieldDiamondRightXRatio": 0.90,
    "battlefieldDiamondRightYRatio": 0.47,
    "battlefieldDiamondBottomXRatio": 0.50,
    "battlefieldDiamondBottomYRatio": 0.93,
    "battlefieldDiamondLeftXRatio": 0.12,
    "battlefieldDiamondLeftYRatio": 0.47,
    "nextButtonExcludeLeftRatio": 0.83,
    "nextButtonExcludeRightRatio": 0.98,
    "nextButtonExcludeTopRatio": 0.60,
    "nextButtonExcludeBottomRatio": 0.86,
    "topUiExcludeTopRatio": 0.0,
    "topUiExcludeBottomRatio": 0.14,
    "bottomUiExcludeTopRatio": 0.74,
    "plannedDeploymentPoints": 12,
    "deploymentEdgeInsetPixels": 28,
    "goblinsPerPoint": 3,
    "delayBetweenGroupsSeconds": 0.4,
    "maximumPlannedActions": 20,
}


def load_bot_config(config_path: str | Path = CONFIG_PATH) -> BotConfig:
    path = Path(config_path)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")

    try:
        raw_config = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise DecisionEngineError(f"Configuration file is not valid JSON: {path}") from error

    config = BotConfig(
        minimum_gold=_read_non_negative_int(raw_config, "minimumGold"),
        minimum_elixir=_read_non_negative_int(raw_config, "minimumElixir"),
        minimum_dark_elixir=_read_non_negative_int(raw_config, "minimumDarkElixir"),
        require_all_resources=_read_bool(raw_config, "requireAllResources"),
        dry_run=_read_bool(raw_config, "dryRun"),
        max_bases_to_check=_read_int_in_range(raw_config, "maxBasesToCheck", minimum=1, maximum=20),
        max_next_taps=_read_positive_int(raw_config, "maxNextTaps"),
        max_ocr_attempts_per_base=_read_positive_int(raw_config, "maxOcrAttemptsPerBase"),
        max_unknown_state_retries=_read_positive_int(raw_config, "maxUnknownStateRetries"),
        unknown_retry_delay_seconds=_read_positive_float(raw_config, "unknownRetryDelaySeconds"),
        new_base_timeout_seconds=_read_positive_float(raw_config, "newBaseTimeoutSeconds"),
        max_runtime_seconds=_read_positive_float(raw_config, "maxRuntimeSeconds"),
        strategy=_read_non_empty_string(raw_config, "strategy"),
        sneaky_goblin_mode=_read_non_empty_string(raw_config, "sneakyGoblinMode"),
        sneaky_goblin_slot_threshold=_read_ratio(raw_config, "sneakyGoblinSlotThreshold"),
        battlefield_left_ratio=_read_ratio(raw_config, "battlefieldLeftRatio"),
        battlefield_right_ratio=_read_ratio(raw_config, "battlefieldRightRatio"),
        battlefield_top_ratio=_read_ratio(raw_config, "battlefieldTopRatio"),
        battlefield_bottom_ratio=_read_ratio(raw_config, "battlefieldBottomRatio"),
        battlefield_diamond_top_x_ratio=_read_diamond_vertex_ratio(raw_config, "battlefieldDiamondTopXRatio"),
        battlefield_diamond_top_y_ratio=_read_diamond_vertex_ratio(raw_config, "battlefieldDiamondTopYRatio"),
        battlefield_diamond_right_x_ratio=_read_diamond_vertex_ratio(raw_config, "battlefieldDiamondRightXRatio"),
        battlefield_diamond_right_y_ratio=_read_diamond_vertex_ratio(raw_config, "battlefieldDiamondRightYRatio"),
        battlefield_diamond_bottom_x_ratio=_read_diamond_vertex_ratio(raw_config, "battlefieldDiamondBottomXRatio"),
        battlefield_diamond_bottom_y_ratio=_read_diamond_vertex_ratio(raw_config, "battlefieldDiamondBottomYRatio"),
        battlefield_diamond_left_x_ratio=_read_diamond_vertex_ratio(raw_config, "battlefieldDiamondLeftXRatio"),
        battlefield_diamond_left_y_ratio=_read_diamond_vertex_ratio(raw_config, "battlefieldDiamondLeftYRatio"),
        next_button_exclude_left_ratio=_read_ratio(raw_config, "nextButtonExcludeLeftRatio"),
        next_button_exclude_right_ratio=_read_ratio(raw_config, "nextButtonExcludeRightRatio"),
        next_button_exclude_top_ratio=_read_ratio(raw_config, "nextButtonExcludeTopRatio"),
        next_button_exclude_bottom_ratio=_read_ratio(raw_config, "nextButtonExcludeBottomRatio"),
        top_ui_exclude_top_ratio=_read_ratio(raw_config, "topUiExcludeTopRatio"),
        top_ui_exclude_bottom_ratio=_read_ratio(raw_config, "topUiExcludeBottomRatio"),
        bottom_ui_exclude_top_ratio=_read_ratio(raw_config, "bottomUiExcludeTopRatio"),
        planned_deployment_points=_read_int_in_range(raw_config, "plannedDeploymentPoints", minimum=1, maximum=12),
        deployment_edge_inset_pixels=_read_int_in_range(
            raw_config, "deploymentEdgeInsetPixels", minimum=1, maximum=200
        ),
        goblins_per_point=_read_int_in_range(raw_config, "goblinsPerPoint", minimum=1, maximum=10),
        delay_between_groups_seconds=_read_non_negative_float(raw_config, "delayBetweenGroupsSeconds"),
        maximum_planned_actions=_read_int_in_range(raw_config, "maximumPlannedActions", minimum=1, maximum=50),
    )
    _validate_limit_relationships(config)
    return config


def evaluate_resources(resource_result: ResourceReadResult, config: BotConfig) -> DecisionResult:
    gold_value = resource_result.gold.value
    elixir_value = resource_result.elixir.value
    dark_elixir_value = resource_result.dark_elixir.value

    reasons: list[str] = []

    gold_required = config.minimum_gold > 0
    elixir_required = config.minimum_elixir > 0
    dark_elixir_required = config.minimum_dark_elixir > 0

    gold_passed = _evaluate_single_resource(gold_value, config.minimum_gold)
    elixir_passed = _evaluate_single_resource(elixir_value, config.minimum_elixir)
    dark_elixir_passed = _evaluate_single_resource(dark_elixir_value, config.minimum_dark_elixir)

    missing_required = []
    if gold_required and gold_value is None:
        missing_required.append("Gold reading is unavailable")
    if elixir_required and elixir_value is None:
        missing_required.append("Elixir reading is unavailable")
    if dark_elixir_required and dark_elixir_value is None:
        missing_required.append("Dark Elixir reading is unavailable")

    if missing_required:
        reasons.extend(missing_required)
        return DecisionResult(
            decision=Decision.UNDECIDED,
            detected_gold=gold_value,
            detected_elixir=elixir_value,
            detected_dark_elixir=dark_elixir_value,
            minimum_gold=config.minimum_gold,
            minimum_elixir=config.minimum_elixir,
            minimum_dark_elixir=config.minimum_dark_elixir,
            gold_passed=gold_passed,
            elixir_passed=elixir_passed,
            dark_elixir_passed=dark_elixir_passed,
            reasons=reasons,
        )

    reasons.extend(_build_pass_fail_reasons("Gold", gold_passed, gold_required))
    reasons.extend(_build_pass_fail_reasons("Elixir", elixir_passed, elixir_required))
    reasons.extend(_build_pass_fail_reasons("Dark Elixir", dark_elixir_passed, dark_elixir_required))

    enabled_results = [
        result
        for required, result in (
            (gold_required, gold_passed),
            (elixir_required, elixir_passed),
            (dark_elixir_required, dark_elixir_passed),
        )
        if required
    ]

    if not enabled_results:
        reasons.append("No resource thresholds are enabled")
        decision = Decision.ATTACK
    elif config.require_all_resources:
        if all(result is True for result in enabled_results):
            decision = Decision.ATTACK
        else:
            decision = Decision.SKIP
            reasons.append("Not all required resource conditions passed")
    else:
        if any(result is True for result in enabled_results):
            decision = Decision.ATTACK
        else:
            decision = Decision.SKIP
            reasons.append("No enabled resource condition passed")

    return DecisionResult(
        decision=decision,
        detected_gold=gold_value,
        detected_elixir=elixir_value,
        detected_dark_elixir=dark_elixir_value,
        minimum_gold=config.minimum_gold,
        minimum_elixir=config.minimum_elixir,
        minimum_dark_elixir=config.minimum_dark_elixir,
        gold_passed=gold_passed,
        elixir_passed=elixir_passed,
        dark_elixir_passed=dark_elixir_passed,
        reasons=reasons,
    )


def _read_non_negative_int(raw_config: dict, key: str) -> int:
    value = raw_config.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DecisionEngineError(f"Configuration value '{key}' must be a non-negative integer.")
    return value


def _read_bool(raw_config: dict, key: str) -> bool:
    value = raw_config.get(key)
    if not isinstance(value, bool):
        raise DecisionEngineError(f"Configuration value '{key}' must be a boolean.")
    return value


def _read_positive_int(raw_config: dict, key: str) -> int:
    value = raw_config.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise DecisionEngineError(f"Configuration value '{key}' must be a positive integer.")
    return value


def _read_int_in_range(raw_config: dict, key: str, *, minimum: int, maximum: int) -> int:
    value = raw_config.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum or value > maximum:
        raise DecisionEngineError(
            f"Configuration value '{key}' must be an integer between {minimum} and {maximum}."
        )
    return value


def _read_positive_float(raw_config: dict, key: str) -> float:
    value = raw_config.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise DecisionEngineError(f"Configuration value '{key}' must be a positive number.")
    return float(value)


def _read_non_negative_float(raw_config: dict, key: str) -> float:
    value = raw_config.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise DecisionEngineError(f"Configuration value '{key}' must be a non-negative number.")
    return float(value)


def _read_ratio(raw_config: dict, key: str) -> float:
    value = raw_config.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 or value > 1:
        raise DecisionEngineError(f"Configuration value '{key}' must be a number between 0 and 1.")
    return float(value)


def _read_diamond_vertex_ratio(raw_config: dict, key: str) -> float:
    value = raw_config.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < -1 or value > 2:
        raise DecisionEngineError(
            f"Configuration value '{key}' must be a number between -1 and 2."
        )
    return float(value)


def _read_non_empty_string(raw_config: dict, key: str) -> str:
    value = raw_config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DecisionEngineError(f"Configuration value '{key}' must be a non-empty string.")
    return value.strip()


def _validate_limit_relationships(config: BotConfig) -> None:
    max_allowed_next_taps = config.max_bases_to_check - 1
    if config.max_next_taps > max_allowed_next_taps:
        raise DecisionEngineError(
            "Configuration value 'maxNextTaps' must not exceed maxBasesToCheck - 1."
        )
    if config.battlefield_left_ratio >= config.battlefield_right_ratio:
        raise DecisionEngineError("Battlefield left ratio must be smaller than battlefield right ratio.")
    if config.battlefield_top_ratio >= config.battlefield_bottom_ratio:
        raise DecisionEngineError("Battlefield top ratio must be smaller than battlefield bottom ratio.")
    if config.next_button_exclude_left_ratio >= config.next_button_exclude_right_ratio:
        raise DecisionEngineError("Next-button exclude left ratio must be smaller than right ratio.")
    if config.next_button_exclude_top_ratio >= config.next_button_exclude_bottom_ratio:
        raise DecisionEngineError("Next-button exclude top ratio must be smaller than bottom ratio.")
    if config.top_ui_exclude_top_ratio > config.top_ui_exclude_bottom_ratio:
        raise DecisionEngineError("Top-UI exclude top ratio must not exceed bottom ratio.")
    if config.maximum_planned_actions < config.planned_deployment_points:
        raise DecisionEngineError(
            "Configuration value 'maximumPlannedActions' must be at least plannedDeploymentPoints."
        )
    if config.strategy != "sneaky_goblin":
        raise DecisionEngineError("Configuration value 'strategy' must currently be 'sneaky_goblin'.")
    if config.sneaky_goblin_mode != "perimeter_sweep":
        raise DecisionEngineError(
            "Configuration value 'sneakyGoblinMode' must currently be 'perimeter_sweep'."
        )


def _evaluate_single_resource(value: int | None, minimum: int) -> bool | None:
    if value is None:
        return None
    if minimum <= 0:
        return True
    return value >= minimum


def _build_pass_fail_reasons(label: str, passed: bool | None, required: bool) -> list[str]:
    if not required:
        return [f"{label} condition disabled"]
    if passed is True:
        return [f"{label} condition passed"]
    if passed is False:
        return [f"{label} condition failed"]
    return []
