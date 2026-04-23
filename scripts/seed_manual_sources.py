"""Merge manually-curated Source rows into `data/sources.json`.

Covers categories upstream doesn't express as structured data:

1. **Breeding-only babies** (Pichu, Cleffa, Elekid, etc.) — one row per
   (baby, game) pair for games that: (a) are >= the baby's introduction
   generation and (b) have the parent species available. Encoded as
   `method=breeding`, `from_form=<parent>`.
2. **Game Corner prize Pokémon** (Abra/Dratini/Porygon in RBY/GSC, plus
   a handful of others) — encoded as `method=purchase,
   method_details=game-corner`.
3. **Gender-difference female forms** — derived rows mirroring the
   default species' existing encounters with `gender=female`. Stays in
   sync with scraper output.
4. **Forms no scraper expresses at all** (event-only Pokémon, Let's Go
   partner starters, Gen 9 DLC evolutions PokéAPI's evolution chain
   data hasn't been updated for, item-triggered form changes like
   Arceus plates and Silvally memories, USUM totem-sticker rewards) —
   enumerated row-by-row in `EXPLICIT_ROWS`.

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

# Gender-difference pairs: (female_form, default_form). Rows are derived
# at build time by mirroring every source row for the default with
# `gender=female` set. Keeps in sync with authoritative scraper output
# rather than hard-coding per-game encounter lists.
GENDER_DIFFERENCE_PAIRS: list[tuple[str, str]] = [
    ("frillish-female", "frillish"),
    ("jellicent-female", "jellicent"),
    ("pyroar-female", "pyroar"),
    ("meowstic-female", "meowstic"),
    ("indeedee-female", "indeedee"),
    ("oinkologne-female", "oinkologne"),
]

# Arceus elemental plates. Form change is triggered by Arceus holding the
# corresponding Plate item. Arceus is obtainable in BDSP/SP (Ramanas Park)
# and PLA (story); plates are collectible in all three. `arceus-unknown`
# (the "???"-type Curse variant) has been pruned from forms.json entirely
# — datamine-only, never distributed, and the "???" type was removed in
# Gen V so no HOME-reachable path exists.
ARCEUS_PLATE_TYPES: tuple[str, ...] = (
    "bug",
    "dark",
    "dragon",
    "electric",
    "fairy",
    "fighting",
    "fire",
    "flying",
    "ghost",
    "grass",
    "ground",
    "ice",
    "poison",
    "psychic",
    "rock",
    "steel",
    "water",
)
ARCEUS_PLATE_GAMES: tuple[str, ...] = (
    "brilliant-diamond",
    "shining-pearl",
    "legends-arceus",
)

# Silvally type memories. Form change is triggered by Silvally holding the
# corresponding Memory item. Memories are obtainable only in SM/USUM (Game
# Freak gift + Aether Foundation); the SwSh Silvally that appears in
# Dynamax Adventures has no in-game memories, so any memory form there
# requires HOME transfer rather than in-game acquisition.
SILVALLY_MEMORY_TYPES: tuple[str, ...] = (
    "bug",
    "dark",
    "dragon",
    "electric",
    "fairy",
    "fighting",
    "fire",
    "flying",
    "ghost",
    "grass",
    "ground",
    "ice",
    "poison",
    "psychic",
    "rock",
    "steel",
    "water",
)
SILVALLY_MEMORY_GAMES: tuple[str, ...] = ("sun", "moon", "ultra-sun", "ultra-moon")

# USUM totem-sized Pokémon. Each is redeemed from Samson Oak at Heahea
# Beach in exchange for Totem Stickers collected throughout Alola.
# USUM-only; SM had totem battles but did not distribute the totem-sized
# specimens as obtainable Pokémon.
USUM_TOTEM_FORMS: tuple[str, ...] = (
    "raticate-totem-alola",
    "marowak-totem",
    "gumshoos-totem",
    "vikavolt-totem",
    "ribombee-totem",
    "araquanid-totem",
    "lurantis-totem",
    "salazzle-totem",
    "togedemaru-totem",
    "mimikyu-totem-disguised",
    "kommo-o-totem",
)

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
    # SV wild-encounter gaps Bulbapedia's availability tables don't split
    # out as distinct form rows.
    # Squawkabilly alternate plumages — all three colour variants are wild
    # in Paldea alongside the default green.
    {
        "form_id": "squawkabilly-blue-plumage",
        "game_id": "scarlet",
        "method": "wild-encounter",
    },
    {
        "form_id": "squawkabilly-blue-plumage",
        "game_id": "violet",
        "method": "wild-encounter",
    },
    {
        "form_id": "squawkabilly-yellow-plumage",
        "game_id": "scarlet",
        "method": "wild-encounter",
    },
    {
        "form_id": "squawkabilly-yellow-plumage",
        "game_id": "violet",
        "method": "wild-encounter",
    },
    {
        "form_id": "squawkabilly-white-plumage",
        "game_id": "scarlet",
        "method": "wild-encounter",
    },
    {
        "form_id": "squawkabilly-white-plumage",
        "game_id": "violet",
        "method": "wild-encounter",
    },
    # Gimmighoul's overworld "Roaming" form — distinct HOME slot from the
    # default Chest form. Found walking around Paldea routes.
    {
        "form_id": "gimmighoul-roaming",
        "game_id": "scarlet",
        "method": "wild-encounter",
        "method_details": "roaming",
    },
    {
        "form_id": "gimmighoul-roaming",
        "game_id": "violet",
        "method": "wild-encounter",
        "method_details": "roaming",
    },
    # Bloodmoon Ursaluna — single Teal Mask DLC story encounter.
    {
        "form_id": "ursaluna-bloodmoon",
        "game_id": "scarlet",
        "method": "static-encounter",
        "method_details": "only-one",
        "requires_dlc": "hidden-treasure-of-area-zero",
        "notes": "Perrin quest encounter in Kitakami (Teal Mask DLC).",
    },
    {
        "form_id": "ursaluna-bloodmoon",
        "game_id": "violet",
        "method": "static-encounter",
        "method_details": "only-one",
        "requires_dlc": "hidden-treasure-of-area-zero",
        "notes": "Perrin quest encounter in Kitakami (Teal Mask DLC).",
    },
    # --- Tier 8: zero-source functional forms ---------------------------------
    # Remaining HOME-storable `functional` forms with no scraper-emitted
    # source rows. Divided by acquisition mechanic:
    #   (A) Burmy / Mothim alternate cloaks — environment-determined
    #   (B) Legendary form-change items — method=gift + item, following the
    #       plate / memory / mask / fusion convention
    #   (C) Meteorite-driven Deoxys formes — method=gift + from_form
    #   (D) Genesect Drive items — method=gift + item
    #   (E) Floette-Eternal — Legends Z-A story gift
    # -------------------------------------------------------------------------
    # (A) Burmy Sandy / Trash cloaks. Burmy's cloak changes after a battle
    # based on surroundings (rocky/sand → Sandy, buildings/structures → Trash,
    # everything else → Plant). In PLA the three cloaks spawn wild directly in
    # matching environments. Seed for every game where the default Plant
    # cloak is obtainable.
    *(
        {
            "form_id": "burmy-sandy",
            "game_id": game,
            "method": "wild-encounter",
            "notes": (
                "Cloak changes to Sandy after battles in rocky or sand-floored areas; "
                "in Legends: Arceus, also spawns directly in matching environments."
            ),
        }
        for game in ("x", "y", "brilliant-diamond", "shining-pearl", "legends-arceus")
    ),
    *(
        {
            "form_id": "burmy-trash",
            "game_id": game,
            "method": "wild-encounter",
            "notes": (
                "Cloak changes to Trash after battles inside buildings or structures; "
                "in Legends: Arceus, also spawns directly in matching environments."
            ),
        }
        for game in ("x", "y", "brilliant-diamond", "shining-pearl", "legends-arceus")
    ),
    # Mothim alternate cloaks: not visually distinct, but a male Burmy
    # retains its cloak value through evolution at level 20, producing a
    # cloak-tagged Mothim in HOME storage. Seeded in the same games the
    # default Mothim is obtainable.
    *(
        {
            "form_id": "mothim-sandy",
            "game_id": game,
            "method": "evolution",
            "method_details": "level-up",
            "from_form": "burmy-sandy",
            "gender": "male",
            "notes": "Cloak value carries over from the Sandy Cloak male Burmy it evolves from.",
        }
        for game in ("x", "y", "brilliant-diamond", "shining-pearl", "legends-arceus")
    ),
    *(
        {
            "form_id": "mothim-trash",
            "game_id": game,
            "method": "evolution",
            "method_details": "level-up",
            "from_form": "burmy-trash",
            "gender": "male",
            "notes": "Cloak value carries over from the Trash Cloak male Burmy it evolves from.",
        }
        for game in ("x", "y", "brilliant-diamond", "shining-pearl", "legends-arceus")
    ),
    # (B) Legendary form-change items. Encoded as method=gift with the
    # triggering item + from_form, matching the Arceus plate / Silvally
    # memory / Ogerpon mask / Kyurem-Calyrex-Necrozma fusion convention.
    # Only seeded for games where BOTH the base form and the triggering
    # item are obtainable in-game (transfers are covered by transfers.json).
    #
    # Dialga-Origin / Palkia-Origin: introduced in Legends: Arceus.
    # Adamant Crystal / Lustrous Globe are PLA rewards (Requests 89 / 90);
    # BDSP does not have the items in-game.
    {
        "form_id": "dialga-origin",
        "game_id": "legends-arceus",
        "method": "gift",
        "from_form": "dialga",
        "item": "adamant-crystal",
        "notes": "Adamant Crystal received as the reward for Request 89 at Lake Valor.",
    },
    {
        "form_id": "palkia-origin",
        "game_id": "legends-arceus",
        "method": "gift",
        "from_form": "palkia",
        "item": "lustrous-globe",
        "notes": "Lustrous Globe received as the reward for Request 90 at Pearl Settlement.",
    },
    # Giratina-Origin: Griseous Orb triggers the form change in every Gen
    # 6-8 game where it's obtainable; Legends: Arceus uses the Griseous Core.
    *(
        {
            "form_id": "giratina-origin",
            "game_id": game,
            "method": "gift",
            "from_form": "giratina",
            "item": "griseous-orb",
            "notes": notes,
        }
        for game, notes in (
            ("omega-ruby", "Griseous Orb on Route 130 (underwater)."),
            ("alpha-sapphire", "Griseous Orb on Route 130 (underwater)."),
            ("ultra-sun", "Griseous Orb from the Antiquities of the Ages shop in Hau'oli City."),
            ("ultra-moon", "Griseous Orb from the Antiquities of the Ages shop in Hau'oli City."),
            (
                "sword",
                "Griseous Orb from the Stow-on-Side bargain shop after completing the main story.",
            ),
            (
                "shield",
                "Griseous Orb from the Stow-on-Side bargain shop after completing the main story.",
            ),
            (
                "brilliant-diamond",
                "Griseous Orb at Ramanas Park after defeating Origin Forme Giratina.",
            ),
            (
                "shining-pearl",
                "Griseous Orb at Ramanas Park after defeating Origin Forme Giratina.",
            ),
        )
    ),
    {
        "form_id": "giratina-origin",
        "game_id": "legends-arceus",
        "method": "gift",
        "from_form": "giratina",
        "item": "griseous-core",
        "notes": "Form change triggered at Turnback Cave; Griseous Core available in-game.",
    },
    # Shaymin-Sky: only in-game path for the base Shaymin form is PLA
    # (Request 92, requires Sword/Shield save data). Gracidea also arrives
    # in PLA during that request.
    {
        "form_id": "shaymin-sky",
        "game_id": "legends-arceus",
        "method": "gift",
        "from_form": "shaymin",
        "item": "gracidea",
        "notes": "Gracidea received from Medi at Fieldlands Camp during Request 92.",
    },
    # Therian Formes: Reveal Glass toggles between Incarnate and Therian.
    # Seed for the intersection of each base legendary's availability and
    # Reveal Glass availability. Tornadus/Thundurus version-split across
    # ORAS and USUM; Landorus is present in both versions.
    {
        "form_id": "tornadus-therian",
        "game_id": "omega-ruby",
        "method": "gift",
        "from_form": "tornadus",
        "item": "reveal-glass",
        "notes": "Reveal Glass from the mirror shop on Mauville City 1F.",
    },
    {
        "form_id": "tornadus-therian",
        "game_id": "ultra-sun",
        "method": "gift",
        "from_form": "tornadus",
        "item": "reveal-glass",
        "notes": "Reveal Glass from Professor Burnet at the Dimensional Research Lab.",
    },
    {
        "form_id": "tornadus-therian",
        "game_id": "legends-arceus",
        "method": "gift",
        "from_form": "tornadus",
        "item": "reveal-glass",
        "notes": (
            "Reveal Glass from Cogita at Ancient Retreat after completing the "
            "Forces of Nature Pokédex entries."
        ),
    },
    {
        "form_id": "thundurus-therian",
        "game_id": "alpha-sapphire",
        "method": "gift",
        "from_form": "thundurus",
        "item": "reveal-glass",
        "notes": "Reveal Glass from the mirror shop on Mauville City 1F.",
    },
    {
        "form_id": "thundurus-therian",
        "game_id": "ultra-moon",
        "method": "gift",
        "from_form": "thundurus",
        "item": "reveal-glass",
        "notes": "Reveal Glass from Professor Burnet at the Dimensional Research Lab.",
    },
    {
        "form_id": "thundurus-therian",
        "game_id": "legends-arceus",
        "method": "gift",
        "from_form": "thundurus",
        "item": "reveal-glass",
        "notes": (
            "Reveal Glass from Cogita at Ancient Retreat after completing the "
            "Forces of Nature Pokédex entries."
        ),
    },
    *(
        {
            "form_id": "landorus-therian",
            "game_id": game,
            "method": "gift",
            "from_form": "landorus",
            "item": "reveal-glass",
            "notes": notes,
        }
        for game, notes in (
            ("omega-ruby", "Reveal Glass from the mirror shop on Mauville City 1F."),
            ("alpha-sapphire", "Reveal Glass from the mirror shop on Mauville City 1F."),
            ("ultra-sun", "Reveal Glass from Professor Burnet at the Dimensional Research Lab."),
            ("ultra-moon", "Reveal Glass from Professor Burnet at the Dimensional Research Lab."),
            (
                "sword",
                "Reveal Glass from the Stow-on-Side bargain shop after completing the main story.",
            ),
            (
                "shield",
                "Reveal Glass from the Stow-on-Side bargain shop after completing the main story.",
            ),
            (
                "legends-arceus",
                "Reveal Glass from Cogita at Ancient Retreat after completing the "
                "Forces of Nature Pokédex entries.",
            ),
        )
    ),
    {
        "form_id": "enamorus-therian",
        "game_id": "legends-arceus",
        "method": "gift",
        "from_form": "enamorus",
        "item": "reveal-glass",
        "notes": (
            "Reveal Glass from Cogita at Ancient Retreat after completing the "
            "Forces of Nature Pokédex entries."
        ),
    },
    # Hoopa-Unbound: Prison Bottle. Only in-scope game with a legitimate
    # base Hoopa encounter is ORAS (the 2015 event distribution already
    # seeded above). The clerk dispenses the Prison Bottle at any Poké Mart
    # once Hoopa is in the party.
    {
        "form_id": "hoopa-unbound",
        "game_id": "omega-ruby",
        "method": "gift",
        "from_form": "hoopa",
        "item": "prison-bottle",
        "notes": "Prison Bottle given by a clerk at any Poké Mart while Hoopa is in the party.",
    },
    {
        "form_id": "hoopa-unbound",
        "game_id": "alpha-sapphire",
        "method": "gift",
        "from_form": "hoopa",
        "item": "prison-bottle",
        "notes": "Prison Bottle given by a clerk at any Poké Mart while Hoopa is in the party.",
    },
    # Keldeo-Resolute: form changes automatically when Keldeo knows the move
    # Secret Sword. Only in-scope legitimate base encounter is the SwSh
    # Crown Tundra Dynamax Adventure.
    {
        "form_id": "keldeo-resolute",
        "game_id": "sword",
        "method": "gift",
        "from_form": "keldeo",
        "known_move": "secret-sword",
        "requires_dlc": "expansion-pass",
        "notes": "Form change triggered when Keldeo knows Secret Sword (tutor move).",
    },
    {
        "form_id": "keldeo-resolute",
        "game_id": "shield",
        "method": "gift",
        "from_form": "keldeo",
        "known_move": "secret-sword",
        "requires_dlc": "expansion-pass",
        "notes": "Form change triggered when Keldeo knows Secret Sword (tutor move).",
    },
    # Zygarde Power-Construct 10% / 50%: only the USUM Reassembly Unit (via
    # collected Zygarde Cells and Cores) and the SwSh Max Lair Dynamax
    # Adventure produce Power-Construct specimens. SM's Reassembly Unit
    # also exists at the Aether Paradise.
    *(
        {
            "form_id": "zygarde-10-power-construct",
            "game_id": game,
            "method": "gift",
            "from_form": "zygarde-10",
            "notes": (
                "Assembled at the Reassembly Unit at Aether Paradise; "
                "Power Construct requires using at least one Zygarde Core."
            ),
        }
        for game in ("sun", "moon", "ultra-sun", "ultra-moon")
    ),
    *(
        {
            "form_id": "zygarde-50-power-construct",
            "game_id": game,
            "method": "gift",
            "from_form": "zygarde",
            "notes": (
                "Assembled at the Reassembly Unit at Aether Paradise; "
                "Power Construct requires using at least one Zygarde Core."
            ),
        }
        for game in ("sun", "moon", "ultra-sun", "ultra-moon")
    ),
    {
        "form_id": "zygarde-50-power-construct",
        "game_id": "sword",
        "method": "raid",
        "method_details": "dynamax-adventure",
        "requires_dlc": "expansion-pass",
        "notes": (
            "Dynamax Adventure encounter in the Max Lair (Crown Tundra); "
            "Zygarde in Max Lair always has Power Construct."
        ),
    },
    {
        "form_id": "zygarde-50-power-construct",
        "game_id": "shield",
        "method": "raid",
        "method_details": "dynamax-adventure",
        "requires_dlc": "expansion-pass",
        "notes": (
            "Dynamax Adventure encounter in the Max Lair (Crown Tundra); "
            "Zygarde in Max Lair always has Power Construct."
        ),
    },
    # (C) Deoxys Attack / Defense / Speed Formes: form changes via
    # interacting with the Fallarbor Town meteorite at Professor Cozmo's
    # house. Cycles Normal → Attack → Defense → Speed → Normal per
    # interaction. ORAS is the only in-scope game where base Deoxys is
    # catchable and the meteorite exists.
    *(
        {
            "form_id": form_id,
            "game_id": game,
            "method": "gift",
            "from_form": "deoxys",
            "notes": (
                "Form cycled by interacting with the meteorite at Professor "
                "Cozmo's house in Fallarbor Town."
            ),
        }
        for form_id in ("deoxys-attack", "deoxys-defense", "deoxys-speed")
        for game in ("omega-ruby", "alpha-sapphire")
    ),
    # (D) Genesect Drive items. Each drive, when held, changes Genesect's
    # form (and the move Techno Blast's type). Only in-scope game with a
    # legitimate base Genesect encounter is the SwSh Crown Tundra Dynamax
    # Adventure; the bargain shop in Stow-on-Side sells all four drives
    # once the player owns a Genesect.
    *(
        {
            "form_id": f"genesect-{drive}",
            "game_id": game,
            "method": "gift",
            "from_form": "genesect",
            "item": f"{drive}-drive",
            "requires_dlc": "expansion-pass",
            "notes": (
                f"{drive.capitalize()} Drive from the Stow-on-Side bargain shop "
                "while the player owns a Genesect."
            ),
        }
        for drive in ("burn", "chill", "douse", "shock")
        for game in ("sword", "shield")
    ),
    # (E) Floette-Eternal: AZ's Floette, legitimately obtainable for the
    # first time in Legends: Z-A as a story gift from Taunie/Urbain after
    # completing Main Mission 39. One per save file.
    {
        "form_id": "floette-eternal",
        "game_id": "legends-za",
        "method": "gift",
        "method_details": "only-one",
        "notes": "Received from Taunie/Urbain upon completing Main Mission 39.",
    },
]


def _build_rows(existing: list[dict]) -> list[dict]:
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
    # Arceus plates: method=gift follows the fusion/hold-item convention
    # established by ogerpon masks and kyurem/calyrex fusions. Structured
    # `item` captures the plate slug; `from_form` preserves provenance.
    for plate_type in ARCEUS_PLATE_TYPES:
        for game in ARCEUS_PLATE_GAMES:
            rows.append(
                {
                    "form_id": f"arceus-{plate_type}",
                    "game_id": game,
                    "method": "gift",
                    "from_form": "arceus",
                    "item": f"{plate_type}-plate",
                    "notes": "Form change by holding the corresponding Plate.",
                }
            )
    # Silvally memories: same convention as arceus plates.
    for memory_type in SILVALLY_MEMORY_TYPES:
        for game in SILVALLY_MEMORY_GAMES:
            rows.append(
                {
                    "form_id": f"silvally-{memory_type}",
                    "game_id": game,
                    "method": "gift",
                    "from_form": "silvally",
                    "item": f"{memory_type}-memory",
                    "notes": "Form change by holding the corresponding Memory.",
                }
            )
    # USUM totem stickers: redeemable at Heahea Beach from Samson Oak.
    for totem_form in USUM_TOTEM_FORMS:
        for game in ("ultra-sun", "ultra-moon"):
            rows.append(
                {
                    "form_id": totem_form,
                    "game_id": game,
                    "method": "gift",
                    "notes": (
                        "Received from Samson Oak at Heahea Beach in exchange for Totem Stickers."
                    ),
                }
            )
    # Gender-difference females: mirror every existing row for the default
    # species with gender=female set. Only mirrors rows the scraper pass
    # has written, so keeps in sync with upstream and avoids hand-listing
    # per-game encounters.
    default_to_female = {default: female for female, default in GENDER_DIFFERENCE_PAIRS}
    for row in existing:
        default = row["form_id"]
        if default not in default_to_female:
            continue
        if row.get("gender") is not None:
            # Skip rows already gender-restricted (none exist today, but
            # defensive against future scraper additions).
            continue
        mirrored = dict(row)
        mirrored["form_id"] = default_to_female[default]
        mirrored["gender"] = "female"
        rows.append(mirrored)
    rows.extend(EXPLICIT_ROWS)
    return rows


def main() -> int:
    existing: list[dict] = []
    if SOURCES_PATH.exists():
        existing = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))

    new_rows = _build_rows(existing)
    adapter = TypeAdapter(list[Source])
    # Validate shape before merging.
    adapter.validate_python(new_rows)

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
