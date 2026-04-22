"""Unit tests for scrapers/evolution_details.py.

Each test shapes a minimal PokéAPI-style evolution_detail dict for a
canonical example and asserts the extracted Source kwargs."""

from __future__ import annotations

from evolution_details import (
    EVOLUTION_PATH_GAMES,
    ITEM_TO_GAMES,
    KNOWN_TRIGGERS,
    LOCATION_TO_GAMES,
    UNENCODED_BRANCH_FORMS,
    detail_to_source_fields,
    gate_games,
    method_for_trigger,
    resolve_branched_form_ids,
)

from homestretch_data.models import Method


def _named(name: str) -> dict:
    return {"name": name}


def _detail(**overrides) -> dict:
    base = {
        "trigger": None,
        "item": None,
        "held_item": None,
        "location": None,
        "known_move": None,
        "known_move_type": None,
        "trade_species": None,
        "party_species": None,
        "party_type": None,
        "time_of_day": "",
        "gender": None,
        "min_happiness": None,
        "min_affection": None,
        "min_beauty": None,
        "relative_physical_stats": None,
        "needs_overworld_rain": False,
        "turn_upside_down": False,
        "needs_multiplayer": False,
    }
    base.update(overrides)
    return base


def test_scizor_trade_with_held_metal_coat() -> None:
    fields = detail_to_source_fields(
        _detail(trigger=_named("trade"), held_item=_named("metal-coat")),
    )
    assert fields == {"method_details": "trade", "held_item": "metal-coat"}
    assert method_for_trigger("trade") is Method.TRADE


def test_politoed_trade_with_kings_rock() -> None:
    fields = detail_to_source_fields(
        _detail(trigger=_named("trade"), held_item=_named("kings-rock")),
    )
    assert fields == {"method_details": "trade", "held_item": "kings-rock"}


def test_crabominable_level_up_at_mount_lanakila() -> None:
    fields = detail_to_source_fields(
        _detail(trigger=_named("level-up"), location=_named("mount-lanakila")),
    )
    assert fields == {"method_details": "level-up", "location": "mount-lanakila"}
    # Per-game gating: only Gen 7 games have Mount Lanakila.
    scope = gate_games(
        fields, {"sun", "moon", "ultra-sun", "ultra-moon", "scarlet"}, "crabominable"
    )
    assert scope == {"sun", "moon", "ultra-sun", "ultra-moon"}


def test_crabominable_ice_stone_path_is_gen9_only() -> None:
    fields = detail_to_source_fields(
        _detail(trigger=_named("use-item"), item=_named("ice-stone")),
    )
    assert fields == {"method_details": "use-item", "item": "ice-stone"}
    # SM/USUM have Ice Stone the item but not this evolution path.
    scope = gate_games(
        fields,
        {"sun", "moon", "ultra-sun", "ultra-moon", "scarlet", "violet", "sword"},
        "crabominable",
    )
    assert scope == {"scarlet", "violet"}


def test_glaceon_ice_stone_path_is_gen8_plus_excluding_bdsp() -> None:
    fields = detail_to_source_fields(
        _detail(trigger=_named("use-item"), item=_named("ice-stone")),
    )
    scope = gate_games(
        fields,
        {
            "x",
            "y",
            "moon",
            "sword",
            "shield",
            "brilliant-diamond",
            "shining-pearl",
            "scarlet",
            "violet",
        },
        "glaceon",
    )
    # BDSP retained Route 217 Ice Rock; stone method never ported.
    assert scope == {"sword", "shield", "scarlet", "violet"}


def test_magnezone_thunder_stone_path_excludes_bdsp() -> None:
    fields = detail_to_source_fields(
        _detail(trigger=_named("use-item"), item=_named("thunder-stone")),
    )
    scope = gate_games(
        fields,
        {"sword", "brilliant-diamond", "shining-pearl", "scarlet"},
        "magnezone",
    )
    assert scope == {"sword", "scarlet"}


def test_unrestricted_form_item_pair_passes_through() -> None:
    # Ninetales via fire-stone is a Gen 1 path; no EVOLUTION_PATH_GAMES entry.
    fields = {"method_details": "use-item", "item": "fire-stone"}
    scope = gate_games(fields, {"red", "blue", "x", "scarlet"}, "ninetales")
    assert scope == {"red", "blue", "x", "scarlet"}


def test_inkay_turn_upside_down() -> None:
    fields = detail_to_source_fields(
        _detail(trigger=_named("level-up"), turn_upside_down=True),
    )
    assert fields == {"method_details": "level-up", "turn_upside_down": True}


def test_tyrogue_relative_stats_branch() -> None:
    gt = detail_to_source_fields(
        _detail(trigger=_named("level-up"), relative_physical_stats=1),
    )
    eq = detail_to_source_fields(
        _detail(trigger=_named("level-up"), relative_physical_stats=0),
    )
    lt = detail_to_source_fields(
        _detail(trigger=_named("level-up"), relative_physical_stats=-1),
    )
    assert gt["relative_physical_stats"] == "atk-gt-def"
    assert eq["relative_physical_stats"] == "atk-eq-def"
    assert lt["relative_physical_stats"] == "atk-lt-def"


def test_sylveon_known_move_type_with_affection() -> None:
    fields = detail_to_source_fields(
        _detail(
            trigger=_named("level-up"),
            known_move_type=_named("fairy"),
            min_affection=2,
        ),
    )
    assert fields == {
        "method_details": "level-up",
        "known_move_type": "fairy",
        "min_affection": 2,
    }


def test_mantyke_party_species() -> None:
    fields = detail_to_source_fields(
        _detail(trigger=_named("level-up"), party_species=_named("remoraid")),
    )
    assert fields == {"method_details": "level-up", "party_species": "remoraid"}


def test_shelmet_trade_species() -> None:
    fields = detail_to_source_fields(
        _detail(trigger=_named("trade"), trade_species=_named("karrablast")),
    )
    assert fields == {"method_details": "trade", "trade_species": "karrablast"}


def test_eevee_espeon_day_happiness() -> None:
    fields = detail_to_source_fields(
        _detail(trigger=_named("level-up"), time_of_day="day", min_happiness=160),
    )
    assert fields == {
        "method_details": "level-up",
        "time_of_day": "day",
        "min_happiness": 160,
    }


def test_kirlia_gallade_male_only() -> None:
    fields = detail_to_source_fields(
        _detail(trigger=_named("use-item"), item=_named("dawn-stone"), gender=2),
    )
    assert fields["gender"] == "male"
    assert fields["item"] == "dawn-stone"


def test_milotic_beauty() -> None:
    fields = detail_to_source_fields(
        _detail(trigger=_named("level-up"), min_beauty=170),
    )
    assert fields == {"method_details": "level-up", "min_beauty": 170}


def test_null_only_fields_are_omitted() -> None:
    fields = detail_to_source_fields(_detail(trigger=_named("level-up")))
    assert fields == {"method_details": "level-up"}


def test_known_triggers_cover_canonical_names() -> None:
    # Spot-check that the set hasn't drifted below the minimum expected.
    required = {"level-up", "trade", "use-item", "shed", "spin"}
    assert required <= KNOWN_TRIGGERS


def test_item_to_games_covers_regional_exclusives() -> None:
    assert ITEM_TO_GAMES["black-augurite"] == frozenset({"legends-arceus"})
    assert ITEM_TO_GAMES["galarica-cuff"] == frozenset({"sword", "shield"})
    assert "scarlet" in ITEM_TO_GAMES["auspicious-armor"]


def test_location_to_games_covers_gen7_and_gen8() -> None:
    assert LOCATION_TO_GAMES["mount-lanakila"] == frozenset(
        {"sun", "moon", "ultra-sun", "ultra-moon"}
    )
    assert LOCATION_TO_GAMES["tower-of-darkness"] == frozenset({"sword", "shield"})


def test_gate_games_intersects_with_item_restriction() -> None:
    fields = {"item": "black-augurite"}
    scope = gate_games(fields, {"legends-arceus", "scarlet", "sword"}, "kleavor")
    assert scope == {"legends-arceus"}


def test_gate_games_passthrough_for_unknown_location() -> None:
    fields = {"location": "some-new-location"}
    scope = gate_games(fields, {"scarlet", "violet"}, "anything")
    assert scope == {"scarlet", "violet"}


def test_location_to_games_covers_problematic_locations() -> None:
    # Locations where a path is restricted below the species scope.
    assert LOCATION_TO_GAMES["frost-cavern"] == frozenset({"x", "y"})
    assert LOCATION_TO_GAMES["sinnoh-route-217"] == frozenset(
        {"brilliant-diamond", "shining-pearl"}
    )
    assert LOCATION_TO_GAMES["blush-mountain"] == frozenset(
        {"sun", "moon", "ultra-sun", "ultra-moon"}
    )
    # BW/BW2 locations map to empty — out of scope.
    assert LOCATION_TO_GAMES["chargestone-cave"] == frozenset()
    assert LOCATION_TO_GAMES["twist-mountain"] == frozenset()


def test_evolution_path_games_covers_late_added_stone_paths() -> None:
    # Sanity: five expected entries exist.
    for pair in [
        ("crabominable", "ice-stone"),
        ("glaceon", "ice-stone"),
        ("leafeon", "leaf-stone"),
        ("magnezone", "thunder-stone"),
        ("probopass", "thunder-stone"),
    ]:
        assert pair in EVOLUTION_PATH_GAMES, pair


_BRANCHED_VALID_FORMS = frozenset(
    {
        "urshifu",
        "urshifu-rapid-strike",
        "lycanroc",
        "lycanroc-midnight",
        "lycanroc-dusk",
        "basculegion",
        "basculegion-female",
        "wormadam",
        "wormadam-sandy",
        "wormadam-trash",
        "gourgeist",
        "gourgeist-small",
        "gourgeist-large",
        "gourgeist-super",
        "toxtricity",
        "toxtricity-low-key",
        "dudunsparce",
        "dudunsparce-three-segment",
        "pikachu",  # non-branched control
    }
)


def test_resolve_urshifu_tower_of_waters_routes_to_rapid_strike() -> None:
    detail = _detail(trigger=_named("tower-of-waters"))
    assert resolve_branched_form_ids("urshifu", detail, set(_BRANCHED_VALID_FORMS)) == {
        "urshifu-rapid-strike"
    }


def test_resolve_urshifu_tower_of_darkness_routes_to_default() -> None:
    detail = _detail(trigger=_named("tower-of-darkness"))
    assert resolve_branched_form_ids("urshifu", detail, set(_BRANCHED_VALID_FORMS)) == {"urshifu"}


def test_resolve_lycanroc_by_time_of_day() -> None:
    valid = set(_BRANCHED_VALID_FORMS)
    assert resolve_branched_form_ids("lycanroc", _detail(time_of_day="day"), valid) == {"lycanroc"}
    assert resolve_branched_form_ids("lycanroc", _detail(time_of_day="night"), valid) == {
        "lycanroc-midnight"
    }
    assert resolve_branched_form_ids("lycanroc", _detail(time_of_day="dusk"), valid) == {
        "lycanroc-dusk"
    }


def test_resolve_basculegion_fans_out_both_genders() -> None:
    # PokeAPI leaves gender=null on the basculin → basculegion detail,
    # so we emit for both forms and let pre-evolution gender decide.
    valid = set(_BRANCHED_VALID_FORMS)
    assert resolve_branched_form_ids(
        "basculegion", _detail(trigger=_named("recoil-damage")), valid
    ) == {"basculegion", "basculegion-female"}


def test_resolve_unencoded_branches_fan_out() -> None:
    valid = set(_BRANCHED_VALID_FORMS)
    detail = _detail(trigger=_named("level-up"))
    assert resolve_branched_form_ids("wormadam", detail, valid) == {
        "wormadam",
        "wormadam-sandy",
        "wormadam-trash",
    }
    assert resolve_branched_form_ids("gourgeist", detail, valid) == {
        "gourgeist",
        "gourgeist-small",
        "gourgeist-large",
        "gourgeist-super",
    }
    assert resolve_branched_form_ids("toxtricity", detail, valid) == {
        "toxtricity",
        "toxtricity-low-key",
    }
    assert resolve_branched_form_ids("dudunsparce", detail, valid) == {
        "dudunsparce",
        "dudunsparce-three-segment",
    }


def test_resolve_non_branched_species_returns_default_only() -> None:
    assert resolve_branched_form_ids(
        "pikachu", _detail(trigger=_named("level-up")), set(_BRANCHED_VALID_FORMS)
    ) == {"pikachu"}


def test_resolve_filters_out_forms_missing_from_valid_set() -> None:
    # If the branched form isn't in forms.json, it shouldn't emit a row for it.
    valid = {"wormadam", "wormadam-sandy"}  # trash intentionally missing
    assert resolve_branched_form_ids("wormadam", _detail(trigger=_named("level-up")), valid) == {
        "wormadam",
        "wormadam-sandy",
    }


def test_unencoded_branch_forms_table_matches_forms_file_species() -> None:
    # Regression guard: if someone adds a branch, they should extend the table.
    assert set(UNENCODED_BRANCH_FORMS) == {
        "wormadam",
        "gourgeist",
        "toxtricity",
        "dudunsparce",
        "basculegion",
    }
