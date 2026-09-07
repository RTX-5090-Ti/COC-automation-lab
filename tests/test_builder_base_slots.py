from builder_base_slots import builder_army_slots, save_builder_army_slots_debug


def test_builder_army_slots_match_the_reference_ui() -> None:
    troops = builder_army_slots((1920, 1080))

    assert [slot.kind for slot in troops] == ["troop"] * 7
    assert [slot.center for slot in troops] == [
        (200, 944), (360, 935), (511, 935), (663, 935), (814, 935), (966, 935), (1123, 935),
    ]


def test_builder_army_slot_debug_image_is_saved(tmp_path) -> None:
    output = save_builder_army_slots_debug(
        "screenshots/debug/builder_enemy_base.png",
        tmp_path / "builder_slots.png",
    )

    assert output.is_file()
