"""Scrape Pokémon species, form, and encounter data from PokéAPI.

Three modes:
    uv run python scrapers/pokeapi.py --mode forms      --max-dex 1025
    uv run python scrapers/pokeapi.py --mode sources    --max-dex 1025
    uv run python scrapers/pokeapi.py --mode evolutions --max-dex 1025

Respects a 1 req/sec rate limit, caches responses under .cache/pokeapi/, and
merges results into data/forms.json or data/sources.json (existing entries are
preserved by composite key).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import httpx
from evolution_details import (
    KNOWN_TRIGGERS,
    detail_to_source_fields,
    gate_games,
    method_for_trigger,
)
from pydantic import TypeAdapter
from utils import RateLimitedClient, merge_by_key, source_key, source_sort_key

from homestretch_data.models import Form, FormCategory, Method, Source

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
CACHE_DIR = REPO_ROOT / ".cache" / "pokeapi"
FORMS_PATH = DATA_DIR / "forms.json"
SOURCES_PATH = DATA_DIR / "sources.json"
BASE_URL = "https://pokeapi.co/api/v2"
USER_AGENT = (
    "HomeStretch/0.1 (+https://github.com/geg0046/homestretch-data; "
    "contact: homestretchapp@outlook.com)"
)
MIN_REQUEST_INTERVAL = 1.0

GENERATION_MAP = {
    "generation-i": 1,
    "generation-ii": 2,
    "generation-iii": 3,
    "generation-iv": 4,
    "generation-v": 5,
    "generation-vi": 6,
    "generation-vii": 7,
    "generation-viii": 8,
    "generation-ix": 9,
}

REGIONAL_SUFFIXES = {"alola", "galar", "hisui", "paldea"}

# Species whose non-default forms are purely visual variants (no stat/ability/
# type differences). PokéAPI has no programmatic signal for this; maintain by
# hand.
COSMETIC_SPECIES = {
    "alcremie",
    "deerling",
    "flabebe",
    "floette",
    "florges",
    "furfrou",
    "gastrodon",
    "maushold",
    "minior",
    "polteageist",
    "poltchageist",
    "sawsbuck",
    "scatterbug",
    "shellos",
    "sinistcha",
    "sinistea",
    "spewpa",
    "tatsugiri",
    "unown",
    "vivillon",
}

# Species whose non-default forms differ mechanically (stats, abilities, types,
# or signature moves).
FUNCTIONAL_SPECIES = {
    "arceus",
    "basculin",
    "burmy",
    "calyrex",
    "deoxys",
    "dialga",
    "dudunsparce",
    "enamorus",
    "genesect",
    "gimmighoul",
    "giratina",
    "gourgeist",
    "hoopa",
    "keldeo",
    "kyurem",
    "landorus",
    "lycanroc",
    "mothim",
    "necrozma",
    "ogerpon",
    "oricorio",
    "palkia",
    "pumpkaboo",
    "rockruff",
    "rotom",
    "shaymin",
    "silvally",
    "squawkabilly",
    "thundurus",
    "tornadus",
    "toxtricity",
    "ursaluna",
    "urshifu",
    "wormadam",
    "zygarde",
}

# Forms PokéAPI exposes that have no living-dex slot in HOME, so the scraper
# drops them from forms.json entirely. Two disjoint reasons, kept as separate
# sets so future readers can see why each form was excluded.

# (1) Not storable in their origin game. Koraidon/Miraidon mount-modes are
# visual ride transformations of the default form and share its PC slot;
# eternatus-eternamax is a story-only boss form never given to the player.
SKIP_FORM_IDS_INGAME = {
    "eternatus-eternamax",
    "koraidon-gliding-build",
    "koraidon-limited-build",
    "koraidon-sprinting-build",
    "koraidon-swimming-build",
    "miraidon-aquatic-mode",
    "miraidon-drive-mode",
    "miraidon-glide-mode",
    "miraidon-low-power-mode",
}

# (2) Storable in their origin game but explicitly blocked from Pokémon Bank
# / HOME transfer, so they cannot exist in a HOME living dex. References:
# - pichu-spiky-eared: HGSS-only; Prof. Elm's in-game dialogue refuses Poké
#   Transfer ("It appears to have traveled through time…").
# - pikachu cosplay family (cosplay/belle/libre/phd/pop-star/rock-star):
#   ORAS contest Pikachu. Cannot be deposited in Pokémon Bank, so no path
#   into Gen 7+ or HOME.
# - cap Pikachus (alola/hoenn/kalos/original/partner/sinnoh/unova/world):
#   event-distributed across USUM / Sw-Sh / Let's Go / GO, but Bulbapedia
#   confirms they cannot be transferred to HOME or to Sword/Shield.
SKIP_FORM_IDS_HOME_UNREACHABLE = {
    "pichu-spiky-eared",
    "pikachu-belle",
    "pikachu-cosplay",
    "pikachu-libre",
    "pikachu-phd",
    "pikachu-pop-star",
    "pikachu-rock-star",
    "pikachu-alola-cap",
    "pikachu-hoenn-cap",
    "pikachu-kalos-cap",
    "pikachu-original-cap",
    "pikachu-partner-cap",
    "pikachu-sinnoh-cap",
    "pikachu-unova-cap",
    "pikachu-world-cap",
}

SKIP_FORM_IDS = SKIP_FORM_IDS_INGAME | SKIP_FORM_IDS_HOME_UNREACHABLE

# PokéAPI version names whose encounters we ingest. Matches the in-scope
# game IDs in data/games.json (game IDs and PokéAPI version names are now the
# same string, so this is just a membership filter). Versions outside this set
# (Gen 3-5 mainline, spin-offs like Colosseum, XD, Channel) are dropped.
IN_SCOPE_VERSIONS: frozenset[str] = frozenset(
    {
        "red",
        "blue",
        "yellow",
        "gold",
        "silver",
        "crystal",
        "x",
        "y",
        "omega-ruby",
        "alpha-sapphire",
        "sun",
        "moon",
        "ultra-sun",
        "ultra-moon",
        "lets-go-pikachu",
        "lets-go-eevee",
        "sword",
        "shield",
        "brilliant-diamond",
        "shining-pearl",
        "legends-arceus",
        "scarlet",
        "violet",
        "legends-za",
    }
)

# PokéAPI encounter-method names → HomeStretch Method enum. Methods absent from
# this map are skipped with a warning so we don't silently miscategorise an
# encounter. Spin-off-only methods (snag*, pokespot, colosseum-bonus-disc-*,
# pokemon-channel-pal, pokemon-ranger, pokemon-battle-revolution,
# new-york-pokecenter-wish-eggs) are intentionally unmapped — their versions
# are out of scope anyway.
ENCOUNTER_METHOD_TO_METHOD: dict[str, Method] = {
    "walk": Method.WILD_ENCOUNTER,
    "surf": Method.WILD_ENCOUNTER,
    "rock-smash": Method.WILD_ENCOUNTER,
    "headbutt": Method.WILD_ENCOUNTER,
    "headbutt-low": Method.WILD_ENCOUNTER,
    "headbutt-normal": Method.WILD_ENCOUNTER,
    "headbutt-high": Method.WILD_ENCOUNTER,
    "dark-grass": Method.WILD_ENCOUNTER,
    "grass-spots": Method.WILD_ENCOUNTER,
    "cave-spots": Method.WILD_ENCOUNTER,
    "bridge-spots": Method.WILD_ENCOUNTER,
    "surf-spots": Method.WILD_ENCOUNTER,
    "yellow-flowers": Method.WILD_ENCOUNTER,
    "purple-flowers": Method.WILD_ENCOUNTER,
    "red-flowers": Method.WILD_ENCOUNTER,
    "rough-terrain": Method.WILD_ENCOUNTER,
    "seaweed": Method.WILD_ENCOUNTER,
    "roaming-grass": Method.WILD_ENCOUNTER,
    "roaming-water": Method.WILD_ENCOUNTER,
    "sos-encounter": Method.WILD_ENCOUNTER,
    "sos-from-bubbling-spot": Method.WILD_ENCOUNTER,
    "bubbling-spots": Method.WILD_ENCOUNTER,
    "berry-piles": Method.WILD_ENCOUNTER,
    "horde": Method.WILD_ENCOUNTER,
    "overworld": Method.WILD_ENCOUNTER,
    "overworld-water": Method.WILD_ENCOUNTER,
    "overworld-flying": Method.WILD_ENCOUNTER,
    "overworld-special": Method.WILD_ENCOUNTER,
    "overworld-water-special": Method.WILD_ENCOUNTER,
    "overworld-flying-special": Method.WILD_ENCOUNTER,
    "old-rod": Method.FISHING,
    "good-rod": Method.FISHING,
    "super-rod": Method.FISHING,
    "super-rod-spots": Method.FISHING,
    "feebas-tile-fishing": Method.FISHING,
    "gift": Method.GIFT,
    "gift-egg": Method.GIFT,
    "only-one": Method.STATIC_ENCOUNTER,
    "pokeflute": Method.STATIC_ENCOUNTER,
    "squirt-bottle": Method.STATIC_ENCOUNTER,
    "wailmer-pail": Method.STATIC_ENCOUNTER,
    "devon-scope": Method.STATIC_ENCOUNTER,
    "island-scan": Method.STATIC_ENCOUNTER,
    "npc-trade": Method.TRADE,
}

# Forms PokéAPI can't auto-flag as event-only but are distributed only via
# events/promotions and ARE HOME-storable. Their acquisition is expressed
# through sources.json with method=event since there's no real encounter.
# Game-exclusive forms like pikachu-starter / eevee-starter (Let's Go P/E)
# are NOT event-only — their game-locking is a gift-method source instead.
EVENT_ONLY_FORM_IDS = {
    "greninja-battle-bond",
    "magearna-original",
    "zarude-dada",
}

# Form-level override for species whose non-default forms are otherwise
# species-level-categorised incorrectly. floette's flower-colour variants
# are cosmetic, but floette-eternal (Eternal Flower Floette) has a unique
# ability, signature move, and stat spread — it's functional.
FUNCTIONAL_FORM_IDS = {
    "floette-eternal",
}


def _is_regional(form_name: str) -> bool:
    if form_name.endswith("-cap"):
        return False
    return any(
        form_name == suffix or form_name.startswith(f"{suffix}-") for suffix in REGIONAL_SUFFIXES
    )


def categorize(
    form_id: str,
    species_id: str,
    is_default_form: bool,
    form_data: dict[str, Any],
    variety_name: str,
) -> list[FormCategory]:
    cats: list[FormCategory] = []
    form_name = form_data.get("form_name") or ""
    if form_data.get("is_mega"):
        cats.append(FormCategory.MEGA)
    if _is_regional(form_name):
        cats.append(FormCategory.REGIONAL_VARIANT)
    if form_name == "gmax" or variety_name.endswith("-gmax"):
        cats.append(FormCategory.GIGANTAMAX)
    if form_name == "primal" or variety_name.endswith("-primal"):
        cats.append(FormCategory.PRIMAL)
    if form_name.startswith("totem"):
        cats.append(FormCategory.TOTEM)
    if form_id in EVENT_ONLY_FORM_IDS:
        cats.append(FormCategory.EVENT_ONLY)
    if not is_default_form and form_name in {"female", "male"}:
        cats.append(FormCategory.GENDER_DIFFERENCE)
    if not is_default_form and not cats:
        if form_id in FUNCTIONAL_FORM_IDS:
            cats.append(FormCategory.FUNCTIONAL)
        elif species_id in COSMETIC_SPECIES:
            cats.append(FormCategory.COSMETIC)
        elif species_id in FUNCTIONAL_SPECIES:
            cats.append(FormCategory.FUNCTIONAL)
    return cats


def build_forms_for_species(
    species: dict[str, Any], client: RateLimitedClient
) -> list[dict[str, Any]]:
    species_id = species["name"]
    national_dex = species["id"]
    generation = GENERATION_MAP[species["generation"]["name"]]

    results: list[dict[str, Any]] = []
    for variety in species["varieties"]:
        is_default_variety = variety["is_default"]
        variety_name = variety["pokemon"]["name"]
        pokemon = client.get_json(variety["pokemon"]["url"])

        for form_ref in pokemon["forms"]:
            form_data = client.get_json(form_ref["url"])
            if form_data.get("is_battle_only"):
                continue

            is_default_form = bool(form_data.get("is_default")) and is_default_variety
            form_id = species_id if is_default_form else form_ref["name"]
            if form_id in SKIP_FORM_IDS:
                continue
            categories = categorize(form_id, species_id, is_default_form, form_data, variety_name)
            raw_form_name = form_data.get("form_name") or None

            results.append(
                {
                    "id": form_id,
                    "species_id": species_id,
                    "national_dex": national_dex,
                    "form_name": None if is_default_form else raw_form_name,
                    "is_default": is_default_form,
                    "generation_introduced": generation,
                    "categories": [c.value for c in categories],
                }
            )
    return results


def _primary_form_id_for_variety(
    species_id: str,
    variety: dict[str, Any],
    pokemon: dict[str, Any],
    client: RateLimitedClient,
) -> str | None:
    """Return the form_id we attribute this variety's encounters to, or None
    if every form in the variety is battle-only or in SKIP_FORM_IDS.
    """
    is_default_variety = variety["is_default"]
    for form_ref in pokemon["forms"]:
        form_data = client.get_json(form_ref["url"])
        if form_data.get("is_battle_only"):
            continue
        is_default_form = bool(form_data.get("is_default")) and is_default_variety
        form_id = species_id if is_default_form else form_ref["name"]
        if form_id in SKIP_FORM_IDS:
            continue
        return form_id
    return None


def build_sources_for_species(
    species: dict[str, Any],
    client: RateLimitedClient,
    unknown_methods: set[str],
) -> list[dict[str, Any]]:
    """Emit one Source row per unique (form_id, game_id, our-method) combo.

    PokéAPI's encounter data is keyed by variety (pokemon), not by form. We
    attribute each variety's encounters to the variety's primary non-battle-
    only form. PokéAPI method names that map to the same Method enum value
    collapse into one Source row whose method_details lists them.
    """
    species_id = species["name"]
    by_key: dict[tuple[str, str, Method], set[str]] = {}

    for variety in species["varieties"]:
        variety_name = variety["pokemon"]["name"]
        pokemon = client.get_json(variety["pokemon"]["url"])
        primary_form_id = _primary_form_id_for_variety(species_id, variety, pokemon, client)
        if primary_form_id is None:
            continue

        encounters = client.get_json(f"{BASE_URL}/pokemon/{variety_name}/encounters")
        for entry in encounters:
            for version_detail in entry["version_details"]:
                version_name = version_detail["version"]["name"]
                if version_name not in IN_SCOPE_VERSIONS:
                    continue
                for detail in version_detail["encounter_details"]:
                    method_name = detail["method"]["name"]
                    our_method = ENCOUNTER_METHOD_TO_METHOD.get(method_name)
                    if our_method is None:
                        unknown_methods.add(method_name)
                        continue
                    key = (primary_form_id, version_name, our_method)
                    by_key.setdefault(key, set()).add(method_name)

    results: list[dict[str, Any]] = []
    for (form_id, game_id, method), method_names in by_key.items():
        # Rule 7: drop any component that equals the method enum — it's
        # redundant with `method`. Matters when the collapsed set mixes
        # a bare method name with a subtype (e.g. {"gift", "gift-egg"}
        # → method_details="gift-egg", not "gift, gift-egg").
        components = sorted(n for n in method_names if n != method.value)
        details_str = ", ".join(components)
        entry: dict[str, Any] = {
            "form_id": form_id,
            "game_id": game_id,
            "method": method.value,
        }
        if details_str:
            entry["method_details"] = details_str
        results.append(entry)
    return results


# PokéAPI regional-pokédex names → in-scope game IDs. Used by the evolution
# pass to decide which games an evolved species is available in (pokemon
# endpoint's game_indices is empty for Gen 6+, so we can't use that signal
# there). Omits national/conquest-gallery (too broad / out of scope) and
# `champions` (unclear semantics across multiple gens). Pokédexes whose only
# games are out of scope (original-sinnoh, original-unova, updated-unova,
# hoenn for R/S/E) are also omitted.
POKEDEX_TO_GAMES: dict[str, tuple[str, ...]] = {
    "kanto": ("red", "blue", "yellow"),
    "original-johto": ("gold", "silver"),
    "updated-johto": ("crystal",),
    "kalos-central": ("x", "y"),
    "kalos-coastal": ("x", "y"),
    "kalos-mountain": ("x", "y"),
    "updated-hoenn": ("omega-ruby", "alpha-sapphire"),
    "original-alola": ("sun", "moon"),
    "original-melemele": ("sun", "moon"),
    "original-akala": ("sun", "moon"),
    "original-ulaula": ("sun", "moon"),
    "original-poni": ("sun", "moon"),
    "updated-alola": ("ultra-sun", "ultra-moon"),
    "updated-melemele": ("ultra-sun", "ultra-moon"),
    "updated-akala": ("ultra-sun", "ultra-moon"),
    "updated-ulaula": ("ultra-sun", "ultra-moon"),
    "updated-poni": ("ultra-sun", "ultra-moon"),
    "letsgo-kanto": ("lets-go-pikachu", "lets-go-eevee"),
    "galar": ("sword", "shield"),
    "isle-of-armor": ("sword", "shield"),
    "crown-tundra": ("sword", "shield"),
    "extended-sinnoh": ("brilliant-diamond", "shining-pearl"),
    "hisui": ("legends-arceus",),
    "paldea": ("scarlet", "violet"),
    "kitakami": ("scarlet", "violet"),
    "blueberry": ("scarlet", "violet"),
    "lumiose-city": ("legends-za",),
}


def build_evolution_sources_for_chain(
    chain: dict[str, Any],
    client: RateLimitedClient,
    valid_form_ids: set[str],
    unknown_triggers: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Walk a PokéAPI evolution-chain and emit one Source row per
    evolution_detail per applicable game.

    Each PokéAPI `evolution_detail` dict is one specific evolution path
    (Crabominable has two: one with `location=mount-lanakila`, one with
    `item=ice-stone`). Each path gets its own row. Structured condition
    fields (item, held_item, location, known_move, trade_species, …) are
    populated via `detail_to_source_fields`. Per-game gating narrows the
    species's pokedex scope when the detail names a location or item that
    isn't available in every scoped game.

    Attribution is to the evolved species's default form (form_id ==
    species_id). Branched regional evolutions (runerigus from
    yamask-galar) are refined afterwards by the Bulbapedia evolutions
    pass, which fills `from_form` and adds rows for regional targets
    PokéAPI skipped.
    """
    results: list[dict[str, Any]] = []

    def walk(node: dict[str, Any]) -> None:
        for child in node["evolves_to"]:
            evolved_species_id = child["species"]["name"]
            if evolved_species_id not in valid_form_ids:
                walk(child)
                continue

            evolved_species = client.get_json(child["species"]["url"])
            scope_games: set[str] = set()
            for pdn in evolved_species.get("pokedex_numbers", []):
                scope_games.update(POKEDEX_TO_GAMES.get(pdn["pokedex"]["name"], ()))
            scope_games &= IN_SCOPE_VERSIONS
            if not scope_games:
                walk(child)
                continue

            for detail in child["evolution_details"]:
                trigger = detail.get("trigger", {}).get("name")
                if not trigger:
                    continue
                if trigger not in KNOWN_TRIGGERS and unknown_triggers is not None:
                    unknown_triggers.add(trigger)
                method = method_for_trigger(trigger)
                fields = detail_to_source_fields(detail)
                applicable_games = gate_games(fields, scope_games, evolved_species_id)
                if not applicable_games:
                    continue
                # Rule 7: omit method_details when it equals the enum.
                details_str = fields.pop("method_details", None)
                if details_str == method.value:
                    details_str = None
                for game_id in applicable_games:
                    entry: dict[str, Any] = {
                        "form_id": evolved_species_id,
                        "game_id": game_id,
                        "method": method.value,
                    }
                    if details_str is not None:
                        entry["method_details"] = details_str
                    entry.update(fields)
                    results.append(entry)
            walk(child)

    walk(chain["chain"])
    return results


def load_existing_forms() -> list[dict[str, Any]]:
    if not FORMS_PATH.exists():
        return []
    raw = json.loads(FORMS_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw, list)
    return raw


def merge_forms(existing: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = merge_by_key(existing, new, key_fn=lambda f: f["id"])
    merged.sort(key=lambda f: (f["national_dex"], f["id"]))
    return merged


_source_key = source_key
_source_sort_key = source_sort_key


def load_existing_sources() -> list[dict[str, Any]]:
    if not SOURCES_PATH.exists():
        return []
    raw = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw, list)
    return raw


def merge_sources(
    existing: list[dict[str, Any]], new: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    merged = merge_by_key(existing, new, key_fn=_source_key)
    merged.sort(key=_source_sort_key)
    return merged


def _scrape_forms(client: RateLimitedClient, min_dex: int, max_dex: int) -> int:
    all_new: list[dict[str, Any]] = []
    for dex in range(min_dex, max_dex + 1):
        species = client.get_json(f"{BASE_URL}/pokemon-species/{dex}/")
        forms = build_forms_for_species(species, client)
        all_new.extend(forms)
        print(f"  #{dex:04d} {species['name']}: {len(forms)} form(s)")

    merged = merge_forms(load_existing_forms(), all_new)
    TypeAdapter(list[Form]).validate_python(merged)
    FORMS_PATH.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(merged)} forms to {FORMS_PATH.relative_to(REPO_ROOT)}")
    return 0


def _scrape_sources(client: RateLimitedClient, min_dex: int, max_dex: int) -> int:
    all_new: list[dict[str, Any]] = []
    unknown_methods: set[str] = set()
    for dex in range(min_dex, max_dex + 1):
        species = client.get_json(f"{BASE_URL}/pokemon-species/{dex}/")
        sources = build_sources_for_species(species, client, unknown_methods)
        all_new.extend(sources)
        print(f"  #{dex:04d} {species['name']}: {len(sources)} source(s)")

    merged = merge_sources(load_existing_sources(), all_new)
    TypeAdapter(list[Source]).validate_python(merged)
    SOURCES_PATH.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(merged)} sources to {SOURCES_PATH.relative_to(REPO_ROOT)}")
    if unknown_methods:
        names = sorted(unknown_methods)
        print(f"warning: {len(names)} unmapped PokéAPI encounter-method(s): {names}")
    return 0


def _scrape_evolutions(client: RateLimitedClient, min_dex: int, max_dex: int) -> int:
    existing_forms = load_existing_forms()
    if not existing_forms:
        print("error: data/forms.json is empty; run --mode forms first", file=sys.stderr)
        return 1
    valid_form_ids = {f["id"] for f in existing_forms}

    seen_chain_urls: set[str] = set()
    all_new: list[dict[str, Any]] = []
    unknown_triggers: set[str] = set()
    for dex in range(min_dex, max_dex + 1):
        species = client.get_json(f"{BASE_URL}/pokemon-species/{dex}/")
        chain_url = species["evolution_chain"]["url"]
        if chain_url in seen_chain_urls:
            print(f"  #{dex:04d} {species['name']}: chain already processed")
            continue
        seen_chain_urls.add(chain_url)
        chain = client.get_json(chain_url)
        sources = build_evolution_sources_for_chain(chain, client, valid_form_ids, unknown_triggers)
        all_new.extend(sources)
        print(f"  #{dex:04d} {species['name']}: {len(sources)} evolution source(s)")

    merged = merge_sources(load_existing_sources(), all_new)
    TypeAdapter(list[Source]).validate_python(merged)
    SOURCES_PATH.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(merged)} sources to {SOURCES_PATH.relative_to(REPO_ROOT)}")
    if unknown_triggers:
        names = sorted(unknown_triggers)
        print(f"warning: {len(names)} unrecognized evolution trigger(s): {names}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("forms", "sources", "evolutions"),
        default="forms",
        help="What to scrape (default: forms)",
    )
    parser.add_argument(
        "--max-dex",
        type=int,
        default=151,
        help="Highest national-dex number to scrape (default: 151 = Gen 1)",
    )
    parser.add_argument(
        "--min-dex",
        type=int,
        default=1,
        help="Lowest national-dex number to scrape (default: 1)",
    )
    args = parser.parse_args()

    with httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=30.0,
    ) as raw_client:
        client = RateLimitedClient(raw_client, MIN_REQUEST_INTERVAL, CACHE_DIR)
        if args.mode == "forms":
            return _scrape_forms(client, args.min_dex, args.max_dex)
        if args.mode == "sources":
            return _scrape_sources(client, args.min_dex, args.max_dex)
        return _scrape_evolutions(client, args.min_dex, args.max_dex)


if __name__ == "__main__":
    sys.exit(main())
