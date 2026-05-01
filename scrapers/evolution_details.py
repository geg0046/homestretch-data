"""Map a PokeAPI evolution_detail dict into Source field kwargs.

PokeAPI's `evolution_detail` carries 20+ fields describing a single
evolution path. The Source schema splits these into structured fields
(item, held_item, location, known_move, …) so the app can filter and
display them without parsing prose.

One PokeAPI `evolution_detail` dict → one Source row (per applicable
game). Alternative paths (Crabominable has two: `level-up` at Mt.
Lanakila, `use-item` ice-stone) each produce their own row.

Per-game gating lives in LOCATION_TO_GAMES and ITEM_TO_GAMES below:
when `location` or `item`/`held_item` is set, emit only for games that
actually expose that path. Base species scope is still
POKEAPI.POKEDEX_TO_GAMES — this is a further narrowing.
"""

from __future__ import annotations

from typing import Any

from homestretch_data.models import Method

# Evolution triggers whose name should go into method_details as-is.
# Any PokeAPI trigger.name outside this set still emits, but logs a
# warning so new triggers don't silently leak.
KNOWN_TRIGGERS: frozenset[str] = frozenset(
    {
        "level-up",
        "trade",
        "use-item",
        "shed",
        "spin",
        "tower-of-darkness",
        "tower-of-waters",
        "three-critical-hits",
        "take-damage",
        "recoil-damage",
        "agile-style-move",
        "strong-style-move",
        "other",
        # Gen 9 additions: Gholdengo (Gimmighoul-coins), Kingambit
        # (defeat 3 leader's-crest Bisharp), Annihilape (use Rage Fist 20x).
        "gimmmighoul-coins",
        "three-defeated-bisharp",
        "use-move",
    }
)

# Location-gated evolution paths: the `location` field names a place that
# only exists in certain games, so those paths only apply in those games.
# Keys are PokeAPI location.name slugs. If a location appears in an
# evolution_detail but isn't listed here, we log a warning and fall back
# to species scope.
LOCATION_TO_GAMES: dict[str, frozenset[str]] = {
    # Gen 4 - Sinnoh (BDSP remakes only; DP/Pt out of scope)
    "mt-coronet": frozenset({"brilliant-diamond", "shining-pearl"}),
    "sinnoh-route-217": frozenset({"brilliant-diamond", "shining-pearl"}),
    "eterna-forest": frozenset({"brilliant-diamond", "shining-pearl"}),
    # Gen 5 - Unova (BW/BW2 out of scope, so paths keyed here emit nowhere).
    "pinwheel-forest": frozenset(),
    "chargestone-cave": frozenset(),
    "twist-mountain": frozenset(),
    # Gen 6 - Kalos
    "kalos-route-13": frozenset({"x", "y"}),
    "kalos-route-14": frozenset({"x", "y"}),
    "kalos-route-20": frozenset({"x", "y"}),
    "frost-cavern": frozenset({"x", "y"}),
    "mt-pyre": frozenset({"omega-ruby", "alpha-sapphire"}),
    # Gen 7 - Alola
    "mount-lanakila": frozenset({"sun", "moon", "ultra-sun", "ultra-moon"}),
    "blush-mountain": frozenset({"sun", "moon", "ultra-sun", "ultra-moon"}),
    "vast-poni-canyon": frozenset({"sun", "moon", "ultra-sun", "ultra-moon"}),
    # Gen 8 - Galar
    "giants-cap": frozenset({"sword", "shield"}),
    "lake-of-outrage": frozenset({"sword", "shield"}),
    "tower-of-darkness": frozenset({"sword", "shield"}),
    "tower-of-waters": frozenset({"sword", "shield"}),
    # Gen 9 / Paldea
    "area-zero": frozenset({"scarlet", "violet"}),
}

# Item-gated evolution paths: some evolution items are game-restricted
# (regional exclusives, DLC-only stones, generation-specific items).
# Unrestricted items are simply absent — falling back to species scope.
ITEM_TO_GAMES: dict[str, frozenset[str]] = {
    # Hisui / Legends Arceus exclusives
    "black-augurite": frozenset({"legends-arceus"}),
    "peat-block": frozenset({"legends-arceus"}),
    "razor-claw": frozenset(  # available broadly; no gating needed
        {
            "brilliant-diamond",
            "shining-pearl",
            "sword",
            "shield",
            "scarlet",
            "violet",
            "legends-arceus",
            "legends-za",
        }
    ),
    # Galar SwSh exclusives
    "galarica-cuff": frozenset({"sword", "shield"}),
    "galarica-wreath": frozenset({"sword", "shield"}),
    "sweet-apple": frozenset({"sword", "shield", "scarlet", "violet"}),
    "tart-apple": frozenset({"sword", "shield", "scarlet", "violet"}),
    "cracked-pot": frozenset({"sword", "shield", "scarlet", "violet"}),
    "chipped-pot": frozenset({"sword", "shield", "scarlet", "violet"}),
    # SV exclusives
    "auspicious-armor": frozenset({"scarlet", "violet"}),
    "malicious-armor": frozenset({"scarlet", "violet"}),
    "scroll-of-darkness": frozenset({"scarlet", "violet"}),
    "scroll-of-waters": frozenset({"scarlet", "violet"}),
    "metal-alloy": frozenset({"scarlet", "violet"}),
    "syrupy-apple": frozenset({"scarlet", "violet"}),
    "unremarkable-teacup": frozenset({"scarlet", "violet"}),
    "masterpiece-teacup": frozenset({"scarlet", "violet"}),
    # Linking Cord: trade-evolution item; SwSh+ / BDSP / SV / LA
    "linking-cord": frozenset(
        {
            "sword",
            "shield",
            "brilliant-diamond",
            "shining-pearl",
            "scarlet",
            "violet",
            "legends-arceus",
        }
    ),
    # Ice Stone: exists broadly (Gen 7+), so don't restrict item availability
    # here. Specific paths that only work in later games (e.g. Crabrawler
    # → Crabominable is SV-only despite the stone existing since USUM)
    # are narrowed in EVOLUTION_PATH_GAMES below.
}


# Game sets used by EVOLUTION_PATH_GAMES. Expressed as "games where this
# path's mechanics are available," and intersected with species scope at
# emit time — so listing a game here is harmless when the form isn't in
# that game's dex.
#
# BDSP is excluded from the Gen-8 stone-path set: it's a DP remake that
# retained the original location-based mechanics (Ice Rock, Moss Rock,
# Mt. Coronet magnetic field) even though Ice / Leaf / Thunder Stones
# are purchasable at Veilstone Department Store. Eevee won't evolve
# into Glaceon / Leafeon with a stone in BDSP, and Magneton / Nosepass
# still need Mt. Coronet. SwSh, LA, SV, and LZ all support the stone
# methods.
_GEN_8_PLUS_STONE_PATHS = frozenset(
    {
        "sword",
        "shield",
        "legends-arceus",
        "scarlet",
        "violet",
        "legends-za",
    }
)
_GEN_9_PLUS = frozenset({"scarlet", "violet", "legends-za"})


# Evolution paths whose *availability in a game* is narrower than the
# underlying item's availability. Keyed by (evolved_form_id, item_slug);
# applies when either `item` or `held_item` on the detail matches.
#
# These are cases where a species predates the introduction of a new
# evolution path via an item. The item may have existed earlier, but the
# specific path from pre-evo to this form was bolted on later — PokéAPI
# emits the detail without that distinction, so we correct it here.
#
# Add an entry when an evolution_detail ships in games where the path
# doesn't actually work. Verify against Bulbapedia's evobox per-game
# availability before adding.
EVOLUTION_PATH_GAMES: dict[tuple[str, str], frozenset[str]] = {
    # Crabrawler → Crabominable via Ice Stone: Gen 9 addition (SV).
    # Gen 7 path is level-up at Mount Lanakila (handled via LOCATION_TO_GAMES).
    ("crabominable", "ice-stone"): _GEN_9_PLUS,
    # Gen 8 (SwSh) added stone-based alternatives to previously location-
    # or magnetic-field-gated evolutions. Earlier games retained the
    # location paths (handled via LOCATION_TO_GAMES entries above), and
    # BDSP intentionally didn't port the stone methods despite selling
    # the stones in-game — see _GEN_8_PLUS_STONE_PATHS above.
    ("glaceon", "ice-stone"): _GEN_8_PLUS_STONE_PATHS,
    ("leafeon", "leaf-stone"): _GEN_8_PLUS_STONE_PATHS,
    ("magnezone", "thunder-stone"): _GEN_8_PLUS_STONE_PATHS,
    ("probopass", "thunder-stone"): _GEN_8_PLUS_STONE_PATHS,
}


def detail_to_source_fields(detail: dict[str, Any]) -> dict[str, Any]:
    """Extract structured Source kwargs from a PokeAPI evolution_detail.

    Returns a dict ready to splat into Source(...) / a row dict. Always
    sets `method_details` (the trigger name) when the trigger is known.
    None-valued PokeAPI fields are omitted from the result so JSON
    serialization doesn't emit noisy null keys.
    """
    out: dict[str, Any] = {}

    trigger = detail.get("trigger", {}).get("name")
    if trigger:
        out["method_details"] = trigger

    def _named(key: str) -> str | None:
        obj = detail.get(key)
        if obj and isinstance(obj, dict):
            return obj.get("name")
        return None

    if v := _named("item"):
        out["item"] = v
    if v := _named("held_item"):
        out["held_item"] = v
    if v := _named("location"):
        out["location"] = v
    if v := _named("known_move"):
        out["known_move"] = v
    if v := _named("known_move_type"):
        out["known_move_type"] = v
    if v := _named("trade_species"):
        out["trade_species"] = v
    if v := _named("party_species"):
        out["party_species"] = v
    if v := _named("party_type"):
        out["party_type"] = v

    tod = detail.get("time_of_day")
    if tod:
        out["time_of_day"] = tod

    gender_code = detail.get("gender")
    if gender_code == 1:
        out["gender"] = "female"
    elif gender_code == 2:
        out["gender"] = "male"

    for num_field in ("min_happiness", "min_affection", "min_beauty"):
        v = detail.get(num_field)
        if v is not None:
            out[num_field] = v

    rps = detail.get("relative_physical_stats")
    if rps == 1:
        out["relative_physical_stats"] = "atk-gt-def"
    elif rps == -1:
        out["relative_physical_stats"] = "atk-lt-def"
    elif rps == 0:
        out["relative_physical_stats"] = "atk-eq-def"

    if detail.get("needs_overworld_rain"):
        out["needs_overworld_rain"] = True
    if detail.get("turn_upside_down"):
        out["turn_upside_down"] = True
    if detail.get("needs_multiplayer"):
        out["needs_multiplayer"] = True

    return out


def method_for_trigger(trigger: str) -> Method:
    """Map a PokeAPI trigger slug to a Source.method enum value."""
    return Method.TRADE if trigger == "trade" else Method.EVOLUTION


# Species with branched forms PokeAPI's evolution_chain endpoint doesn't track.
# The endpoint attributes every detail to the default form_id; we need to fan
# each detail out across the correct branched form(s). Two cases:
#
# 1. "Encoded" branches — the detail carries enough info to pick the branch
#    deterministically (urshifu's trigger, lycanroc's time_of_day,
#    basculegion's gender). Handled inline in resolve_branched_form_ids.
# 2. "Unencoded" branches — the detail is identical across all branches; the
#    branch is determined by pre-evolution state the detail doesn't expose
#    (wormadam's cloak, gourgeist's size, toxtricity's nature, dudunsparce's
#    RNG). Listed here; the scraper emits the row for every listed form.
UNENCODED_BRANCH_FORMS: dict[str, tuple[str, ...]] = {
    "wormadam": ("wormadam", "wormadam-sandy", "wormadam-trash"),
    "gourgeist": ("gourgeist", "gourgeist-small", "gourgeist-large", "gourgeist-super"),
    "toxtricity": ("toxtricity", "toxtricity-low-key"),
    "dudunsparce": ("dudunsparce", "dudunsparce-three-segment"),
    # Basculegion is gender-branched but PokéAPI's evolution_detail leaves
    # `gender: null` on the basculin → basculegion path. Both forms share the
    # recoil-damage trigger; the caller's pre-evolution gender determines
    # which one they get.
    "basculegion": ("basculegion", "basculegion-female"),
}


# Regional-variant evolution paths PokéAPI conflates into the default
# child species's chain. PokéAPI's `evolution_chain` endpoint emits one
# `evolution_details` list per child species id, even when separate
# entries describe paths that only apply to a regional variant of that
# species (e.g. rattata-alola → raticate-alola needs `time_of_day=night`,
# while rattata → raticate is unconditional level-up). Both details get
# attributed to the default form unless filtered.
#
# Each entry maps default-form id → list of (signature, owner_form_id).
# When an emitted detail's structured fields match every key/value in
# `signature`, the detail is dropped from the default form. The owner
# regional form picks up the path via the Bulbapedia regional-trigger
# override pass (`_REGIONAL_TRIGGER_OVERRIDES` in bulbapedia.py).
#
# Verified against Bulbapedia + Serebii. Add an entry only when a known
# regional variant has a strictly distinguishing evolution criterion;
# variants that share the default's path (alolan dugtrio, galarian
# rapidash, etc.) don't need an entry.
REGIONAL_VARIANT_DETAILS: dict[str, list[tuple[dict[str, Any], str]]] = {
    "raticate": [({"time_of_day": "night"}, "raticate-alola")],
    "marowak": [({"time_of_day": "night"}, "marowak-alola")],
    "ninetales": [({"item": "ice-stone"}, "ninetales-alola")],
    "sandslash": [({"item": "ice-stone"}, "sandslash-alola")],
    "persian": [({"min_happiness": 160}, "persian-alola")],
}


def detail_belongs_to_regional_variant(evolved_species_id: str, fields: dict[str, Any]) -> bool:
    """True iff `fields` matches a regional-variant signature for this species.

    `fields` is the structured output of `detail_to_source_fields`. A
    match requires every key in the signature dict to be present and
    equal in `fields`; extra unrelated keys in `fields` don't disqualify
    the match.
    """
    for cond, _ in REGIONAL_VARIANT_DETAILS.get(evolved_species_id, ()):
        if all(fields.get(k) == v for k, v in cond.items()):
            return True
    return False


def resolve_branched_form_ids(
    species_id: str,
    detail: dict[str, Any],
    valid_form_ids: set[str],
) -> set[str]:
    """Return every form_id this evolution_detail should emit a row for.

    For most species, PokeAPI's evolution_chain attribution to the default
    form is correct, so we return `{species_id}`. Species with branched
    evolutions (see UNENCODED_BRANCH_FORMS and the inline encoded-branch
    rules) return a different set.
    """
    if species_id == "urshifu":
        trigger = (detail.get("trigger") or {}).get("name")
        form_id = "urshifu-rapid-strike" if trigger == "tower-of-waters" else "urshifu"
        return {form_id} & valid_form_ids

    if species_id == "lycanroc":
        tod_to_form = {
            "day": "lycanroc",
            "night": "lycanroc-midnight",
            "dusk": "lycanroc-dusk",
        }
        form_id = tod_to_form.get(detail.get("time_of_day"), "lycanroc")
        return {form_id} & valid_form_ids

    if species_id in UNENCODED_BRANCH_FORMS:
        return set(UNENCODED_BRANCH_FORMS[species_id]) & valid_form_ids

    return {species_id} & valid_form_ids


def gate_games(
    detail_fields: dict[str, Any],
    species_scope: set[str],
    evolved_form_id: str,
) -> set[str]:
    """Narrow species-level game scope based on path / location / item availability.

    `species_scope` is the set of games where the evolved species is
    obtainable at all (derived from pokedex membership). This function
    intersects with:

    1. LOCATION_TO_GAMES — a location slug only exists in certain games.
    2. ITEM_TO_GAMES — some items are game-restricted (regional exclusives).
    3. EVOLUTION_PATH_GAMES — some evolution paths via a specific item were
       added later than the species; this narrows the (form, item) pair
       beyond what item-availability alone would suggest.

    Returns species_scope unchanged when none of the structured fields
    match a restriction entry.
    """
    scope = set(species_scope)
    loc = detail_fields.get("location")
    if loc and loc in LOCATION_TO_GAMES:
        scope &= LOCATION_TO_GAMES[loc]
    for item_key in ("item", "held_item"):
        item = detail_fields.get(item_key)
        if not item:
            continue
        if item in ITEM_TO_GAMES:
            scope &= ITEM_TO_GAMES[item]
        path_key = (evolved_form_id, item)
        if path_key in EVOLUTION_PATH_GAMES:
            scope &= EVOLUTION_PATH_GAMES[path_key]
    return scope
