"""Unit tests for scrapers/method_details.py prose normalizer."""

from __future__ import annotations

import pytest
from method_details import normalize_method_details

from homestretch_data.models import Method


@pytest.mark.parametrize(
    "method,raw",
    [
        (Method.GIFT, None),
        (Method.BREEDING, None),
        (Method.RAID, ""),
    ],
)
def test_none_and_empty_return_none(method: Method, raw: str | None) -> None:
    assert normalize_method_details(method, raw) is None


def test_rule7_collapse_when_details_equals_method() -> None:
    assert normalize_method_details(Method.GIFT, "gift") is None
    assert normalize_method_details(Method.BREEDING, "breeding") is None


def test_already_slug_shaped_passes_through() -> None:
    assert normalize_method_details(Method.WILD_ENCOUNTER, "walk") == "walk"
    assert normalize_method_details(Method.RAID, "tera-raid") == "tera-raid"
    assert normalize_method_details(Method.FISHING, "super-rod") == "super-rod"
    assert normalize_method_details(Method.PURCHASE, "game-corner") == "game-corner"


def test_fishing_extracts_rods_in_canonical_order() -> None:
    assert normalize_method_details(Method.FISHING, "Good Rod") == "good-rod"
    assert (
        normalize_method_details(Method.FISHING, "Old Rod, Super Rod, Good Rod")
        == "old-rod, good-rod, super-rod"
    )


def test_fishing_without_rod_returns_none() -> None:
    assert normalize_method_details(Method.FISHING, "{{rt|10|Kalos}}") is None


def test_wild_encounter_recognizes_common_modes() -> None:
    assert (
        normalize_method_details(
            Method.WILD_ENCOUNTER,
            "'''Coronet Highlands:''' massive mass outbreaks<br>",
        )
        == "mass-outbreak"
    )
    assert (
        normalize_method_details(
            Method.WILD_ENCOUNTER, "Space-Time Distortion in [[Obsidian Fieldlands]]"
        )
        == "space-time-distortion"
    )
    assert normalize_method_details(Method.WILD_ENCOUNTER, "SOS encounter only") == "sos-encounter"
    assert normalize_method_details(Method.WILD_ENCOUNTER, "Surfing on {{rt|17|Kanto}}") == "surf"
    assert normalize_method_details(Method.WILD_ENCOUNTER, "Overworld on Route 2") == "overworld"
    assert normalize_method_details(Method.WILD_ENCOUNTER, "Grass on {{rt|1|Kanto}}") == "walk"


def test_wild_encounter_unrecognized_prose_returns_none() -> None:
    assert normalize_method_details(Method.WILD_ENCOUNTER, "[[Dusty Bowl]]") is None


def test_raid_extracts_specific_kind_before_tier() -> None:
    assert normalize_method_details(Method.RAID, "Gigantamax Max Raid Battle") == "gmax"
    assert (
        normalize_method_details(Method.RAID, "Dynamax Adventure in Crown Tundra")
        == "dynamax-adventure"
    )
    assert normalize_method_details(Method.RAID, "5-star Tera Raid Battle") == "tera-raid"


def test_raid_falls_back_to_lowest_tier() -> None:
    assert normalize_method_details(Method.RAID, "3★ and 4★ in the Wild Area") == "3-star"


def test_raid_no_signal_returns_none() -> None:
    assert normalize_method_details(Method.RAID, "[[Wild Area]]") is None


def test_static_encounter_recognizes_items() -> None:
    # `only-one` is treated as vacuous (every static is one-time by
    # definition) — same generalization as rule 7. Drops to None whether
    # the input arrives as prose or as a pre-slugified value.
    assert normalize_method_details(Method.STATIC_ENCOUNTER, "Only one can be caught") is None
    assert normalize_method_details(Method.STATIC_ENCOUNTER, "only-one") is None
    assert normalize_method_details(Method.STATIC_ENCOUNTER, "Use the Poké Flute") == "pokeflute"
    assert normalize_method_details(Method.STATIC_ENCOUNTER, "Roaming around Kanto") == "roaming"


def test_wiki_markup_stripped_before_recognizer() -> None:
    raw = "{{rt|10|Galar}}, Dusty Bowl '''(Overworld)'''<br>"
    assert normalize_method_details(Method.WILD_ENCOUNTER, raw) == "overworld"


def test_prose_for_other_methods_drops_to_none() -> None:
    assert normalize_method_details(Method.GIFT, "Received from {{color|red|Professor}}") is None
    assert normalize_method_details(Method.EVENT, "{{tt|Distributed|2020}} in Japan") is None
