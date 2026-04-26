"""Unit tests for scrapers/bulbapedia.py's form-annotation resolution.

Bulbapedia species pages pack availability for every regional variant
into one ===Game locations=== section, disambiguating via bold
annotations like `'''Galarian Form'''`. `resolve_form_ids_from_segment`
and `split_area_segments` are the two helpers the wikitext parser
relies on."""

from __future__ import annotations

from bulbapedia import (
    _rod_set,
    extract_area_location,
    extract_area_locations,
    resolve_form_ids_from_segment,
    split_area_segments,
)


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


# --- extract_area_location ------------------------------------------------


def test_extract_static_first_wikilink() -> None:
    # Static segments open with the place; first-wikilink path is canonical.
    assert (
        extract_area_location(
            "[[Cerulean Cave]] ([[List of in-game event Pokémon|Only one]])",
            prefer_preposition=False,
        )
        == "cerulean-cave"
    )


def test_extract_wild_route_template() -> None:
    # Wild Availability segments are typically just an area template.
    assert extract_area_location("{{rt|13|Kalos}}", prefer_preposition=False) == "kalos-route-13"


def test_extract_wild_first_wikilink() -> None:
    assert (
        extract_area_location("[[Petalburg Woods]]", prefer_preposition=False) == "petalburg-woods"
    )


def test_extract_gift_prefers_preposition_link() -> None:
    # NPC name comes first; the place follows "in" — preposition path picks
    # the place, not the NPC.
    assert (
        extract_area_location(
            "Received from [[Bill]] in [[Goldenrod City]]", prefer_preposition=True
        )
        == "goldenrod-city"
    )


def test_extract_gift_falls_back_to_first_wikilink_without_preposition() -> None:
    assert (
        extract_area_location("[[Goldenrod City]] gift", prefer_preposition=True)
        == "goldenrod-city"
    )


def test_extract_strips_inline_metadata() -> None:
    # <small>/<sup> footnotes are stripped before location extraction.
    segment = "[[Route 5]]<small>(after E4)</small><sup>[1]</sup>"
    assert extract_area_location(segment, prefer_preposition=False) == "route-5"


def test_extract_drops_trailing_condition_clause() -> None:
    # "after X" / "during Y" trail the location and must not bleed in.
    assert (
        extract_area_location(
            "[[Lake Verity]] after defeating the Elite Four", prefer_preposition=False
        )
        == "lake-verity"
    )


def test_extract_returns_none_for_empty_segment() -> None:
    assert extract_area_location("", prefer_preposition=False) is None
    assert extract_area_location("   ", prefer_preposition=False) is None


def test_extract_returns_none_for_overlong_prose() -> None:
    # The 40-char gate prevents condition prose from leaking as a slug.
    long_prose = "very long prose with no wikilink that exceeds the slug length cap entirely"
    assert extract_area_location(long_prose, prefer_preposition=False) is None


def test_extract_unicode_normalized_to_ascii() -> None:
    # Pokémon-style accents should be NFKD-stripped to ASCII before slugging.
    assert (
        extract_area_location("[[Pokémon Mansion]]", prefer_preposition=False) == "pokemon-mansion"
    )


def test_extract_fb_template_two_arg() -> None:
    assert (
        extract_area_location("{{FB|Kanto|Route 1}}", prefer_preposition=False) == "kanto-route-1"
    )


# --- extract_area_locations (multi, wild-encounter) -----------------------


def test_extract_locations_walks_routes_and_named_places() -> None:
    # Bidoof-style Sinnoh segment: leading [[Route]] generic-noun link,
    # then a comma-joined list of route templates and proper-noun places.
    segment = "[[Route]]s {{rtn|201|Sinnoh}}, {{rtn|202|Sinnoh}}, [[Lake Verity]], [[Great Marsh]]"
    assert extract_area_locations(segment) == [
        "lake-verity",
        "great-marsh",
        "sinnoh-route-201",
        "sinnoh-route-202",
    ]


def test_extract_locations_skips_generic_noun_first_link() -> None:
    # Bare [[Route]] alone yields nothing — it's a category, not a place.
    assert extract_area_locations("[[Route]]") == []


def test_extract_locations_falls_back_to_target_when_display_is_generic() -> None:
    # `[[Sinnoh Route 201|Route]]` — display "Route" is generic; use the
    # underlying target slug instead.
    assert extract_area_locations("[[Sinnoh Route 201|Route]]") == ["sinnoh-route-201"]


def test_extract_locations_skips_pokemon_type_links() -> None:
    # Friend Safari prose: `[[Fire (type)|Fire]]`-style links collapse to
    # the bare type name and must not show up as locations.
    segment = "[[Friend Safari]] ([[Fire (type)|Fire]])"
    assert extract_area_locations(segment) == ["friend-safari"]


def test_extract_locations_dedupes_repeated_slugs() -> None:
    # Same location named twice yields one slug, in first-seen order.
    segment = "[[Lake Verity]] and again [[Lake Verity]]"
    assert extract_area_locations(segment) == ["lake-verity"]


def test_extract_locations_empty_segment() -> None:
    assert extract_area_locations("") == []
    assert extract_area_locations("just prose, no wikilinks") == []


def test_extract_locations_skips_fishing_rod_links() -> None:
    # Fishing-style segment: `[[Cerulean City]] ([[Old Rod]])`. The rod
    # link is the *method*, not the location.
    assert extract_area_locations("[[Cerulean City]] ([[Old Rod]])") == ["cerulean-city"]
    # Multi-rod parenthetical with the bare `[[fishing]]` generic label.
    segment = "[[Whirl Islands]] ([[fishing]] with [[Old Rod]] or [[Good Rod]])"
    assert extract_area_locations(segment) == ["whirl-islands"]


def test_extract_locations_walks_routes_with_rod_suffix() -> None:
    # Mirrors the Bulbapedia fishing shape: routes enumerated, rod
    # annotated as a parenthesized suffix.
    segment = "[[Route]]s {{rtn|12|Kanto}}, {{rtn|13|Kanto}} ([[Old Rod]])"
    assert extract_area_locations(segment) == ["kanto-route-12", "kanto-route-13"]


# --- _rod_set ------------------------------------------------------------


def test_rod_set_none_and_empty() -> None:
    # Empty / None means "no rod info"; the consumption loop treats that as
    # "accept any rod" rather than "matches no rods".
    assert _rod_set(None) == frozenset()
    assert _rod_set("") == frozenset()


def test_rod_set_single_rod() -> None:
    assert _rod_set("super-rod") == frozenset({"super-rod"})


def test_rod_set_comma_joined_strips_whitespace() -> None:
    assert _rod_set("old-rod, good-rod, super-rod") == frozenset(
        {"old-rod", "good-rod", "super-rod"}
    )


def test_rod_set_intersection_is_used_for_match() -> None:
    # Sanity-check the intersection semantic the consumption loop relies on.
    existing = _rod_set("old-rod, good-rod, super-rod")
    bulba = _rod_set("old-rod")
    assert existing & bulba == frozenset({"old-rod"})  # match
    bulba_disjoint = _rod_set("super-rod")
    assert existing & bulba_disjoint == frozenset({"super-rod"})  # also match
    existing_super_only = _rod_set("super-rod")
    bulba_old_only = _rod_set("old-rod")
    assert existing_super_only & bulba_old_only == frozenset()  # no match
