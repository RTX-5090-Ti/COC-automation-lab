from __future__ import annotations

from trial_flow_controller import TrialFlowController


def test_random_setup_excludes_a_setup_after_two_consecutive_uses(monkeypatch) -> None:
    controller = object.__new__(TrialFlowController)
    controller.setup_history = ["setup_1", "setup_1"]
    controller.selected_setup = None
    captured_candidates: list[tuple[str, ...]] = []

    def choose_last(candidates):
        captured_candidates.append(tuple(candidates))
        return candidates[-1]

    monkeypatch.setattr("trial_flow_controller.random.choice", choose_last)

    selected = controller._select_random_setup()

    assert "setup_1" not in captured_candidates[0]
    assert selected == "setup_8"
    assert controller.setup_history == ["setup_1", "setup_1", "setup_8"]
