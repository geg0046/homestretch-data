"""Unit tests for scrapers/bulbapedia.py's form-annotation resolution.

Bulbapedia species pages pack availability for every regional variant
into one ===Game locations=== section, disambiguating via bold
annotations like `'''Galarian Form'''`. `resolve_form_ids_from_segment`
and `split_area_segments` are the two helpers the wikitext parser
relies on."""

from __future__ import annotations

from bulbapedia import resolve_form_ids_from_segment, split_area_segments


def test_split_area_segments_handles_br_variants() -> None:
    assert split_area_segments("a<br>b<br/>c<br />d") == ["a", "b", "c", "d"]
    assert split_area_segments("one path only") == ["one path only"]
    assert split_area_segments("<br><br>") == []


def test_unannotated_segment_falls_back_to_default_form() -> None:
    forms = {"pikachu"}
    assert resolve_form_ids_from_segment("Route 4", "pikachu", forms) == ["pikachu"]


def test_unannotated_segment_without_default_returns_empty() -> None:
    # A species whose default form_id isn't in forms.json has nothing to
    # anchor unannotated segments to.
    assert resolve_form_ids_from_segment("Route 4", "pikachu", set()) == []


def test_kantonian_annotation_routes_to_default() -> None:
    forms = {"meowth", "meowth-alola", "meowth-galar"}
    segment = "Route 7 <small>('''Kantonian Form''')</small>"
    assert resolve_form_ids_from_segment(segment, "meowth", forms) == ["meowth"]


def test_galarian_annotation_routes_to_regional_form() -> None:
    forms = {"meowth", "meowth-alola", "meowth-galar"}
    segment = "Route 4 <small>('''Galarian Form''')</small>"
    assert resolve_form_ids_from_segment(segment, "meowth", forms) == ["meowth-galar"]


def test_alolan_and_hisuian_annotations() -> None:
    forms = {"ninetales", "ninetales-alola"}
    assert resolve_form_ids_from_segment(
        "Snowslide Slope '''Alolan Form'''", "ninetales", forms
    ) == ["ninetales-alola"]

    forms = {"growlithe", "growlithe-hisui"}
    assert resolve_form_ids_from_segment(
        "Cobalt Coastlands '''Hisuian Form'''", "growlithe", forms
    ) == ["growlithe-hisui"]


def test_slash_combined_regions_emit_both() -> None:
    forms = {"meowth", "meowth-alola", "meowth-galar"}
    segment = "Trade <small>('''Alolan/Galarian Forms''')</small>"
    assert resolve_form_ids_from_segment(segment, "meowth", forms) == [
        "meowth-alola",
        "meowth-galar",
    ]


def test_paldean_tauros_single_breed() -> None:
    forms = {
        "tauros",
        "tauros-paldea-aqua-breed",
        "tauros-paldea-blaze-breed",
        "tauros-paldea-combat-breed",
    }
    segment = "Union Circle <small>('''Paldean Form (Aqua Breed)''')</small>"
    assert resolve_form_ids_from_segment(segment, "tauros", forms) == ["tauros-paldea-aqua-breed"]


def test_paldean_tauros_combined_breeds() -> None:
    forms = {
        "tauros",
        "tauros-paldea-aqua-breed",
        "tauros-paldea-blaze-breed",
        "tauros-paldea-combat-breed",
    }
    segment = "Area Two <small>('''Paldean Form (Combat and Blaze Breeds)''')</small>"
    assert set(resolve_form_ids_from_segment(segment, "tauros", forms)) == {
        "tauros-paldea-combat-breed",
        "tauros-paldea-blaze-breed",
    }


def test_paldean_wooper_without_breed() -> None:
    # Wooper has no breed variants, just wooper-paldea.
    forms = {"wooper", "wooper-paldea"}
    segment = "Paldean Sea '''Paldean Form'''"
    assert resolve_form_ids_from_segment(segment, "wooper", forms) == ["wooper-paldea"]


def test_all_forms_annotation_returns_every_form() -> None:
    forms = {"meowth", "meowth-alola", "meowth-galar"}
    segment = "Hyperspace Lumiose <small>('''All Forms''')</small>"
    assert set(resolve_form_ids_from_segment(segment, "meowth", forms)) == forms


def test_annotation_for_form_missing_from_forms_json_skipped() -> None:
    # If the page annotates a region we don't track (e.g. Johtonian → default,
    # but no default form in the input set), the resolver skips it and
    # returns empty — not a bogus form_id.
    forms: set[str] = set()
    segment = "Some area '''Galarian Form'''"
    assert resolve_form_ids_from_segment(segment, "meowth", forms) == []


def test_multiple_annotations_in_one_segment() -> None:
    # Rare but possible: two bold annotations side-by-side.
    forms = {"meowth", "meowth-alola", "meowth-galar"}
    segment = "Event '''Alolan Form''' and '''Galarian Form'''"
    assert set(resolve_form_ids_from_segment(segment, "meowth", forms)) == {
        "meowth-alola",
        "meowth-galar",
    }


def test_regional_form_with_extra_mode_suffix_resolves_uniquely() -> None:
    # Darmanitan stores only one Galarian variant in forms.json
    # (darmanitan-galar-standard; the zen variant is battle-only). A
    # "Galarian Form" annotation without a mode should route there.
    forms = {"darmanitan", "darmanitan-galar-standard"}
    segment = "Route 8 '''Galarian Form'''"
    assert resolve_form_ids_from_segment(segment, "darmanitan", forms) == [
        "darmanitan-galar-standard"
    ]
