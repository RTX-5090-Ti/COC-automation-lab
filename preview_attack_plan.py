from __future__ import annotations

import argparse
from pathlib import Path

import cv2

from adb_controller import ADBController
from decision_engine import load_bot_config
from screen_detector import BoundingBox
from strategies.attack_plan import ActionType, AttackAction, AttackPlan, save_attack_plan_debug_image
from strategies.sneaky_goblin import SneakyGoblinPlanner


DEFAULT_SOURCE_PATH = Path("screenshots/debug/enemy_base_preview_source.png")
DEFAULT_OUTPUT_PATH = Path("screenshots/debug/attack_plan_sneaky_goblin.png")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render the Sneaky Goblin deployment preview without gameplay actions.")
    parser.add_argument("--capture", action="store_true", help="Capture the current emulator screen as the preview source.")
    parser.add_argument("--adb-path", help="Optional explicit path to the adb executable.")
    parser.add_argument("--device-id", help="Optional explicit device id.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE_PATH, help="Screenshot to render.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="Preview image output path.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    source_path: Path = args.source

    if args.capture:
        controller = ADBController(adb_path=args.adb_path, device_id=args.device_id)
        controller.select_device(preferred_serial=args.device_id)
        controller.capture_screenshot(source_path)
        print(f"Captured preview source: {source_path.as_posix()}")

    image = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
    if image is None:
        raise SystemExit(
            f"Preview source does not exist: {source_path}. "
            "Open an enemy base, then run this command once with --capture."
        )

    height, width = image.shape[:2]
    config = load_bot_config()
    planner = SneakyGoblinPlanner()
    battlefield_roi = planner._build_battlefield_roi(width, height, config)
    battlefield_polygon = planner._build_battlefield_polygon(width, height, config)
    excluded_regions = planner._build_excluded_regions(width, height, config)
    selected_points = planner._build_edge_deployment_points(
        battlefield_polygon=battlefield_polygon,
        screenshot_width=width,
        screenshot_height=height,
        excluded_regions=excluded_regions,
        inset_pixels=config.deployment_edge_inset_pixels,
        edge_point_counts=(
            config.deployment_points_da,
            config.deployment_points_ab,
            config.deployment_points_bc,
            config.deployment_points_cd,
        ),
        guide_ratios=(
            config.debug_boundary_da_end_ratio,
            config.debug_boundary_bh_length_ratio,
            config.debug_boundary_bk_length_ratio,
        ),
    )[: config.planned_deployment_points]

    actions: list[AttackAction] = []
    for edge_name, x, y in selected_points:
        if x < 0 or y < 0 or x >= width or y >= height:
            continue
        if edge_name == "C-D" and not planner._is_valid_point(
            x=x,
            y=y,
            screenshot_width=width,
            screenshot_height=height,
            battlefield_roi=battlefield_roi,
            battlefield_polygon=battlefield_polygon,
            excluded_regions=excluded_regions,
        ):
            continue
        actions.append(
            AttackAction(
                sequence_number=len(actions) + 1,
                action_type=ActionType.DEPLOY_GROUP,
                x=x,
                y=y,
                amount=config.goblins_per_point,
                delay_after_seconds=config.delay_between_groups_seconds,
                description=f"Preview deployment near edge {edge_name}.",
            )
        )

    attack_plan = AttackPlan(
        strategy_name=config.sneaky_goblin_mode,
        valid=True,
        actions=actions,
        screenshot_width=width,
        screenshot_height=height,
        troop_slot_center=None,
        error_message=None,
    )
    output_path = save_attack_plan_debug_image(
        screenshot_path=source_path,
        output_path=args.output,
        battlefield_roi=battlefield_roi,
        battlefield_polygon=battlefield_polygon,
        excluded_regions=excluded_regions,
        troop_slot_box=None,
        attack_plan=attack_plan,
        debug_boundary_da_end_ratio=config.debug_boundary_da_end_ratio,
        debug_boundary_bh_length_ratio=config.debug_boundary_bh_length_ratio,
        debug_boundary_bk_length_ratio=config.debug_boundary_bk_length_ratio,
    )
    print(f"Preview generated: {output_path.as_posix()} ({len(actions)} valid deployment points)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
