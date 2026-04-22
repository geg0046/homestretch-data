"""Merge manually-curated Source rows into `data/sources.json`.

Covers categories upstream doesn't express as structured data:

1. **Breeding-only babies** (Pichu, Cleffa, Elekid, etc.) — one row per
   (baby, game) pair for games that: (a) are >= the baby's introduction
   generation and (b) have the parent species available. Encoded as
   `method=breeding`, `from_form=<parent>`.
2. **Game Corner prize Pokémon** (Abra/Dratini/Porygon in RBY/GSC, plus
   a handful of others) — encoded as `method=purchase,
   method_details=game-corner`.
3. **Forms no scraper expresses at all** (event-only Pokémon, Let's Go
   partner starters, Gen 9 DLC evolutions PokéAPI's evolution chain
   data hasn't been updated for) — enumerated row-by-row in
   `EXPLICIT_ROWS`.

Re-runs are idempotent: rows are keyed on the full Source identity
(`SOURCE_KEY_FIELDS`) and existing entries win on conflict, matching
scraper merge semantics. Run after a fresh scrape rebuild:

    rm data/sources.json
    uv run python scrapers/pokeapi.py    --mode sources --max-dex 1025
    uv run python scrapers/pokeapi.py    --mode evolutions --max-dex 1025
    uv run python scrapers/bulbapedia.py --mode sources --max-dex 1025
    uv run python scrapers/bulbapedia.py --mode evolutions --max-dex 1025
    uv run python scripts/seed_manual_sources.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import TypeAdapter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scrapers"))
from utils import merge_by_key, source_key, source_sort_key

from homestretch_data.models import Source

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCES_PATH = REPO_ROOT / "data" / "sources.json"


# Breeding rows: (baby_form, parent_form, [games])
# Games chosen by the original one-shot: game generation >= baby's intro gen,
# parent available in the game, no existing source row for baby in that game.
BREEDING_ROWS: list[tuple[str, str, list[str]]] = [
    ("azurill", "marill", ["alpha-sapphire", "moon", "omega-ruby", "sun"]),
    ("bonsly", "sudowoodo", ["x", "y"]),
    ("budew", "roselia", ["alpha-sapphire", "moon", "omega-ruby", "sun"]),
    ("chingling", "chimecho", ["alpha-sapphire", "omega-ruby"]),
    ("cleffa", "clefairy", ["gold", "silver", "x", "y"]),
    ("elekid", "electabuzz", ["gold", "shining-pearl", "silver", "x", "y"]),
    ("happiny", "chansey", ["x", "y"]),
    ("igglybuff", "jigglypuff", ["alpha-sapphire", "gold", "omega-ruby", "silver", "x", "y"]),
    ("magby", "magmar", ["brilliant-diamond", "gold", "silver", "x", "y"]),
    ("munchlax", "snorlax", ["x", "y"]),
    ("pichu", "pikachu", ["alpha-sapphire", "gold", "omega-ruby", "silver", "x", "y"]),
    ("smoochum", "jynx", ["gold", "silver"]),
    ("togepi", "togetic", ["moon", "sun"]),
    ("wynaut", "wobbuffet", ["x", "y"]),
]

# Game Corner purchase rows: (form, [games])
# RBY Celadon + GSC Goldenrod prize Pokémon. Mauville (RSE) and Veilstone
# (DPPt) excluded — their prize lists are TMs/items only.
PURCHASE_ROWS: list[tuple[str, list[str]]] = [
    ("abra", ["blue", "crystal", "gold", "red", "silver", "yellow"]),
    ("clefairy", ["blue", "red"]),
    ("cubone", ["crystal"]),
    ("dratini", ["blue", "gold", "red", "silver"]),
    ("eevee", ["gold", "silver"]),
    ("ekans", ["gold"]),
    ("larvitar", ["crystal"]),
    ("mr-mime", ["gold", "silver"]),
    ("nidorina", ["red"]),
    ("nidorino", ["blue"]),
    ("pikachu", ["crystal"]),
    ("pinsir", ["blue", "yellow"]),
    ("porygon", ["blue", "crystal", "gold", "red", "silver", "yellow"]),
    ("sandshrew", ["silver"]),
    ("scyther", ["red", "yellow"]),
    ("vulpix", ["yellow"]),
    ("wigglytuff", ["yellow"]),
    ("wobbuffet", ["crystal"]),
]

# Fully-specified rows for forms where scraper coverage is incomplete and the
# acquisition path is narrow enough to enumerate. Each dict is a complete
# Source payload (everything but the implicit idempotency key).
EXPLICIT_ROWS: list[dict[str, object]] = [
    # Event-only forms (scrapers/pokeapi.py::EVENT_ONLY_FORM_IDS marks the
    # category; nothing emits the corresponding source rows).
    # greninja-battle-bond: Ash-Greninja distribution in SM (2017).
    {"form_id": "greninja-battle-bond", "game_id": "sun", "method": "event"},
    {"form_id": "greninja-battle-bond", "game_id": "moon", "method": "event"},
    # magearna-original: "Original Color" Magearna, awarded for completing
    # the National Dex in Pokémon HOME from SwSh (2020).
    {"form_id": "magearna-original", "game_id": "sword", "method": "event"},
    {"form_id": "magearna-original", "game_id": "shield", "method": "event"},
    # zarude-dada: serial-code distribution tied to the Coco movie (2020).
    {"form_id": "zarude-dada", "game_id": "sword", "method": "event"},
    {"form_id": "zarude-dada", "game_id": "shield", "method": "event"},
    # Let's Go partner Pikachu/Eevee — game-locked gift at game start.
    {"form_id": "pikachu-starter", "game_id": "lets-go-pikachu", "method": "gift"},
    {"form_id": "eevee-starter", "game_id": "lets-go-eevee", "method": "gift"},
    # basculin-white-striped: PLA wild + SV Indigo Disk Terarium.
    {
        "form_id": "basculin-white-striped",
        "game_id": "legends-arceus",
        "method": "wild-encounter",
    },
    {
        "form_id": "basculin-white-striped",
        "game_id": "scarlet",
        "method": "wild-encounter",
        "requires_dlc": "hidden-treasure-of-area-zero",
    },
    {
        "form_id": "basculin-white-striped",
        "game_id": "violet",
        "method": "wild-encounter",
        "requires_dlc": "hidden-treasure-of-area-zero",
    },
    # Indigo Disk evolutions PokéAPI's evolution-chain endpoint hasn't
    # surfaced yet. Duraludon → Archaludon via Metal Alloy; Dipplin →
    # Hydrapple via level-up while knowing Dragon Cheer.
    {
        "form_id": "archaludon",
        "game_id": "scarlet",
        "method": "evolution",
        "method_details": "use-item",
        "item": "metal-alloy",
        "from_form": "duraludon",
        "requires_dlc": "hidden-treasure-of-area-zero",
    },
    {
        "form_id": "archaludon",
        "game_id": "violet",
        "method": "evolution",
        "method_details": "use-item",
        "item": "metal-alloy",
        "from_form": "duraludon",
        "requires_dlc": "hidden-treasure-of-area-zero",
    },
    {
        "form_id": "hydrapple",
        "game_id": "scarlet",
        "method": "evolution",
        "method_details": "level-up",
        "known_move": "dragon-cheer",
        "from_form": "dipplin",
        "requires_dlc": "hidden-treasure-of-area-zero",
    },
    {
        "form_id": "hydrapple",
        "game_id": "violet",
        "method": "evolution",
        "method_details": "level-up",
        "known_move": "dragon-cheer",
        "from_form": "dipplin",
        "requires_dlc": "hidden-treasure-of-area-zero",
    },
    # Fusion and held-item forms. These are HOME-storable state changes
    # (not battle-only) but PokéAPI doesn't express them as evolutions —
    # they're triggered by key items outside the evolution system.
    # Encoded as method=gift: the key item is the gift; the form is what
    # holding or using it on the base legendary produces. from_form points
    # at the base; the fusion partner / mask goes in notes since no
    # structured field fits.
    # Kyurem-black / -white via DNA Splicers (Zekrom / Reshiram fusion).
    # Obtainable where both base legendaries + splicers are available: SwSh
    # Crown Tundra and SV Indigo Disk.
    {
        "form_id": "kyurem-black",
        "game_id": "sword",
        "method": "gift",
        "from_form": "kyurem",
        "requires_dlc": "expansion-pass",
        "notes": "Fuse Kyurem with Zekrom using DNA Splicers (Crown Tundra).",
    },
    {
        "form_id": "kyurem-black",
        "game_id": "shield",
        "method": "gift",
        "from_form": "kyurem",
        "requires_dlc": "expansion-pass",
        "notes": "Fuse Kyurem with Zekrom using DNA Splicers (Crown Tundra).",
    },
    {
        "form_id": "kyurem-black",
        "game_id": "scarlet",
        "method": "gift",
        "from_form": "kyurem",
        "requires_dlc": "hidden-treasure-of-area-zero",
        "notes": "Fuse Kyurem with Zekrom using DNA Splicers (Indigo Disk).",
    },
    {
        "form_id": "kyurem-black",
        "game_id": "violet",
        "method": "gift",
        "from_form": "kyurem",
        "requires_dlc": "hidden-treasure-of-area-zero",
        "notes": "Fuse Kyurem with Zekrom using DNA Splicers (Indigo Disk).",
    },
    {
        "form_id": "kyurem-white",
        "game_id": "sword",
        "method": "gift",
        "from_form": "kyurem",
        "requires_dlc": "expansion-pass",
        "notes": "Fuse Kyurem with Reshiram using DNA Splicers (Crown Tundra).",
    },
    {
        "form_id": "kyurem-white",
        "game_id": "shield",
        "method": "gift",
        "from_form": "kyurem",
        "requires_dlc": "expansion-pass",
        "notes": "Fuse Kyurem with Reshiram using DNA Splicers (Crown Tundra).",
    },
    {
        "form_id": "kyurem-white",
        "game_id": "scarlet",
        "method": "gift",
        "from_form": "kyurem",
        "requires_dlc": "hidden-treasure-of-area-zero",
        "notes": "Fuse Kyurem with Reshiram using DNA Splicers (Indigo Disk).",
    },
    {
        "form_id": "kyurem-white",
        "game_id": "violet",
        "method": "gift",
        "from_form": "kyurem",
        "requires_dlc": "hidden-treasure-of-area-zero",
        "notes": "Fuse Kyurem with Reshiram using DNA Splicers (Indigo Disk).",
    },
    # Calyrex-ice / -shadow via Reins of Unity (Glastrier / Spectrier fusion).
    # SwSh Crown Tundra only.
    {
        "form_id": "calyrex-ice",
        "game_id": "sword",
        "method": "gift",
        "from_form": "calyrex",
        "requires_dlc": "expansion-pass",
        "notes": "Fuse Calyrex with Glastrier using Reins of Unity.",
    },
    {
        "form_id": "calyrex-ice",
        "game_id": "shield",
        "method": "gift",
        "from_form": "calyrex",
        "requires_dlc": "expansion-pass",
        "notes": "Fuse Calyrex with Glastrier using Reins of Unity.",
    },
    {
        "form_id": "calyrex-shadow",
        "game_id": "sword",
        "method": "gift",
        "from_form": "calyrex",
        "requires_dlc": "expansion-pass",
        "notes": "Fuse Calyrex with Spectrier using Reins of Unity.",
    },
    {
        "form_id": "calyrex-shadow",
        "game_id": "shield",
        "method": "gift",
        "from_form": "calyrex",
        "requires_dlc": "expansion-pass",
        "notes": "Fuse Calyrex with Spectrier using Reins of Unity.",
    },
    # Necrozma-dawn / -dusk via N-Lunarizer / N-Solarizer (Lunala / Solgaleo
    # fusion). USUM only; fusion persists in storage.
    {
        "form_id": "necrozma-dawn",
        "game_id": "ultra-sun",
        "method": "gift",
        "from_form": "necrozma",
        "notes": "Fuse Necrozma with Lunala using N-Lunarizer.",
    },
    {
        "form_id": "necrozma-dawn",
        "game_id": "ultra-moon",
        "method": "gift",
        "from_form": "necrozma",
        "notes": "Fuse Necrozma with Lunala using N-Lunarizer.",
    },
    {
        "form_id": "necrozma-dusk",
        "game_id": "ultra-sun",
        "method": "gift",
        "from_form": "necrozma",
        "notes": "Fuse Necrozma with Solgaleo using N-Solarizer.",
    },
    {
        "form_id": "necrozma-dusk",
        "game_id": "ultra-moon",
        "method": "gift",
        "from_form": "necrozma",
        "notes": "Fuse Necrozma with Solgaleo using N-Solarizer.",
    },
    # Ogerpon masks: each mask is a SV Teal Mask DLC quest reward; holding
    # it on Ogerpon produces the corresponding form.
    {
        "form_id": "ogerpon-wellspring-mask",
        "game_id": "scarlet",
        "method": "gift",
        "from_form": "ogerpon",
        "requires_dlc": "hidden-treasure-of-area-zero",
        "notes": "Hold the Wellspring Mask (Teal Mask DLC reward) on Ogerpon.",
    },
    {
        "form_id": "ogerpon-wellspring-mask",
        "game_id": "violet",
        "method": "gift",
        "from_form": "ogerpon",
        "requires_dlc": "hidden-treasure-of-area-zero",
        "notes": "Hold the Wellspring Mask (Teal Mask DLC reward) on Ogerpon.",
    },
    {
        "form_id": "ogerpon-hearthflame-mask",
        "game_id": "scarlet",
        "method": "gift",
        "from_form": "ogerpon",
        "requires_dlc": "hidden-treasure-of-area-zero",
        "notes": "Hold the Hearthflame Mask (Teal Mask DLC reward) on Ogerpon.",
    },
    {
        "form_id": "ogerpon-hearthflame-mask",
        "game_id": "violet",
        "method": "gift",
        "from_form": "ogerpon",
        "requires_dlc": "hidden-treasure-of-area-zero",
        "notes": "Hold the Hearthflame Mask (Teal Mask DLC reward) on Ogerpon.",
    },
    {
        "form_id": "ogerpon-cornerstone-mask",
        "game_id": "scarlet",
        "method": "gift",
        "from_form": "ogerpon",
        "requires_dlc": "hidden-treasure-of-area-zero",
        "notes": "Hold the Cornerstone Mask (Teal Mask DLC reward) on Ogerpon.",
    },
    {
        "form_id": "ogerpon-cornerstone-mask",
        "game_id": "violet",
        "method": "gift",
        "from_form": "ogerpon",
        "requires_dlc": "hidden-treasure-of-area-zero",
        "notes": "Hold the Cornerstone Mask (Teal Mask DLC reward) on Ogerpon.",
    },
    # Mythicals. Distributed via serial codes, Pokémon Center events, movie
    # tie-ins, or unique in-game mechanics — PokéAPI lists them in the
    # Pokédex but emits no encounter data. Games listed are in-scope
    # distribution venues; transfers to later games are covered by
    # transfers.json, not replicated here.
    {
        "form_id": "victini",
        "game_id": "sword",
        "method": "event",
        "notes": "Pokémon Day 2021 serial-code distribution.",
    },
    {
        "form_id": "victini",
        "game_id": "shield",
        "method": "event",
        "notes": "Pokémon Day 2021 serial-code distribution.",
    },
    # Genesect is Crown Tundra's Dynamax Adventure legendary — technically an
    # encounter, not a distribution event.
    {
        "form_id": "genesect",
        "game_id": "sword",
        "method": "raid",
        "method_details": "dynamax-adventure",
        "requires_dlc": "expansion-pass",
    },
    {
        "form_id": "genesect",
        "game_id": "shield",
        "method": "raid",
        "method_details": "dynamax-adventure",
        "requires_dlc": "expansion-pass",
    },
    {
        "form_id": "diancie",
        "game_id": "omega-ruby",
        "method": "event",
        "notes": "2014 Pokémon Center code distribution tied to the XY Wi-Fi event.",
    },
    {
        "form_id": "diancie",
        "game_id": "alpha-sapphire",
        "method": "event",
        "notes": "2014 Pokémon Center code distribution tied to the XY Wi-Fi event.",
    },
    {
        "form_id": "hoopa",
        "game_id": "omega-ruby",
        "method": "event",
        "notes": "2015 Pokémon Center serial-code distribution.",
    },
    {
        "form_id": "hoopa",
        "game_id": "alpha-sapphire",
        "method": "event",
        "notes": "2015 Pokémon Center serial-code distribution.",
    },
    {
        "form_id": "volcanion",
        "game_id": "omega-ruby",
        "method": "event",
        "notes": "2016 Volcanion and the Mechanical Marvel movie tie-in distribution.",
    },
    {
        "form_id": "volcanion",
        "game_id": "alpha-sapphire",
        "method": "event",
        "notes": "2016 Volcanion and the Mechanical Marvel movie tie-in distribution.",
    },
    {
        "form_id": "marshadow",
        "game_id": "ultra-sun",
        "method": "event",
        "notes": "2017 I Choose You movie tie-in serial-code distribution.",
    },
    {
        "form_id": "marshadow",
        "game_id": "ultra-moon",
        "method": "event",
        "notes": "2017 I Choose You movie tie-in serial-code distribution.",
    },
    {
        "form_id": "zeraora",
        "game_id": "ultra-sun",
        "method": "event",
        "notes": "2019 Pokémon Center / Power of Us movie tie-in distribution.",
    },
    {
        "form_id": "zeraora",
        "game_id": "ultra-moon",
        "method": "event",
        "notes": "2019 Pokémon Center / Power of Us movie tie-in distribution.",
    },
    {
        "form_id": "zeraora",
        "game_id": "sword",
        "method": "event",
        "notes": "2020 global Max Raid challenge giveaway.",
    },
    {
        "form_id": "zeraora",
        "game_id": "shield",
        "method": "event",
        "notes": "2020 global Max Raid challenge giveaway.",
    },
    # Meltan / Melmetal are exclusive to the Let's Go games' GO integration.
    # Meltan is caught in the Mystery Box (received by transferring from GO).
    # Melmetal must be evolved in GO with 400 Candy, then transferred back.
    {
        "form_id": "meltan",
        "game_id": "lets-go-pikachu",
        "method": "gift",
        "notes": "Caught in the Mystery Box after connecting Pokémon GO.",
    },
    {
        "form_id": "meltan",
        "game_id": "lets-go-eevee",
        "method": "gift",
        "notes": "Caught in the Mystery Box after connecting Pokémon GO.",
    },
    {
        "form_id": "melmetal",
        "game_id": "lets-go-pikachu",
        "method": "gift",
        "notes": "Evolved from Meltan in Pokémon GO (400 Candy) and transferred.",
    },
    {
        "form_id": "melmetal",
        "game_id": "lets-go-eevee",
        "method": "gift",
        "notes": "Evolved from Meltan in Pokémon GO (400 Candy) and transferred.",
    },
    {
        "form_id": "zarude",
        "game_id": "sword",
        "method": "event",
        "notes": "2020 Coco movie tie-in serial-code distribution.",
    },
    {
        "form_id": "zarude",
        "game_id": "shield",
        "method": "event",
        "notes": "2020 Coco movie tie-in serial-code distribution.",
    },
    # Pecharunt is unlocked through the Mochi Mayhem epilogue in SV after
    # finishing the Indigo Disk DLC. Story-driven single capture.
    {
        "form_id": "pecharunt",
        "game_id": "scarlet",
        "method": "static-encounter",
        "method_details": "only-one",
        "requires_dlc": "hidden-treasure-of-area-zero",
        "notes": "Caught during the Mochi Mayhem epilogue after completing Indigo Disk.",
    },
    {
        "form_id": "pecharunt",
        "game_id": "violet",
        "method": "static-encounter",
        "method_details": "only-one",
        "requires_dlc": "hidden-treasure-of-area-zero",
        "notes": "Caught during the Mochi Mayhem epilogue after completing Indigo Disk.",
    },
    # Gen 9 paradox Pokémon absent from PokéAPI encounter data. Walking
    # Wake / Iron Leaves were the SV 1.2.0 Tera Raid event in 2023 (no DLC
    # required). The remaining four (Gouging Fire, Raging Bolt, Iron
    # Boulder, Iron Crown) became permanently catchable via the
    # Area Zero Underdepths special encounter in the Indigo Disk DLC —
    # version-exclusive along the same ancient/future split as the other
    # paradox pairs.
    {
        "form_id": "walking-wake",
        "game_id": "scarlet",
        "method": "raid",
        "method_details": "tera-raid",
        "notes": "SV 1.2.0 Tera Raid Battle event (2023).",
    },
    {
        "form_id": "iron-leaves",
        "game_id": "violet",
        "method": "raid",
        "method_details": "tera-raid",
        "notes": "SV 1.2.0 Tera Raid Battle event (2023).",
    },
    {
        "form_id": "gouging-fire",
        "game_id": "scarlet",
        "method": "static-encounter",
        "method_details": "only-one",
        "requires_dlc": "hidden-treasure-of-area-zero",
        "notes": "Area Zero Underdepths encounter after completing the Indigo Disk DLC.",
    },
    {
        "form_id": "raging-bolt",
        "game_id": "scarlet",
        "method": "static-encounter",
        "method_details": "only-one",
        "requires_dlc": "hidden-treasure-of-area-zero",
        "notes": "Area Zero Underdepths encounter after completing the Indigo Disk DLC.",
    },
    {
        "form_id": "iron-boulder",
        "game_id": "violet",
        "method": "static-encounter",
        "method_details": "only-one",
        "requires_dlc": "hidden-treasure-of-area-zero",
        "notes": "Area Zero Underdepths encounter after completing the Indigo Disk DLC.",
    },
    {
        "form_id": "iron-crown",
        "game_id": "violet",
        "method": "static-encounter",
        "method_details": "only-one",
        "requires_dlc": "hidden-treasure-of-area-zero",
        "notes": "Area Zero Underdepths encounter after completing the Indigo Disk DLC.",
    },
]


def _build_rows() -> list[dict]:
    rows: list[dict] = []
    for baby, parent, games in BREEDING_ROWS:
        for game in games:
            rows.append(
                {
                    "form_id": baby,
                    "game_id": game,
                    "method": "breeding",
                    "from_form": parent,
                }
            )
    for form, games in PURCHASE_ROWS:
        for game in games:
            rows.append(
                {
                    "form_id": form,
                    "game_id": game,
                    "method": "purchase",
                    "method_details": "game-corner",
                }
            )
    rows.extend(EXPLICIT_ROWS)
    return rows


def main() -> int:
    new_rows = _build_rows()
    adapter = TypeAdapter(list[Source])
    # Validate shape before merging.
    adapter.validate_python(new_rows)

    existing: list[dict] = []
    if SOURCES_PATH.exists():
        existing = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))

    merged = merge_by_key(existing, new_rows, source_key)
    merged.sort(key=source_sort_key)

    # Re-validate the final set and normalize to canonical dict shape.
    validated = adapter.validate_python(merged)
    out = [s.model_dump(exclude_none=True, exclude_defaults=True) for s in validated]

    SOURCES_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    added = len(merged) - len(existing)
    print(f"seed_manual_sources: {added} new row(s), {len(merged)} total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
