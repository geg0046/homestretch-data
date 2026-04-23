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

# Arceus plate forms, Silvally memory forms, and other held-item-triggered
# form changes are NOT tracked. Pokémon HOME strips held items on deposit,
# collapsing the Pokémon back to its default form in HOME storage, so those
# forms have no distinct HOME slot. See `scrapers/pokeapi.py`
# `SKIP_FORM_IDS_HOME_UNREACHABLE` for the complete list, and
# `reference_serebii_home_nondepositable` memory for the authoritative
# source (<https://www.serebii.net/pokemonhome/nondepositablepokemon.shtml>).

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
    # magearna-original: "Original Color" Magearna, awarded for completing
    # the National Dex in Pokémon HOME from SwSh (2020).
    {"form_id": "magearna-original", "game_id": "sword", "method": "event"},
    {"form_id": "magearna-original", "game_id": "shield", "method": "event"},
    # zarude-dada: serial-code distribution tied to the Coco movie (2020).
    {"form_id": "zarude-dada", "game_id": "sword", "method": "event"},
    {"form_id": "zarude-dada", "game_id": "shield", "method": "event"},
    # Let's Go partner Pikachu/Eevee and greninja-battle-bond (Ash-Greninja)
    # are NOT tracked — all three are on Serebii's HOME non-depositable list
    # (partners are permanently save-bound; Ash-Greninja is a listed form
    # change). See scrapers/pokeapi.py SKIP_FORM_IDS_HOME_UNREACHABLE.
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
    # Kyurem-Black/White, Calyrex-Ice/Shadow, Necrozma-Dawn/Dusk, and the
    # three Ogerpon mask forms are NOT tracked: all are on Serebii's HOME
    # non-depositable list. Fused forms cannot be deposited in HOME at all;
    # mask forms require a held item that HOME strips on deposit.
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
    #       plate / memory / mask convention
    #   (C) Meteorite-driven Deoxys formes — method=gift + from_form
    #   (D) Floette-Eternal — Legends Z-A story gift
    #
    # Giratina-Origin and Genesect Drive forms were evaluated and excluded:
    # HOME strips held items on deposit, and neither form has a save-data
    # flag that persists through HOME (Guidebook does not acknowledge
    # Genesect drives at all). Both are pruned from forms.json.
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
    # (B) Save-data-persistent form-change items. Encoded as method=gift
    # with the triggering item + from_form. Only seeded for games where
    # both the base form and the triggering item are obtainable in-game
    # (transfers are covered by transfers.json).
    #
    # Origin Formes (Dialga, Palkia, Giratina) are NOT tracked: all three
    # are on Serebii's HOME non-depositable list. See scrapers/pokeapi.py
    # SKIP_FORM_IDS_HOME_UNREACHABLE.
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
    # Genesect Drive forms are NOT tracked: Bulbapedia's Pokémon HOME article
    # explicitly lists "Genesect's drive forms" among forms "not acknowledged
    # at all" by the Pokémon Guidebook, and HOME strips the held Drive on
    # deposit. See scrapers/pokeapi.py SKIP_FORM_IDS_HOME_UNREACHABLE.
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
    # --- Tier 9: Gen 8/9 regional-dex gap closures -----------------------------
    # Closes species flagged by scripts/coverage_audit.py as missing from a
    # game's regional Pokédex. Three sub-buckets:
    #   (F) Version-exclusive trade rows — species catchable only on the
    #       paired version; method=trade documents the local-trade path.
    #   (G) SwSh Crown Tundra Max Lair (Dynamax Adventure) rows — species
    #       PokéAPI doesn't surface from the Max Lair encounter table.
    #   (H) Story / event rows PokéAPI / Bulbapedia scrapers missed:
    #       Ramanas Park post-game legendaries, Phione → Manaphy egg event,
    #       Legends: Z-A Mystery-Gift-gated encounters (Mewtwo, Diancie).
    # Species that appear in both paired versions' missing lists and have no
    # in-game acquisition in SwSh or SV are handled via a coverage_audit.py
    # exclusion set (HOME-transfer-only dex entries), not fake source rows.
    # -------------------------------------------------------------------------
    # (F) Version-exclusive trade rows. method=trade with no trade_species,
    # since the "trade partner" is simply the paired-version player.
    #
    # Sword ← Shield
    *(
        {
            "form_id": sp,
            "game_id": "sword",
            "method": "trade",
            "notes": "Version-exclusive; trade from Shield.",
        }
        for sp in (
            "corsola",
            "croagunk",
            "drampa",
            "eiscue",
            "goomy",
            "larvitar",
            "lotad",
            "lunatone",
            "oranguru",
            "ponyta",
            "sableye",
            "solosis",
            "spritzee",
            "vullaby",
            "zamazenta",
        )
    ),
    # Shield ← Sword
    *(
        {
            "form_id": sp,
            "game_id": "shield",
            "method": "trade",
            "notes": "Version-exclusive; trade from Sword.",
        }
        for sp in (
            "darumaka",
            "deino",
            "farfetchd",
            "gothita",
            "jangmo-o",
            "mawile",
            "passimian",
            "rufflet",
            "scraggy",
            "seedot",
            "solrock",
            "stonjourner",
            "swirlix",
            "turtonator",
            "zacian",
        )
    ),
    # Brilliant Diamond ← Shining Pearl (BD-exclusive fossil Shieldon, plus
    # SP-exclusive wilds). Palkia and Dialga are handled in bucket H via
    # Ramanas Park, not trade.
    *(
        {
            "form_id": sp,
            "game_id": "brilliant-diamond",
            "method": "trade",
            "notes": "Version-exclusive; trade from Shining Pearl.",
        }
        for sp in ("glameow", "misdreavus", "shieldon")
    ),
    # Shining Pearl ← Brilliant Diamond
    *(
        {
            "form_id": sp,
            "game_id": "shining-pearl",
            "method": "trade",
            "notes": "Version-exclusive; trade from Brilliant Diamond.",
        }
        for sp in ("cranidos", "gligar", "murkrow", "scyther", "stunky")
    ),
    # Scarlet ← Violet (includes box legendary Miraidon and all Violet-native
    # Iron paradoxes from tier 6).
    *(
        {
            "form_id": sp,
            "game_id": "scarlet",
            "method": "trade",
            "notes": "Version-exclusive; trade from Violet.",
        }
        for sp in (
            "bagon",
            "clauncher",
            "dreepy",
            "eiscue",
            "iron-boulder",
            "iron-bundle",
            "iron-crown",
            "iron-hands",
            "iron-jugulis",
            "iron-leaves",
            "iron-moth",
            "iron-thorns",
            "iron-treads",
            "iron-valiant",
            "miraidon",
            "misdreavus",
            "passimian",
        )
    ),
    # Violet ← Scarlet (includes Koraidon and all Scarlet-native ancient
    # paradoxes from tier 6).
    *(
        {
            "form_id": sp,
            "game_id": "violet",
            "method": "trade",
            "notes": "Version-exclusive; trade from Scarlet.",
        }
        for sp in (
            "brute-bonnet",
            "deino",
            "drifloon",
            "flutter-mane",
            "gouging-fire",
            "great-tusk",
            "koraidon",
            "larvitar",
            "oranguru",
            "raging-bolt",
            "roaring-moon",
            "sandy-shocks",
            "scream-tail",
            "skrelp",
            "slither-wing",
            "stonjourner",
            "stunky",
            "walking-wake",
        )
    ),
    # (G) SwSh Crown Tundra Max Lair (Dynamax Adventure) — species missing
    # from BOTH Sword and Shield because PokéAPI doesn't surface Max Lair
    # encounters. Max Lair is version-agnostic so both versions get rows.
    *(
        {
            "form_id": sp,
            "game_id": game,
            "method": "raid",
            "method_details": "dynamax-adventure",
            "requires_dlc": "expansion-pass",
            "notes": "Dynamax Adventure encounter in the Max Lair (Crown Tundra).",
        }
        for sp in ("bagon", "gible", "heracross", "kabuto", "omanyte", "pinsir")
        for game in ("sword", "shield")
    ),
    # (H) Story / event rows.
    #
    # BDSP Ramanas Park — post-game legendary slates. Each version catches
    # its counterpart mascot there (Dialga in SP, Palkia in BD) after the
    # player completes the National Pokédex. Method is static-encounter,
    # method_details=only-one (single available capture).
    {
        "form_id": "palkia",
        "game_id": "brilliant-diamond",
        "method": "static-encounter",
        "method_details": "only-one",
        "notes": "Ramanas Park Lustrous Slate after completing the National Pokédex.",
    },
    {
        "form_id": "dialga",
        "game_id": "shining-pearl",
        "method": "static-encounter",
        "method_details": "only-one",
        "notes": "Ramanas Park Adamant Slate after completing the National Pokédex.",
    },
    # BDSP Manaphy — Phione egg from Ramanas Park's Manaphy Slate hatches
    # into Phione in the normal case, but the unique Manaphy pre-event
    # gift (via serial code / mystery gift) provided a Manaphy egg in both
    # versions. Encoded as method=gift, method_details=only-one.
    {
        "form_id": "manaphy",
        "game_id": "brilliant-diamond",
        "method": "gift",
        "method_details": "only-one",
        "notes": "Manaphy Egg via Mystery Gift (2022 promotion).",
    },
    {
        "form_id": "manaphy",
        "game_id": "shining-pearl",
        "method": "gift",
        "method_details": "only-one",
        "notes": "Manaphy Egg via Mystery Gift (2022 promotion).",
    },
    # Legends: Z-A — Mystery-Gift-activated static encounters.
    {
        "form_id": "mewtwo",
        "game_id": "legends-za",
        "method": "static-encounter",
        "method_details": "only-one",
        "notes": "Magenta District Lysandre Labs; requires Mystery Gift activation.",
    },
    {
        "form_id": "diancie",
        "game_id": "legends-za",
        "method": "static-encounter",
        "method_details": "only-one",
        "notes": "Magenta Sector 8; requires Mystery Gift activation.",
    },
    # --- Tier 12: pre-Gen-8 regional-dex gap closures --------------------------
    # Extends `coverage_audit.py`'s regional-dex check to RBY / GSC / XY /
    # ORAS / SM / USUM / LGPE. Seeds four buckets:
    #   (I) Version-exclusive trade rows for paired RBY / GSC / XY / SM /
    #       USUM / LGPE games (same `method=trade` pattern as tier 9).
    #   (J) VC Mew distribution (2016 20th-anniversary serial code) —
    #       the only legitimate Mew source for Gen 1 and LGPE (Mew in
    #       LGPE ships with the Poké Ball Plus peripheral, distinct
    #       mechanic).
    #   (K) GSC Kanto-gate content: Prof. Oak's starter gift, Cerulean
    #       Cave Mewtwo, post-E4 legendary-bird statics (Articuno /
    #       Zapdos / Moltres), Ruins of Alph fossil revivals, and the
    #       Celebi VC 2018 distribution.
    #   (L) SM / USUM evolution gaps (Glaceon / Leafeon at Ice/Moss Rock;
    #       Weavile from Sneasel holding Razor Claw at night) —
    #       scrapers missed the location-gated Alola evolution rows.
    # Deferred to tier 13 (scraper extension): Gen 6 ORAS mass wild-
    # encounter gap (94 rows across both versions; PokéAPI ORAS encounter
    # coverage is systematically incomplete) and residual Gen 6/7 scraper
    # gaps (Friend Safari / Bug-Catching Contest / common wilds the
    # scraper omitted in XY/SM/USUM).
    # -------------------------------------------------------------------------
    # (I) Version-exclusive trade rows.
    #
    # Red/Blue: trades between the two games; Yellow's reward for choosing
    # Pikachu replaces several wild encounters, so its exclusions need
    # trades from whichever of R/B has the species.
    *(
        {
            "form_id": sp,
            "game_id": "red",
            "method": "trade",
            "notes": "Version-exclusive; trade from Blue.",
        }
        for sp in ("bellsprout", "meowth", "pinsir", "sandshrew", "vulpix")
    ),
    *(
        {
            "form_id": sp,
            "game_id": "blue",
            "method": "trade",
            "notes": "Version-exclusive; trade from Red.",
        }
        for sp in ("ekans", "growlithe", "mankey", "oddish", "scyther")
    ),
    # Yellow: missing Red-exclusives (ekans, weedle) and Blue-exclusives
    # (koffing, meowth). Trade from whichever version has the species.
    {
        "form_id": "ekans",
        "game_id": "yellow",
        "method": "trade",
        "notes": "Not catchable in Yellow; trade from Red.",
    },
    {
        "form_id": "weedle",
        "game_id": "yellow",
        "method": "trade",
        "notes": "Not catchable in Yellow (Pikachu starter swap); trade from Red.",
    },
    {
        "form_id": "koffing",
        "game_id": "yellow",
        "method": "trade",
        "notes": "Not catchable in Yellow; trade from Blue.",
    },
    {
        "form_id": "meowth",
        "game_id": "yellow",
        "method": "trade",
        "notes": "Not catchable in Yellow; trade from Blue.",
    },
    # Gold ↔ Silver trades (version-exclusives).
    *(
        {
            "form_id": sp,
            "game_id": "gold",
            "method": "trade",
            "notes": "Version-exclusive; trade from Silver.",
        }
        for sp in ("delibird", "ledyba", "meowth", "phanpy", "skarmory", "vulpix", "weedle")
    ),
    *(
        {
            "form_id": sp,
            "game_id": "silver",
            "method": "trade",
            "notes": "Version-exclusive; trade from Gold.",
        }
        for sp in ("caterpie", "gligar", "growlithe", "mankey", "spinarak", "teddiursa")
    ),
    # Crystal version-exclusives (not catchable wild in Crystal due to
    # encounter-table differences from Gold/Silver). Trade from whichever
    # of Gold/Silver has the species.
    {
        "form_id": "girafarig",
        "game_id": "crystal",
        "method": "trade",
        "notes": "Not catchable in Crystal; trade from Gold or Silver.",
    },
    {
        "form_id": "mankey",
        "game_id": "crystal",
        "method": "trade",
        "notes": "Not catchable in Crystal; trade from Gold.",
    },
    {
        "form_id": "mareep",
        "game_id": "crystal",
        "method": "trade",
        "notes": "Not catchable in Crystal; trade from Gold or Silver.",
    },
    {
        "form_id": "remoraid",
        "game_id": "crystal",
        "method": "trade",
        "notes": "Not catchable in Crystal; trade from Gold or Silver.",
    },
    {
        "form_id": "vulpix",
        "game_id": "crystal",
        "method": "trade",
        "notes": "Not catchable in Crystal; trade from Silver.",
    },
    # X ↔ Y trades (version-exclusives where the paired version has a row).
    *(
        {
            "form_id": sp,
            "game_id": "x",
            "method": "trade",
            "notes": "Version-exclusive; trade from Y.",
        }
        for sp in ("electrike", "larvitar", "shellder", "skrelp")
    ),
    *(
        {
            "form_id": sp,
            "game_id": "y",
            "method": "trade",
            "notes": "Version-exclusive; trade from X.",
        }
        for sp in ("aron", "clauncher", "houndour", "staryu")
    ),
    # Sun ↔ Moon trades.
    *(
        {
            "form_id": sp,
            "game_id": "sun",
            "method": "trade",
            "notes": "Version-exclusive; trade from Moon.",
        }
        for sp in (
            "celesteela",
            "drampa",
            "oranguru",
            "petilil",
            "pheromosa",
            "sandshrew",
            "vullaby",
        )
    ),
    *(
        {
            "form_id": sp,
            "game_id": "moon",
            "method": "trade",
            "notes": "Version-exclusive; trade from Sun.",
        }
        for sp in (
            "buzzwole",
            "cottonee",
            "kartana",
            "passimian",
            "rufflet",
            "turtonator",
            "vulpix",
        )
    ),
    # Ultra Sun ↔ Ultra Moon trades.
    *(
        {
            "form_id": sp,
            "game_id": "ultra-sun",
            "method": "trade",
            "notes": "Version-exclusive; trade from Ultra Moon.",
        }
        for sp in (
            "baltoy",
            "celesteela",
            "drampa",
            "electrike",
            "oranguru",
            "petilil",
            "pheromosa",
            "sandshrew",
            "stakataka",
            "vullaby",
        )
    ),
    *(
        {
            "form_id": sp,
            "game_id": "ultra-moon",
            "method": "trade",
            "notes": "Version-exclusive; trade from Ultra Sun.",
        }
        for sp in (
            "blacephalon",
            "buzzwole",
            "cottonee",
            "golett",
            "houndour",
            "kartana",
            "passimian",
            "rufflet",
            "turtonator",
            "vulpix",
        )
    ),
    # Let's Go Pikachu ↔ Eevee trades (cross-version exclusives).
    *(
        {
            "form_id": sp,
            "game_id": "lets-go-pikachu",
            "method": "trade",
            "notes": "Version-exclusive; trade from Let's Go Eevee.",
        }
        for sp in ("bellsprout", "ekans", "koffing", "meowth", "pinsir", "vulpix")
    ),
    *(
        {
            "form_id": sp,
            "game_id": "lets-go-eevee",
            "method": "trade",
            "notes": "Version-exclusive; trade from Let's Go Pikachu.",
        }
        for sp in ("grimer", "growlithe", "mankey", "oddish", "sandshrew", "scyther")
    ),
    # (J) VC Mew + LGPE Mew.
    # Gen 1 VC Mew was distributed via serial code for the 2016 Pokémon
    # 20th-anniversary event.
    *(
        {
            "form_id": "mew",
            "game_id": game,
            "method": "event",
            "notes": "Pokémon 20th-anniversary Virtual Console distribution (2016).",
        }
        for game in ("red", "blue", "yellow")
    ),
    # GSC VC Mew was distributed via serial code for the Gen 2 VC launch
    # (2018).
    *(
        {
            "form_id": "mew",
            "game_id": game,
            "method": "event",
            "notes": "Virtual Console Gen 2 launch serial-code distribution (2018).",
        }
        for game in ("gold", "silver", "crystal")
    ),
    # LGPE Mew ships with the Poké Ball Plus peripheral — redeemed once
    # per save file.
    {
        "form_id": "mew",
        "game_id": "lets-go-pikachu",
        "method": "gift",
        "method_details": "only-one",
        "notes": "Redeemed from the Poké Ball Plus peripheral (one per save).",
    },
    {
        "form_id": "mew",
        "game_id": "lets-go-eevee",
        "method": "gift",
        "method_details": "only-one",
        "notes": "Redeemed from the Poké Ball Plus peripheral (one per save).",
    },
    # (K) GSC Kanto-gate content (post-E4 Kanto access) and Gen 2 fossils.
    #
    # Prof. Oak gifts one of the three Kanto starters after the player
    # defeats Red on Mt. Silver. Single choice per save.
    *(
        {
            "form_id": starter,
            "game_id": game,
            "method": "gift",
            "method_details": "only-one",
            "notes": "Prof. Oak's starter gift in Pallet Town after defeating Red.",
        }
        for starter in ("bulbasaur", "charmander", "squirtle")
        for game in ("gold", "silver", "crystal")
    ),
    # Mewtwo lives in Cerulean Cave in post-E4 Kanto in all three games.
    *(
        {
            "form_id": "mewtwo",
            "game_id": game,
            "method": "static-encounter",
            "method_details": "only-one",
            "notes": "Cerulean Cave in post-game Kanto.",
        }
        for game in ("gold", "silver", "crystal")
    ),
    # Articuno at Seafoam Islands; Zapdos at the Power Plant.
    *(
        {
            "form_id": "articuno",
            "game_id": game,
            "method": "static-encounter",
            "method_details": "only-one",
            "notes": "Seafoam Islands in post-game Kanto.",
        }
        for game in ("gold", "silver", "crystal")
    ),
    *(
        {
            "form_id": "zapdos",
            "game_id": game,
            "method": "static-encounter",
            "method_details": "only-one",
            "notes": "Power Plant in post-game Kanto.",
        }
        for game in ("gold", "silver", "crystal")
    ),
    # Moltres at Mt. Silver exterior entrance in all three games.
    *(
        {
            "form_id": "moltres",
            "game_id": game,
            "method": "static-encounter",
            "method_details": "only-one",
            "notes": "Mt. Silver in post-game Kanto.",
        }
        for game in ("gold", "silver", "crystal")
    ),
    # Ruins of Alph fossil revivals. Helix Fossil → Omanyte from the
    # Aerodactyl-chamber puzzle, Dome Fossil → Kabuto from the Kabuto
    # puzzle. Revived by the scientist at Union Cave's fossil lab.
    *(
        {
            "form_id": "kabuto",
            "game_id": game,
            "method": "fossil-revive",
            "item": "dome-fossil",
            "notes": "Dome Fossil from the Kabuto chamber puzzle at the Ruins of Alph.",
        }
        for game in ("gold", "silver", "crystal")
    ),
    *(
        {
            "form_id": "omanyte",
            "game_id": game,
            "method": "fossil-revive",
            "item": "helix-fossil",
            "notes": "Helix Fossil from the Aerodactyl chamber puzzle at the Ruins of Alph.",
        }
        for game in ("gold", "silver", "crystal")
    ),
    # Celebi was distributed via serial code at the 2018 GSC VC launch.
    *(
        {
            "form_id": "celebi",
            "game_id": game,
            "method": "event",
            "notes": "Virtual Console Gen 2 launch serial-code distribution (2018).",
        }
        for game in ("gold", "silver", "crystal")
    ),
    # (L) SM / USUM location-gated evolutions that the scraper missed.
    #
    # Eevee → Glaceon at the Ice Rock in Mount Lanakila; Eevee → Leafeon
    # at the Moss Rock at Lush Jungle. Available in both SM and USUM.
    *(
        {
            "form_id": "glaceon",
            "game_id": game,
            "method": "evolution",
            "method_details": "level-up",
            "location": "mount-lanakila",
            "from_form": "eevee",
        }
        for game in ("sun", "moon", "ultra-sun", "ultra-moon")
    ),
    *(
        {
            "form_id": "leafeon",
            "game_id": game,
            "method": "evolution",
            "method_details": "level-up",
            "location": "lush-jungle",
            "from_form": "eevee",
        }
        for game in ("sun", "moon", "ultra-sun", "ultra-moon")
    ),
    # Sneasel → Weavile: level up at night while holding Razor Claw.
    *(
        {
            "form_id": "weavile",
            "game_id": game,
            "method": "evolution",
            "method_details": "level-up",
            "held_item": "razor-claw",
            "time_of_day": "night",
            "from_form": "sneasel",
        }
        for game in ("sun", "moon", "ultra-sun", "ultra-moon")
    ),
    # (M) Residual Gen 2 / Gen 7 scraper gaps — common wilds the
    # PokéAPI/Bulbapedia scrapers missed.
    #
    # GSC: Pinsir and Scyther only available via the National Park
    # Bug-Catching Contest in Johto. Not emitted by the scraper.
    *(
        {
            "form_id": sp,
            "game_id": game,
            "method": "wild-encounter",
            "notes": "Bug-Catching Contest reward at the Johto National Park.",
        }
        for sp in ("pinsir", "scyther")
        for game in ("gold", "silver", "crystal")
    ),
    # SM carvanha: Sun-exclusive SOS from Sharpedo on Melemele Sea.
    {
        "form_id": "carvanha",
        "game_id": "sun",
        "method": "wild-encounter",
        "method_details": "sos-encounter",
        "notes": "SOS call from Sharpedo on Melemele Sea (Sun-exclusive).",
    },
    {
        "form_id": "carvanha",
        "game_id": "moon",
        "method": "trade",
        "notes": "Version-exclusive; trade from Sun.",
    },
    # Gible SOS (rare) from Sandile in Haina Desert, all four Alola games.
    *(
        {
            "form_id": "gible",
            "game_id": game,
            "method": "wild-encounter",
            "method_details": "sos-encounter",
            "notes": "Rare SOS call from Sandile in Haina Desert.",
        }
        for game in ("sun", "moon", "ultra-sun", "ultra-moon")
    ),
    # Bounsweet in Lush Jungle (USUM wild).
    *(
        {
            "form_id": "bounsweet",
            "game_id": game,
            "method": "wild-encounter",
            "notes": "Lush Jungle tall grass.",
        }
        for game in ("ultra-sun", "ultra-moon")
    ),
    # Sandile in Haina Desert (USUM wild). Already wild in SM; scraper
    # missed the USUM rows.
    *(
        {
            "form_id": "sandile",
            "game_id": game,
            "method": "wild-encounter",
            "notes": "Haina Desert sand patches.",
        }
        for game in ("ultra-sun", "ultra-moon")
    ),
    # Alolan Geodude in USUM (Ten Carat Hill and route caves). The
    # Alolan variant fulfills the Alola regional dex entry for geodude;
    # scraper missed the USUM wild rows.
    *(
        {
            "form_id": "geodude-alola",
            "game_id": game,
            "method": "wild-encounter",
            "notes": "Ten Carat Hill caves and Melemele Island routes.",
        }
        for game in ("ultra-sun", "ultra-moon")
    ),
    # Magearna in SM: QR Scanner serial-code event (2017) after beating
    # the main story. USUM rows already exist.
    *(
        {
            "form_id": "magearna",
            "game_id": game,
            "method": "event",
            "notes": "2017 QR Scanner serial-code distribution after completing the main story.",
        }
        for game in ("sun", "moon")
    ),
    # XY wild-encounter scraper gap. The following 19 species are
    # catchable wild on specific Kalos routes in both X and Y but were
    # never emitted by PokéAPI/Bulbapedia encounter parsing. Seed as
    # wild-encounter for both versions.
    *(
        {
            "form_id": sp,
            "game_id": game,
            "method": "wild-encounter",
            "notes": notes,
        }
        for sp, notes in (
            ("buizel", "Route 22 / Azure Bay wilds."),
            ("deino", "Terminus Cave."),
            ("diglett", "Connecting Cave."),
            ("gastly", "Route 14 tall grass at night."),
            ("gothita", "Route 19."),
            ("lotad", "Route 3."),
            ("magnemite", "Route 10."),
            ("makuhita", "Cyllage City / Route 8."),
            ("patrat", "Route 2 / Route 22."),
            ("poochyena", "Route 2."),
            ("purrloin", "Route 3."),
            ("spinarak", "Santalune Forest."),
            ("surskit", "Route 3 (water)."),
            ("swinub", "Frost Cavern."),
            ("timburr", "Route 18."),
            ("trubbish", "Lost Hotel."),
            ("venipede", "Route 5."),
            ("voltorb", "Route 10."),
            ("zorua", "Route 20 (Winding Woods)."),
        )
        for game in ("x", "y")
    ),
    # XY Rotom: wild-encounter in the Lost Hotel (single rare spawn).
    *(
        {
            "form_id": "rotom",
            "game_id": game,
            "method": "wild-encounter",
            "notes": "Lost Hotel (rare).",
        }
        for game in ("x", "y")
    ),
    # XY Aerodactyl: Old Amber fossil revival at Ambrette Town Fossil Lab.
    *(
        {
            "form_id": "aerodactyl",
            "game_id": game,
            "method": "fossil-revive",
            "item": "old-amber",
            "notes": "Old Amber at the Ambrette Town Fossil Lab.",
        }
        for game in ("x", "y")
    ),
    # XY Weavile: Sneasel → Weavile via Razor Claw at night.
    *(
        {
            "form_id": "weavile",
            "game_id": game,
            "method": "evolution",
            "method_details": "level-up",
            "held_item": "razor-claw",
            "time_of_day": "night",
            "from_form": "sneasel",
        }
        for game in ("x", "y")
    ),
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
