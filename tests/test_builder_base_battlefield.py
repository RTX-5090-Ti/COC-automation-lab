from builder_base_battlefield import builder_battlefield_polygon, builder_deployment_points, save_builder_battlefield_roi_debug
from decision_engine import load_bot_config


def test_builder_battlefield_polygon_uses_its_own_configurable_vertices() -> None:
    config = load_bot_config()
    polygon = builder_battlefield_polygon((1920, 1080), config)

    assert polygon == [
        (round(1920 * config.builder_battlefield_a_x_ratio), round(1080 * config.builder_battlefield_a_y_ratio)),
        (round(1920 * config.builder_battlefield_b_x_ratio), round(1080 * config.builder_battlefield_b_y_ratio)),
        (round(1920 * config.builder_battlefield_c_x_ratio), round(1080 * config.builder_battlefield_c_y_ratio)),
        (round(1920 * config.builder_battlefield_d_x_ratio), round(1080 * config.builder_battlefield_d_y_ratio)),
    ]
    assert all(0 <= value <= 1 for value in (
        config.builder_boundary_de_length_ratio,
        config.builder_boundary_bf_length_ratio,
        config.builder_boundary_bg_length_ratio,
        config.builder_boundary_dh_length_ratio,
    ))


def test_builder_battlefield_debug_image_is_saved(tmp_path) -> None:
    output = save_builder_battlefield_roi_debug(
        "screenshots/debug/builder_enemy_base.png",
        tmp_path / "builder_roi.png",
        load_bot_config(),
    )

    assert output.is_file()


def test_builder_deployment_points_follow_the_requested_edge_counts() -> None:
    points = builder_deployment_points((1920, 1080), load_bot_config())

    assert len(points) == 28
    assert [point.edge for point in points].count("D-E") == 10
    assert [point.edge for point in points].count("B-F") == 10
    assert [point.edge for point in points].count("B-G") == 5
    assert [point.edge for point in points].count("D-H") == 3
