from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import trial_flow_controller as flow_module
from trial_flow_controller import TrialFlowController
from decision_engine import load_bot_config, Decision
from screen_detector import ScreenState


@pytest.mark.parametrize("count", range(10, 18))
@pytest.mark.parametrize("edge,points", [
    ("DA", range(1, 11)), ("AB", range(11, 21)),
    ("BC", range(21, 30)), ("CD", range(30, 36)),
])
def test_dragon_finishes_each_pass_before_reusing_points(monkeypatch, count, edge, points):
    actions = [SimpleNamespace(sequence_number=i, x=100+i, y=200) for i in range(1, 36)]
    plan = SimpleNamespace(actions=actions, valid=True, screenshot_width=1920, screenshot_height=1080)
    planner = Mock()
    planner.plan_attack.return_value = SimpleNamespace(
        attack_plan=plan, troop_slot_result=SimpleNamespace(bounding_box=(1, 2, 3, 4))
    )
    monkeypatch.setattr(flow_module, "SneakyGoblinPlanner", lambda: planner)
    end = Mock()
    end.return_value.run.return_value = 0
    monkeypatch.setattr(flow_module, "BattleEndController", end)
    controller = TrialFlowController.__new__(TrialFlowController)
    controller.bot_config = SimpleNamespace(
        dragon_count=count, new_base_timeout_seconds=20,
        dragon_post_deployment_wait_seconds_options=(40, 41, 42, 43, 44, 45),
        screen_transition_poll_seconds_options=(0.2,),
    )
    controller.screen_threshold = 0.85
    controller.adb_controller = Mock()
    controller.package_name = "test.package"
    controller.control = Mock()
    for name in ("_assert_game_ready", "_tap_slot", "_validate_deployment_point", "_deploy_action_round", "_wait_with_checkpoints"):
        setattr(controller, name, Mock())

    assert controller._deploy_dragons_on_edge_then_end_battle(edge) == 0
    deployed, phase = controller._deploy_action_round.call_args.args
    order = [action.sequence_number for action in deployed]
    assert len(order) == count
    for start in range(0, count, len(points)):
        current_pass = order[start:start + len(points)]
        assert len(set(current_pass)) == len(current_pass)
        assert set(current_pass) <= set(points)
        if len(current_pass) == len(points):
            assert set(current_pass) == set(points)
    assert phase == f"DEPLOY_DRAGONS_{edge}"
    controller._tap_slot.assert_called_once()
    telemetry = controller.control.report.call_args.kwargs["attackPlan"]
    assert telemetry["points"] == order
    assert telemetry["plannedActionCount"] == count
    assert telemetry["deploymentPointCount"] == len(points)
    assert telemetry["passes"] == [order[start:start + len(points)] for start in range(0, count, len(points))]
    delay, phase = controller._wait_with_checkpoints.call_args.args
    assert delay in (40, 41, 42, 43, 44, 45)
    assert phase == "POST_DEPLOYMENT_WAIT"
    controller.adb_controller.assert_not_called()


@pytest.mark.parametrize("attack", [True, False])
def test_main_dragon_flow_requires_resource_attack(config_path, monkeypatch, attack):
    controller = TrialFlowController(
        adb_controller=Mock(), resource_reader=Mock(), bot_config=load_bot_config(config_path),
        package_name="test", screen_threshold=0.85, battlefield_diff_threshold=0.05,
        dry_run=False, random_dragon_setup=True,
    )
    controller._capture_and_detect = Mock(return_value=SimpleNamespace(state=ScreenState.HOME))
    for name in ("_tap_detection", "_wait_for_state", "_wait_for_enemy_base_to_settle", "_wait_for_different_enemy_base"):
        setattr(controller, name, Mock())
    controller._read_and_decide = Mock(return_value=SimpleNamespace(decision=Decision.ATTACK if attack else Decision.SKIP))
    controller._deploy_dragons_on_edge_then_end_battle = Mock(return_value=0)
    monkeypatch.setattr(flow_module, "build_battlefield_fingerprint", Mock())
    assert controller.run() == 0
    assert controller._read_and_decide.called
    if attack:
        controller._deploy_dragons_on_edge_then_end_battle.assert_called_once()
        edge = controller._deploy_dragons_on_edge_then_end_battle.call_args.args[0]
        assert edge in ("DA", "AB", "BC", "CD")
        assert controller.selected_setup == f"dragon_{edge.lower()}"
    else:
        controller._deploy_dragons_on_edge_then_end_battle.assert_not_called()
        assert controller.selected_setup is None
