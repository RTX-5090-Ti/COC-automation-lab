"""Mode-specific requirements exported by the gameplay controllers."""
from pathlib import Path


def required_template_paths(farm_mode: str) -> tuple[Path, ...]:
    if farm_mode == "builder_base":
        from builder_base_end_controller import required_template_paths as builder_paths
        return builder_paths()
    if farm_mode == "home_village":
        from trial_flow_controller import required_template_paths as home_paths
        return home_paths()
    raise ValueError(f"Unsupported farm mode: {farm_mode}")
