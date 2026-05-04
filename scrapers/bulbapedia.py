"""Scrape mainline-game source rows from Bulbapedia's per-species Game locations.

Usage:
    uv run python scrapers/bulbapedia.py --mode sources    --max-dex 1025
    uv run python scrapers/bulbapedia.py --mode evolutions --max-dex 1025
    uv run python scrapers/bulbapedia.py --mode locations  --max-dex 1025

Fetches wikitext via the MediaWiki `api.php` endpoint (no HTML scraping).
Respects a 1 req/sec rate limit, caches responses under .cache/bulbapedia/,
and merges new rows into data/sources.json additively (existing rows are
preserved; sources mode never replaces PokéAPI-sourced rows).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from method_details import _normalize_wild_encounter_set, normalize_method_details
from pydantic import TypeAdapter
from utils import (
    SOURCE_KEY_FIELDS,
    RateLimitedClient,
    merge_by_key,
    source_key,
    source_sort_key,
)

from homestretch_data.models import Method, Source

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
CACHE_DIR = REPO_ROOT / ".cache" / "bulbapedia"
POKEAPI_CACHE_DIR = REPO_ROOT / ".cache" / "pokeapi"
SOURCES_PATH = DATA_DIR / "sources.json"
FORMS_PATH = DATA_DIR / "forms.json"
USER_AGENT = (
    "HomeStretch/0.1 (+https://github.com/geg0046/homestretch-data; "
    "contact: homestretchapp@outlook.com)"
)
MIN_REQUEST_INTERVAL = 1.0
BULBAPEDIA_API = "https://bulbapedia.bulbagarden.net/w/api.php"
POKEAPI_BASE = "https://pokeapi.co/api/v2"

# Bulbapedia version display names (as they appear in Availability/Entry*
# `v=`/`v2=` parameters) → HomeStretch game IDs. Pre-Gen-8 entries are
# included so the parser can tell "known-but-out-of-scope" apart from
# "unknown" — sources mode filters emissions to IN_SCOPE_GAME_IDS below.
VERSION_TO_GAME_ID: dict[str, str] = {
    "Sword": "sword",
    "Shield": "shield",
    "Brilliant Diamond": "brilliant-diamond",
    "Shining Pearl": "shining-pearl",
    "Legends: Arceus": "legends-arceus",
    "Scarlet": "scarlet",
    "Violet": "violet",
    "Legends: Z-A": "legends-za",
    "Red": "red",
    "Blue": "blue",
    "Yellow": "yellow",
    "Gold": "gold",
    "Silver": "silver",
    "Crystal": "crystal",
    "Ruby": "ruby",
    "Sapphire": "sapphire",
    "FireRed": "firered",
    "LeafGreen": "leafgreen",
    "Emerald": "emerald",
    "Diamond": "diamond",
    "Pearl": "pearl",
    "Platinum": "platinum",
    "HeartGold": "heartgold",
    "SoulSilver": "soulsilver",
    "Black": "black",
    "White": "white",
    "Black 2": "black-2",
    "White 2": "white-2",
    "X": "x",
    "Y": "y",
    "Omega Ruby": "omega-ruby",
    "Alpha Sapphire": "alpha-sapphire",
    "Sun": "sun",
    "Moon": "moon",
    "Ultra Sun": "ultra-sun",
    "Ultra Moon": "ultra-moon",
    "Let's Go Pikachu": "lets-go-pikachu",
    "Let's Go Eevee": "lets-go-eevee",
}

# DLC / expansion labels that appear as `v=` values. Each maps to the base
# game IDs it applies to plus a DLC slug recorded in `requires_dlc`.
# Bulbapedia is inconsistent about whether DLC labels carry a version
# suffix/prefix: most pages use the generic form (e.g. `v=Expansion Pass`
# applying to both SwSh), but newer Gen 9 additions frequently use the
# per-version form (`v=The Hidden Treasure of Area Zero (Scarlet)`,
# `v=Sword Expansion Pass`). Both shapes must be listed explicitly.
DLC_TO_GAMES: dict[str, tuple[tuple[str, ...], str]] = {
    "Expansion Pass": (("sword", "shield"), "expansion-pass"),
    "Sword Expansion Pass": (("sword",), "expansion-pass"),
    "Shield Expansion Pass": (("shield",), "expansion-pass"),
    "The Isle of Armor": (("sword", "shield"), "isle-of-armor"),
    "The Crown Tundra": (("sword", "shield"), "crown-tundra"),
    "The Teal Mask": (("scarlet", "violet"), "teal-mask"),
    "The Indigo Disk": (("scarlet", "violet"), "indigo-disk"),
    "The Hidden Treasure of Area Zero": (
        ("scarlet", "violet"),
        "hidden-treasure-of-area-zero",
    ),
    "The Hidden Treasure of Area Zero (Scarlet)": (
        ("scarlet",),
        "hidden-treasure-of-area-zero",
    ),
    "The Hidden Treasure of Area Zero (Violet)": (
        ("violet",),
        "hidden-treasure-of-area-zero",
    ),
}

# Sources mode emits rows only for in-scope mainline games; DLC labels and
# out-of-scope versions (RSE, FRLG, DPPt, HGSS, BW, B2W2) are dropped after
# parsing. Mirrors `IN_SCOPE_VERSIONS` in `pokeapi.py`.
IN_SCOPE_GAME_IDS = frozenset(
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


# --- MediaWiki template parsing -------------------------------------------------


def _find_templates(s: str) -> list[tuple[int, int]]:
    """Return (start, end) spans of top-level `{{...}}` templates in s."""
    spans: list[tuple[int, int]] = []
    i = 0
    n = len(s)
    while i < n:
        if s[i : i + 2] == "{{":
            depth = 1
            j = i + 2
            while j < n and depth > 0:
                if s[j : j + 2] == "{{":
                    depth += 1
                    j += 2
                elif s[j : j + 2] == "}}":
                    depth -= 1
                    j += 2
                else:
                    j += 1
            spans.append((i, j))
            i = j
        else:
            i += 1
    return spans


def _split_top_level(s: str, sep: str) -> list[str]:
    """Split s on sep, respecting `{{...}}` and `[[...]]` nesting."""
    depth_b = 0
    depth_l = 0
    parts: list[str] = []
    start = 0
    i = 0
    n = len(s)
    while i < n:
        if s[i : i + 2] == "{{":
            depth_b += 1
            i += 2
            continue
        if s[i : i + 2] == "}}":
            depth_b -= 1
            i += 2
            continue
        if s[i : i + 2] == "[[":
            depth_l += 1
            i += 2
            continue
        if s[i : i + 2] == "]]":
            depth_l -= 1
            i += 2
            continue
        if s[i] == sep and depth_b == 0 and depth_l == 0:
            parts.append(s[start:i])
            start = i + 1
        i += 1
    parts.append(s[start:])
    return parts


def _parse_template(template: str) -> dict[str, str]:
    """Parse `{{Name|a=b|c=d|pos}}` into a dict. `_name` holds template name."""
    assert template.startswith("{{") and template.endswith("}}")
    body = template[2:-2]
    parts = _split_top_level(body, "|")
    params: dict[str, str] = {"_name": parts[0].strip()}
    positional = 0
    for part in parts[1:]:
        eq = _find_top_level_char(part, "=")
        if eq >= 0:
            params[part[:eq].strip()] = part[eq + 1 :].strip()
        else:
            positional += 1
            params[str(positional)] = part.strip()
    return params


def _find_top_level_char(s: str, ch: str) -> int:
    depth_b = 0
    depth_l = 0
    i = 0
    n = len(s)
    while i < n:
        if s[i : i + 2] == "{{":
            depth_b += 1
            i += 2
            continue
        if s[i : i + 2] == "}}":
            depth_b -= 1
            i += 2
            continue
        if s[i : i + 2] == "[[":
            depth_l += 1
            i += 2
            continue
        if s[i : i + 2] == "]]":
            depth_l -= 1
            i += 2
            continue
        if s[i] == ch and depth_b == 0 and depth_l == 0:
            return i
        i += 1
    return -1


_WIKILINK_RE = re.compile(r"\[\[(?:[^\]|]*\|)?([^\]]+)\]\]")
_DL_TEMPLATE_RE = re.compile(r"\{\{DL\|[^|}]*\|[^|}]*\|([^}]+)\}\}")
# Catches one-arg shorthand templates like {{p|Sprigatito}}, {{pkmn|Sun}},
# {{a|Ability}} — the display text is the single pipe-arg. Runs after DL so
# the 3-arg DL form is already handled.
_SHORTHAND_TEMPLATE_RE = re.compile(r"\{\{[a-zA-Z]+\|([^|{}]+)\}\}")


def _flatten_wikitext(s: str) -> str:
    """Collapse wikilinks and simple templates down to their display text."""
    s = _DL_TEMPLATE_RE.sub(r"\1", s)
    s = _SHORTHAND_TEMPLATE_RE.sub(r"\1", s)
    s = _WIKILINK_RE.sub(r"\1", s)
    return s.strip()


# --- Page fetching --------------------------------------------------------------


def _species_page_title(display_name: str) -> str:
    """Convert a PokéAPI English display name into a Bulbapedia page title."""
    return f"{display_name.replace(' ', '_')}_(Pokémon)"


_REDIRECT_RE = re.compile(r"^\s*#redirect\s*\[\[([^\]]+)\]\]", re.I)


def fetch_wikitext(
    bulba: RateLimitedClient,
    page_title: str,
) -> str | None:
    """Return the raw wikitext of a Bulbapedia page, or None if missing.

    Follows `#redirect` pages once — Bulbapedia frequently uses them to
    canonicalise apostrophe variants (curly vs straight, e.g. Sirfetch'd).
    The redirect hop re-uses the per-URL cache, so both titles stay cheap.
    """
    url = (
        f"{BULBAPEDIA_API}?action=parse"
        f"&page={quote(page_title, safe='')}"
        f"&prop=wikitext&format=json&formatversion=2"
    )
    data = bulba.get_json(url)
    if "error" in data:
        return None
    wikitext = data.get("parse", {}).get("wikitext")
    if not wikitext:
        return wikitext
    m = _REDIRECT_RE.match(wikitext)
    if m:
        target = m.group(1).split("|", 1)[0].strip().replace(" ", "_")
        redirect_url = (
            f"{BULBAPEDIA_API}?action=parse"
            f"&page={quote(target, safe='')}"
            f"&prop=wikitext&format=json&formatversion=2"
        )
        data2 = bulba.get_json(redirect_url)
        if "error" in data2:
            return None
        return data2.get("parse", {}).get("wikitext")
    return wikitext


def _english_name(species: dict[str, Any]) -> str | None:
    for entry in species.get("names", []):
        if entry.get("language", {}).get("name") == "en":
            return entry["name"]
    return None


# --- Game locations parsing -----------------------------------------------------


def _extract_main_games_section(wikitext: str) -> str:
    """Return the main-games portion of the `==Game locations==` section.

    Stops at the first `====` subsection (In side games / In events / etc.)
    so only main-series entries are parsed.

    The "Game" word in the heading is sometimes wrapped in a template —
    Rattata and Raticate use `==={{pkmn|games|Game}} locations===`. The
    regex accepts either the bare word or the templated link as the lead-in.
    """
    m = re.search(
        r"==\s*(?:\{\{pkmn\|games\|Game\}\}|Game)\s+locations\s*==+",
        wikitext,
    )
    if not m:
        return ""
    tail = wikitext[m.end() :]
    # Stop at any 4-equals subheading or the next 2- or 3-equals heading
    # (which marks the next top-level section).
    end_match = re.search(r"\n={2,4}[^=\n].*?={2,4}", tail)
    if end_match:
        tail = tail[: end_match.start()]
    return tail


def _iter_availability_templates(section: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for start, end in _find_templates(section):
        raw = section[start:end]
        name_end = raw.find("|")
        if name_end < 0:
            name_end = len(raw) - 2
        name = raw[2:name_end].strip()
        if not name.startswith("Availability/"):
            continue
        params = _parse_template(raw)
        params["_name"] = name
        out.append(params)
    return out


def _resolve_version(v: str) -> tuple[tuple[str, ...], str | None]:
    """Return (game_ids, requires_dlc) for a Bulbapedia version label, or
    ((), None) if the label is unknown or out of scope.
    """
    if v in DLC_TO_GAMES:
        games, dlc_slug = DLC_TO_GAMES[v]
        return (games, dlc_slug)
    if v in VERSION_TO_GAME_ID:
        return ((VERSION_TO_GAME_ID[v],), None)
    return ((), None)


# Area text is classified by these rules in order. Areas that match NO
# pattern fall through to WILD_ENCOUNTER by default. The preceding
# `_is_skippable_area` guard filters purely-unobtainable entries upstream;
# patterns here must only fire on actually-obtainable content.
_METHOD_PATTERNS: tuple[tuple[re.Pattern[str], Method], ...] = (
    (re.compile(r"\bfossil\b", re.I), Method.FOSSIL_REVIVE),
    (re.compile(r"\btera\s+raid\b|\bmax\s+raid\b|\braid\s+battle\b", re.I), Method.RAID),
    (
        re.compile(r"\bevent\b|\bdistribution\b|\bmystery\s+gift\b", re.I),
        Method.EVENT,
    ),
    (
        re.compile(
            r"\bfirst\s+partner\b|\bstarter\b|\bgift\b"
            r"|\breceive[ds]?\b|\bgiven\b|\bawarded\b",
            re.I,
        ),
        Method.GIFT,
    ),
    (re.compile(r"\bfishing\b|\bold\s+rod\b|\bgood\s+rod\b|\bsuper\s+rod\b", re.I), Method.FISHING),
    # Singletons / legendary placements. "Only one" is Bulbapedia's canonical
    # marker. "Trade" is handled last because it's a common substring in
    # phrases like "Trade evolution" / "Trade from X" that describe
    # availability restrictions rather than the actual method.
    (re.compile(r"\bonly\s+one\b|\bone-time\b", re.I), Method.STATIC_ENCOUNTER),
    (re.compile(r"\btrade\b", re.I), Method.TRADE),
)


def _infer_method(area: str) -> tuple[Method | None, str]:
    """Pick a Method for an Availability/Entry area.

    Returns `(None, flat)` when no pattern matches; the caller decides
    whether to fall back to wild-encounter (true for natively-available
    entries) or skip (true for `/None`-marked entries, which are not
    native wild encounters by semantic convention).
    """
    flat = _flatten_wikitext(area)
    for pat, method in _METHOD_PATTERNS:
        if pat.search(flat):
            return (method, flat)
    return (None, flat)


# The "only one" / "one-time" condition on Bulbapedia is a wikilink to a
# list page, not an italic parenthetical — e.g.
#   [[List of in-game event Pokémon in Generation I#Mewtwo|Only one]]
# Any wikilink whose target begins with this phrase is the method marker,
# not a location, and must be scrubbed before the first-wikilink extraction.
_EVENT_LIST_WIKILINK_RE = re.compile(r"\[\[\s*List of in-game event Pok[^\]|]*(?:\|[^\]]*)?\]\]")
# Parse a wikilink: group(1) = target, group(2) = optional display text.
_WIKILINK_PARTS_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
# <small>/<sup>/HTML comments carry requires-this-item footnotes; not location.
_INLINE_METADATA_RE = re.compile(
    r"<small>.*?</small>|<sup>.*?</sup>|<!--.*?-->",
    re.DOTALL | re.IGNORECASE,
)
# Two specific MediaWiki shortcuts Bulbapedia uses a lot that the generic
# `_flatten_wikitext` shorthand regex doesn't reach (they're 2-arg):
#   {{rt|N|Region}}  -> "Region Route N"   (route-number shortcut)
#   {{rtn|N|Region}} -> "Region Route N"   (same, for sortable tables)
#   {{FB|Region|Place}} -> "Region Place"  (flag-button inline link, Gen 1)
_ROUTE_TEMPLATE_RE = re.compile(r"\{\{rtn?\|(\d+)\|([A-Za-z]+)\}\}", re.IGNORECASE)
_FB_TEMPLATE_RE = re.compile(r"\{\{FB\|([^|}]+)\|([^|}]+)\}\}", re.IGNORECASE)
# `{{ka|<location>}}` — Kanto location link template. Equivalent to
# `[[<location>]]` for slug-extraction purposes; used heavily in LGPE
# Availability segments (`{{ka|Pokémon Mansion}}`, `{{ka|Victory Road}}`)
# and on Gen 1/2 species pages.
_KA_TEMPLATE_RE = re.compile(r"\{\{ka\|([^|}]+)\}\}", re.IGNORECASE)
# `{{tt|visible|tooltip}}` footnote template; visible is the display text.
_TT_TEMPLATE_RE = re.compile(r"\{\{tt\|[^|{}]*\|[^{}]*\}\}", re.IGNORECASE)
# Catches any remaining `{{...}}` template that survives the more specific
# strippers above. Used to scrub template syntax embedded in wikilink display
# text (`[[Alpha Pokémon|{{Link|Alpha Pokémon|14px}}]]`) before slug
# generation, so the `14px` image-sizing arg doesn't leak into a location slug.
_TEMPLATE_STRIP_RE = re.compile(r"\{\{[^{}]+\}\}")
# `[[File:...]]` and `[[Image:...]]` image embeds — Bulbapedia uses them
# inline as decoration (alpha-Pokémon icons, type icons). They're not
# location wikilinks; the display text after the first `|` carries image
# attributes (`link=X|14px`) that slug to garbage if let through.
_FILE_WIKILINK_RE = re.compile(r"\[\[\s*(?:File|Image)\s*:[^\]]+\]\]", re.IGNORECASE)
# Preposition-led location phrases in gift prose: "in [[X]]", "at [[Y]]",
# "on {{rt|N|Region}}". Captures the link or template so the match's last
# occurrence is almost always the actual place (NPC names come before the
# "in" / "at" preposition, not after).
_LOCATION_PREPOSITION_RE = re.compile(
    r"\b(?:in|at|on)\s+(?:the\s+)?(?P<link>"
    r"\[\[[^\]]+\]\]"
    r"|\{\{rtn?\|\d+\|[A-Za-z]+\}\}"
    r"|\{\{FB\|[^|}]+\|[^|}]+\}\}"
    r")",
    re.IGNORECASE,
)
# "after X" / "during Y" / "if Z" / "when W" start a condition clause that
# trails the location; trim everything from there on before searching.
_TRAILING_CONDITION_RE = re.compile(r"\s+(?:after|during|if|when)\s+.+$", re.IGNORECASE)
_SLUG_ALLOWED_RE = re.compile(r"[^a-z0-9]+")
_SLUG_MAX_LEN = 40  # clip garbage; named landmarks fit well under this.


def _resolve_link_display(link: str) -> str | None:
    """Return the display text of a `[[wikilink]]` or `{{rt/rtn/FB|...}}` snippet."""
    m = _WIKILINK_PARTS_RE.match(link)
    if m:
        return (m.group(2) or m.group(1)).strip()
    rt = _ROUTE_TEMPLATE_RE.match(link)
    if rt:
        return f"{rt.group(2)} route {rt.group(1)}"
    fb = _FB_TEMPLATE_RE.match(link)
    if fb:
        return f"{fb.group(1)} {fb.group(2)}"
    return None


def extract_area_location(segment: str, *, prefer_preposition: bool) -> str | None:
    """Derive a location slug from a Bulbapedia availability segment.

    - **Static-encounter** (``prefer_preposition=False``): first wikilink
      wins. The segment typically opens with the place
      (``[[Cerulean Cave]] ([[...|Only one]])``) and any trailing links
      are method markers or condition prose.
    - **Gift** (``prefer_preposition=True``): the place follows an ``in``
      / ``at`` / ``on`` preposition (``Received from [[Bill]] in
      [[Goldenrod City]]``). Fall back to first-wikilink if no such
      match — covers gifts written without a preposition.

    Always scrubs the "Only one" event-list wikilink, `<small>`/`<sup>`
    footnote metadata, `{{tt|...}}` tooltips, and any trailing
    ``after X`` / ``during Y`` condition clauses. Returns None when the
    slug is empty, shorter than 2 characters, or longer than
    `_SLUG_MAX_LEN` — the row then keeps `location=None` instead of
    receiving a garbage slug.
    """
    cleaned = _EVENT_LIST_WIKILINK_RE.sub("", segment)
    cleaned = _FILE_WIKILINK_RE.sub("", cleaned)
    cleaned = _INLINE_METADATA_RE.sub("", cleaned)
    cleaned = _TT_TEMPLATE_RE.sub("", cleaned)
    cleaned = _TRAILING_CONDITION_RE.sub("", cleaned)

    display: str | None = None
    if prefer_preposition:
        prep_matches = list(_LOCATION_PREPOSITION_RE.finditer(cleaned))
        if prep_matches:
            display = _resolve_link_display(prep_matches[-1].group("link"))
    if display is None:
        m = _WIKILINK_PARTS_RE.search(cleaned)
        if m:
            target = m.group(1).strip()
            display = (m.group(2) or m.group(1)).strip()
            if "{{" in display:
                display = _TEMPLATE_STRIP_RE.sub("", display).strip()
                if not display:
                    display = target
        else:
            rt = _ROUTE_TEMPLATE_RE.search(cleaned)
            if rt:
                display = f"{rt.group(2)} route {rt.group(1)}"
            else:
                fb = _FB_TEMPLATE_RE.search(cleaned)
                if fb:
                    display = f"{fb.group(1)} {fb.group(2)}"
                else:
                    ka = _KA_TEMPLATE_RE.search(cleaned)
                    if ka:
                        display = ka.group(1)
                    else:
                        flat_head = _flatten_wikitext(cleaned).split("(")[0].strip(" ,.:;")
                        if flat_head:
                            display = flat_head
    if not display:
        return None
    return _slug_from_text(display)


# Backwards-compat alias for existing callers; static never wants preposition
# preference.
def extract_static_location(segment: str) -> str | None:
    return extract_area_location(segment, prefer_preposition=False)


# Generic common-noun and mechanic-name slugs that show up as bare wikilinks
# in availability prose ("[[Route]]s {{rtn|201|Sinnoh}}, ..." prefixes a list
# with a generic-noun link to the [[Route]] page). They name a category, not
# a specific place, so wild-encounter extraction skips them and continues
# scanning the segment for the actual locations.
_GENERIC_LOCATION_SLUGS: frozenset[str] = frozenset(
    {
        # Common-noun place categories
        "route",
        "routes",
        "cave",
        "forest",
        "city",
        "town",
        "lake",
        "sea",
        "mountain",
        "depths",
        "type",
        # Mechanic / non-place wikilinks
        "trade",
        "breed",
        "breeding",
        "pokemon-breeding",
        "swarm",
        "evolution",
        "evolve",
        "wanderer",
        "mass-outbreak",
        "national-pokedex",
        "sos-battle",
        "horde-encounter",
        "ambush-encounter",
        "poke-radar",
        "headbutt-tree",
        "only-high-encounter-trees",
        # PLA / LZA "Alpha Pokémon" annotation — a difficulty-tier label,
        # not a place. Bulbapedia wraps it in `{{Link|Alpha Pokémon|14px}}`
        # template inside wikilinks; after the template strip the link
        # target slugs to this.
        "alpha-pokemon",
        # `[[Coin (Game Corner)|C]]` — Game Corner currency (the item),
        # not the venue. Display "C" is single-char so falls to target,
        # which slugs to this. The actual venue link `[[Celadon Game
        # Corner]]` shows up elsewhere in the same segment.
        "coin-game-corner",
        # Cross-game services that are never an in-game location
        "home",
        "pokemon-home",
        "pokemon-bank",
        # Region names — used as wikilink targets on multi-region tables
        "hoenn",
        "hisui",
        "kanto",
        "johto",
        "sinnoh",
        "unova",
        "kalos",
        "alola",
        "galar",
        "paldea",
        # Pokémon types — appear as `[[Fire (type)|Fire]]`-style links in
        # Friend Safari and elemental-encounter prose; the display text
        # collapses to the bare type name.
        "normal",
        "fire",
        "water",
        "electric",
        "grass",
        "ice",
        "fighting",
        "poison",
        "ground",
        "flying",
        "psychic",
        "bug",
        "rock",
        "ghost",
        "dragon",
        "dark",
        "steel",
        "fairy",
        # Fishing rods and `[[fishing]]` — Bulbapedia annotates fishing
        # segments with parenthesized rod links (`[[Cerulean City]]
        # ([[Old Rod]])`); the rod is the *method*, not the location.
        "fishing",
        "old-rod",
        "good-rod",
        "super-rod",
        # Raid mechanic names — `[[Max Raid Battle]]` / `[[Tera Raid
        # Battle]]` etc. annotate the raid type, not the location.
        # `max-lair` is intentionally NOT in this set: it IS the
        # canonical location for `dynamax-adventure` rows.
        "max-raid",
        "max-raid-battle",
        "tera-raid",
        "tera-raid-battle",
        "dynamax-adventure",
        "pokemon-den",
    }
)


# Bulbapedia uses interchangeable display text for some places
# (`[[Mt. Coronet]]` vs `[[Mount Coronet]]`). Keys are the variant slug
# the slugifier would emit; values are the canonical slug we want in
# data/sources.json. Add entries narrowly — only when a real
# (form_id, game_id, method, method_details, location) collision
# surfaces — to avoid hiding legitimate distinct sub-areas.
_SLUG_ALIASES: dict[str, str] = {
    "mount-coronet": "mt-coronet",
}


def _slug_from_text(text: str) -> str | None:
    """Slugify free-form display text to a slug, or None if it'd be junk."""
    import unicodedata

    if not text:
        return None
    ascii_form = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_ALLOWED_RE.sub("-", ascii_form.lower()).strip("-")
    if not slug or len(slug) < 2 or len(slug) > _SLUG_MAX_LEN:
        return None
    return _SLUG_ALIASES.get(slug, slug)


def extract_area_locations(segment: str) -> list[str]:
    """Extract every non-generic location slug from a wild-encounter segment.

    Wild-encounter Availability segments commonly enumerate many places in
    one ``area=...`` field (``[[Route]]s {{rtn|201|Sinnoh}}, {{rtn|202|
    Sinnoh}}, [[Lake Verity]]``). This walks all `[[wikilinks]]`, `{{rt|}}`
    /`{{rtn|}}` route templates, and `{{FB|}}` flag-button templates,
    yielding one slug per distinct named place.

    Skips:
      - generic common-noun wikilinks (`_GENERIC_LOCATION_SLUGS`) that
        prefix a list (`[[Route]]s ...`) but don't name a specific place;
      - segments where the wikilink display text slugs to a generic but
        the underlying target is specific — those use the target instead
        (`[[Sinnoh Route 201|Route]]` → ``sinnoh-route-201``).

    Same scrubbing pre-pass as ``extract_area_location``: event-list
    wikilinks, ``<small>``/``<sup>`` metadata, ``{{tt|}}`` tooltips,
    and trailing condition clauses.
    """
    cleaned = _EVENT_LIST_WIKILINK_RE.sub("", segment)
    cleaned = _FILE_WIKILINK_RE.sub("", cleaned)
    cleaned = _INLINE_METADATA_RE.sub("", cleaned)
    cleaned = _TT_TEMPLATE_RE.sub("", cleaned)
    cleaned = _TRAILING_CONDITION_RE.sub("", cleaned)

    slugs: list[str] = []
    seen: set[str] = set()
    # `[[Friend Safari]] ([[Friend Safari#Grass-type Safari|Grass]])` emits
    # both `friend-safari` (parent) and `friend-safari-grass-type-safari`
    # (sub-area). The parent is redundant; record the pre-fragment base of
    # any `#`-anchor wikilink so we can drop sibling parent slugs at the end.
    superseded: set[str] = set()

    def push(slug: str | None) -> None:
        if slug and slug not in _GENERIC_LOCATION_SLUGS and slug not in seen:
            slugs.append(slug)
            seen.add(slug)

    for m in _WIKILINK_PARTS_RE.finditer(cleaned):
        target = m.group(1).strip()
        display = (m.group(2) or m.group(1)).strip()
        # `[[File:...|link=X|14px]]` image embeds — display ends up as
        # "link=Alpha Pokémon|14px" and slugs to garbage like
        # `link-alpha-pokemon-14px`. Skip these whole — they're decoration,
        # not location.
        if target.lower().startswith(("file:", "image:")):
            continue
        # `[[Fire (type)|Fire]]`-style links resolve display "Fire" → generic,
        # then fall through to target "Fire (type)" → "fire-type" — still not
        # a location. Drop the whole link when the target carries a
        # `(type)` / `(move)` / `(ability)` disambiguator.
        if target.lower().endswith(("(type)", "(move)", "(ability)", "(species)")):
            continue
        if "#" in target:
            base_slug = _slug_from_text(target.split("#", 1)[0])
            if base_slug:
                superseded.add(base_slug)
        # Display can carry an embedded template
        # (`[[Alpha Pokémon|{{Link|Alpha Pokémon|14px}}]]`); strip it so the
        # `14px` sizing arg doesn't leak into the slug.
        if "{{" in display:
            display = _TEMPLATE_STRIP_RE.sub("", display).strip()
        slug = _slug_from_text(display) if display else None
        if (slug is None or slug in _GENERIC_LOCATION_SLUGS) and target != display:
            slug = _slug_from_text(target)
        push(slug)
    for m in _ROUTE_TEMPLATE_RE.finditer(cleaned):
        push(_slug_from_text(f"{m.group(2)} route {m.group(1)}"))
    for m in _FB_TEMPLATE_RE.finditer(cleaned):
        push(_slug_from_text(f"{m.group(1)} {m.group(2)}"))
    for m in _KA_TEMPLATE_RE.finditer(cleaned):
        push(_slug_from_text(m.group(1)))

    return [s for s in slugs if s not in superseded]


def _is_skippable_area(area: str) -> bool:
    flat = _flatten_wikitext(area).strip().lower()
    if flat in {"", "unobtainable", "none"}:
        return True
    if flat.startswith("unobtainable"):
        return True
    # Evolve-only rows are already covered by --mode evolutions (both the
    # PokéAPI and Bulbapedia variants); don't emit a parallel wild/static
    # row that would just duplicate an evolution source.
    return flat.startswith("evolve")


# Regional-form annotation vocabulary. Bulbapedia species pages mix
# availability for all regional variants in one ===Game locations===
# section and disambiguate via inline annotations like
# "<small>('''Galarian Form''')</small>" / "'''Alolan/Galarian Forms'''" /
# "'''Paldean Form (Combat Breed)'''". These map the annotation phrase to a
# form_id suffix (None = default species_id).
_ANNOTATION_TO_SUFFIX: dict[str, str | None] = {
    "Alolan": "alola",
    "Galarian": "galar",
    "Hisuian": "hisui",
    "Paldean": "paldea",
    # Base-region phrases all resolve to the species default form_id.
    "Kantonian": None,
    "Johtonian": None,
    "Hoennian": None,
    "Sinnohian": None,
    "Unovan": None,
    "Kalosian": None,
}

# Inside `'''...'''` bold markers in an annotation. Captures region phrases
# (possibly slash-combined, e.g. "Alolan/Galarian") and "All Forms".
_ANNOTATION_PHRASE_RE = re.compile(r"'''([^']+?)'''")
_REGION_WORD_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _ANNOTATION_TO_SUFFIX) + r")\b"
)
# Paldean tauros breeds: "(Combat Breed)", "(Aqua Breed)",
# "(Combat and Blaze Breeds)", "(Combat, Blaze Breeds)", etc.
_BREED_RE = re.compile(r"\(([A-Za-z ,]+?)\s+Breeds?\)", re.IGNORECASE)
_ALL_FORMS_RE = re.compile(r"\bAll\s+Forms\b", re.IGNORECASE)


def resolve_form_ids_from_segment(
    segment: str,
    species_id: str,
    species_form_ids: set[str],
) -> list[str]:
    """Resolve the form_id(s) a Bulbapedia area-segment applies to.

    `segment` is one `<br>`-separated piece of an availability entry's area
    text. If bold form annotations are present, they pick the form(s); if
    absent, we fall back to the species default form_id.

    Returns an empty list when no form_id resolves to a form we actually
    carry in forms.json (e.g. annotation names a regional variant we don't
    track, or species has no default form row).
    """
    annotations = _ANNOTATION_PHRASE_RE.findall(segment)

    # "All Forms" / "Both Forms" → every form this species has, but only
    # those currently in forms.json.
    for ann in annotations:
        if _ALL_FORMS_RE.search(ann) or "Both Forms" in ann:
            return sorted(species_form_ids)

    resolved: list[str] = []
    for ann in annotations:
        regions = _REGION_WORD_RE.findall(ann)
        if not regions:
            continue
        breed_match = _BREED_RE.search(ann)
        breed_tokens: list[str] = []
        if breed_match:
            # Split "Combat and Blaze" / "Combat, Blaze" / "Combat" into tokens.
            breed_tokens = [
                t.strip().lower()
                for t in re.split(r"\s+and\s+|,\s*", breed_match.group(1))
                if t.strip()
            ]
        for region in regions:
            suffix = _ANNOTATION_TO_SUFFIX[region]
            if breed_tokens:
                for breed in breed_tokens:
                    form_id = f"{species_id}-{suffix}-{breed}-breed" if suffix else species_id
                    if form_id in species_form_ids:
                        resolved.append(form_id)
                continue
            form_id = f"{species_id}-{suffix}" if suffix else species_id
            if form_id in species_form_ids:
                resolved.append(form_id)
                continue
            # Fallback for species where the regional form_id carries an
            # extra mode suffix (e.g. darmanitan-galar-standard). If there's
            # exactly one form_id in the species set that extends the
            # constructed prefix, use it.
            prefix = f"{form_id}-"
            candidates = [fid for fid in species_form_ids if fid.startswith(prefix)]
            if len(candidates) == 1:
                resolved.append(candidates[0])

    if resolved:
        # Preserve order but dedupe.
        seen: set[str] = set()
        unique: list[str] = []
        for fid in resolved:
            if fid not in seen:
                seen.add(fid)
                unique.append(fid)
        return unique

    # No annotation matched a known form; fall back to species default.
    return [species_id] if species_id in species_form_ids else []


def split_area_segments(area: str) -> list[str]:
    """Split an availability entry's area on `<br>` boundaries.

    Each segment is one acquisition path and carries its own method /
    annotations. Trailing whitespace is trimmed; empty segments dropped.
    """
    parts = re.split(r"<br\s*/?>", area, flags=re.IGNORECASE)
    return [p.strip() for p in parts if p.strip()]


def parse_sources_from_wikitext(
    wikitext: str,
    species_id: str,
    species_form_ids: set[str],
) -> list[dict[str, Any]]:
    """Emit Source dicts for each base-game location entry on a species page.

    Only rows whose resolved game_id lies in IN_SCOPE_GAME_IDS are returned;
    pre-Gen-8 games are already covered by PokéAPI.

    Availability entries are split on `<br>` so each acquisition path is
    processed independently — Bulbapedia packs paths for multiple regional
    forms (e.g. Kantonian + Galarian Meowth) into one entry, each tagged
    with `'''(Region) Form'''` annotations. `resolve_form_ids_from_segment`
    picks the right form_id per segment; unannotated segments fall back to
    the species default.
    """
    section = _extract_main_games_section(wikitext)
    if not section:
        return []

    rows: list[dict[str, Any]] = []
    for tmpl in _iter_availability_templates(section):
        name = tmpl["_name"]
        if "/NA" in name or "/Header" in name or "/Footer" in name:
            continue
        if "Entry" not in name:
            continue
        # `Entry*/None` templates mark a game where the species has no native
        # wild encounter, but their `area` field still frequently carries
        # non-wild acquisition paths — trade (version-exclusives or
        # post-patch additions like "[[Trade]]<sup>Version 2.0.1+</sup>"),
        # raid search, etc. Parse the area and only emit rows for segments
        # whose method is explicitly recognised (not the wild-encounter
        # default), so `/None` never produces a spurious wild-encounter row.
        is_none = "/None" in name
        area = tmpl.get("area", "")
        if _is_skippable_area(area):
            continue

        vs: list[str] = []
        if "Entry1" in name:
            v = tmpl.get("v")
            if v:
                vs.append(v)
        elif "Entry2" in name:
            for key in ("v", "v2"):
                v = tmpl.get(key)
                if v:
                    vs.append(v)

        # Per-segment: infer method + resolve form_id(s) from inline region
        # annotations. A multi-path area with form annotations emits one row
        # per segment/form/version/game combination.
        segments = split_area_segments(area) or [area]
        for segment in segments:
            if _is_skippable_area(segment):
                continue
            inferred, details_text = _infer_method(segment)
            if inferred is None:
                if is_none:
                    continue
                method = Method.WILD_ENCOUNTER
            else:
                method = inferred
            resolved_forms = resolve_form_ids_from_segment(segment, species_id, species_form_ids)
            if not resolved_forms:
                continue
            for v in vs:
                games, dlc = _resolve_version(v)
                for gid in games:
                    if gid not in IN_SCOPE_GAME_IDS:
                        continue
                    for form_id in resolved_forms:
                        entry: dict[str, Any] = {
                            "form_id": form_id,
                            "game_id": gid,
                            "method": method.value,
                        }
                        normalized = normalize_method_details(method, details_text)
                        if normalized is not None:
                            entry["method_details"] = normalized
                        if dlc:
                            entry["requires_dlc"] = dlc
                        rows.append(entry)
    return rows


# --- Disk state -----------------------------------------------------------------


_source_key = source_key


def load_existing_sources() -> list[dict[str, Any]]:
    if not SOURCES_PATH.exists():
        return []
    raw = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw, list)
    return raw


def load_species_id_to_forms() -> dict[str, list[str]]:
    """Return {species_id: [form_id, ...]} from data/forms.json."""
    raw = json.loads(FORMS_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw, list)
    out: dict[str, list[str]] = {}
    for form in raw:
        out.setdefault(form["species_id"], []).append(form["id"])
    return out


def load_dex_form_index() -> dict[tuple[int, str | None], str]:
    """Return {(national_dex, form_name): form_id}. form_name is None for defaults."""
    raw = json.loads(FORMS_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw, list)
    out: dict[tuple[int, str | None], str] = {}
    for form in raw:
        out[(form["national_dex"], form.get("form_name"))] = form["id"]
    return out


# --- Evolution section parsing --------------------------------------------------


# Bulbapedia regional suffix (as it appears in `noN=0000Species-Suffix`) →
# forms.json form_name field.
_REGIONAL_SUFFIX_MAP: dict[str, str] = {
    "Galar": "galar",
    "Alola": "alola",
    "Hisui": "hisui",
    "Paldea": "paldea",
}

# Bulbapedia `formN=` adjective phrases (e.g. "Galarian Form") mapping to
# the same form_name slugs. Used as a fallback when `noN` has no suffix.
_REGIONAL_FORM_TEXT_MAP: dict[str, str] = {
    "Galarian Form": "galar",
    "Alolan Form": "alola",
    "Hisuian Form": "hisui",
    "Paldean Form": "paldea",
}

# Hardcoded fallback game scope for regional variants when sources.json has
# no existing rows to borrow from. Used only when emitting fresh evolution
# rows for regional forms (e.g. raichu-alola) that PokéAPI mode skipped.
_REGIONAL_GAMES: dict[str, tuple[str, ...]] = {
    "alola": ("sun", "moon", "ultra-sun", "ultra-moon"),
    "galar": ("sword", "shield"),
    "hisui": ("legends-arceus",),
    "paldea": ("scarlet", "violet"),
}

# Game-link text → in-scope game IDs. Only used for "cannot evolve in [[X]]"
# exclusion phrases. Kept narrow: only games we might emit evolution rows for.
_GAME_LINK_TO_GAMES: dict[str, tuple[str, ...]] = {
    "Pokémon Scarlet and Violet": ("scarlet", "violet"),
    "Pokémon Sword and Shield": ("sword", "shield"),
    "Pokémon Brilliant Diamond and Shining Pearl": ("brilliant-diamond", "shining-pearl"),
    "Pokémon Legends: Arceus": ("legends-arceus",),
    "Pokémon Legends: Z-A": ("legends-za",),
}

# Item names (as they appear flattened from Bulbapedia wikitext) → slug.
# Two populations:
#   (a) Items that PokéAPI collapses into a generic `use-item` without
#       naming the specific item (Gen 8/9 additions) — needed for Pass 1
#       refinement of existing PokéAPI rows.
#   (b) Common evolution stones — needed for Pass 2 synthesis of fresh
#       rows for regional-variant forms PokéAPI skipped, where there's
#       no PokéAPI-provided `item` to inherit.
# Regional-variant evolution trigger overrides. Bulbapedia's per-species
# evobox templates leave `evoN` empty for most regional-form stages —
# the trigger info lives in prose our scraper doesn't parse. For these
# forms, hardcode `method_details` (and `item` when applicable) so
# Pass-2 emits a complete row and Pass-1 can fill gaps on existing
# PokéAPI rows. Each entry verified against Bulbapedia + Serebii.
# Overrides only fill gaps; they never clobber a Bulbapedia-detected
# value.
# Override values can be either strings (`method_details`, `item`) or
# any structured-condition value the Source schema accepts on those
# fields (currently `time_of_day` strings and integer min_* fields).
_REGIONAL_TRIGGER_OVERRIDES: dict[str, dict[str, Any]] = {
    # Alolan forms. raticate-alola / marowak-alola need `time_of_day=night`
    # and persian-alola needs `min_happiness=160`; PokéAPI conflates these
    # paths into the default chain (filtered by REGIONAL_VARIANT_DETAILS in
    # evolution_details.py), so the regional row has to fill them in here.
    "raticate-alola": {"method_details": "level-up", "time_of_day": "night"},
    "dugtrio-alola": {"method_details": "level-up"},
    "persian-alola": {"method_details": "level-up", "min_happiness": 160},
    "muk-alola": {"method_details": "level-up"},
    "graveler-alola": {"method_details": "level-up"},
    "golem-alola": {"method_details": "trade"},
    "ninetales-alola": {"method_details": "use-item", "item": "ice-stone"},
    "sandslash-alola": {"method_details": "use-item", "item": "ice-stone"},
    "marowak-alola": {"method_details": "level-up", "time_of_day": "night"},
    # Galarian forms.
    "rapidash-galar": {"method_details": "level-up"},
    "slowbro-galar": {"method_details": "use-item", "item": "galarica-cuff"},
    "slowking-galar": {"method_details": "use-item", "item": "galarica-wreath"},
    # Hisuian forms.
    "arcanine-hisui": {"method_details": "use-item", "item": "fire-stone"},
    "electrode-hisui": {"method_details": "use-item", "item": "leaf-stone"},
    "zoroark-hisui": {"method_details": "level-up"},
}


# Condition fields the override can carry beyond method_details / item /
# pre_evo. Listed here so the application loop is data-driven and adding
# a new condition (e.g. `min_affection`) only requires a one-line edit.
_REFINEMENT_CONDITION_FIELDS: tuple[str, ...] = (
    "time_of_day",
    "min_happiness",
    "min_affection",
    "min_beauty",
)


def _new_refinement_entry() -> dict[str, Any]:
    """Default-shaped refinement entry; keeps all call sites in sync."""
    return {
        "pre_evo": None,
        "item": None,
        "method_details": None,
        "conditions": {},
        "excluded": set(),
    }


_ITEM_NAME_TO_SLUG: dict[str, str] = {
    # (a) Gen 8/9 items PokéAPI emits as generic use-item.
    "Black Augurite": "black-augurite",
    "Peat Block": "peat-block",
    "Linking Cord": "linking-cord",
    "Auspicious Armor": "auspicious-armor",
    "Malicious Armor": "malicious-armor",
    "Galarica Cuff": "galarica-cuff",
    "Galarica Wreath": "galarica-wreath",
    # (b) Evolution stones used by regional-variant rows synthesized in
    # Pass 2 (e.g. raichu-alola via Thunder Stone, exeggutor-alola via
    # Leaf Stone, arcanine-hisui via Fire Stone).
    "Thunder Stone": "thunder-stone",
    "Fire Stone": "fire-stone",
    "Water Stone": "water-stone",
    "Leaf Stone": "leaf-stone",
    "Ice Stone": "ice-stone",
    "Sun Stone": "sun-stone",
    "Moon Stone": "moon-stone",
    "Dusk Stone": "dusk-stone",
    "Dawn Stone": "dawn-stone",
    "Shiny Stone": "shiny-stone",
}


def _extract_evolution_section(wikitext: str) -> str:
    """Return the concatenated Evolution wikitext for a species page.

    Captures both the top-level `===Evolution===` (under `==Biology==`) and
    the deeper `===Evolution data===` (under `==Game data==`); Bulbapedia
    frequently puts the per-game gating prose in the latter while the
    evoboxes live in the former.
    """
    chunks: list[str] = []
    for m in re.finditer(r"={2,4}\s*Evolution(?:\s+data)?\s*={2,4}", wikitext):
        tail = wikitext[m.end() :]
        # Stop at the next heading at the same or shallower depth.
        depth = m.group(0).count("=") // 2
        stop_re = re.compile(
            r"\n=" + ("{" + str(depth) + ",4}") + r"[^=\n].*?=" + ("{" + str(depth) + ",4}")
        )
        end_match = stop_re.search(tail)
        if end_match:
            tail = tail[: end_match.start()]
        chunks.append(tail)
    return "\n".join(chunks)


def _resolve_evobox_stage(
    no_raw: str,
    form_text: str,
    dex_index: dict[tuple[int, str | None], str],
) -> str | None:
    """Map an evobox stage's `noN`/`formN` values to a form_id.

    `no_raw` looks like "0562", "0562Yamask-Galar", or "0122Mr. Mime-Galar".
    The 4-digit prefix is the national dex; a trailing `-Region` suffix
    (or a `formN` value like "Galarian Form") pins the regional variant.
    """
    if not no_raw:
        return None
    m = re.match(r"0*(\d+)(.*)", no_raw.strip())
    if not m:
        return None
    dex = int(m.group(1))
    tail = m.group(2).strip()
    region: str | None = None
    for suffix, form_name in _REGIONAL_SUFFIX_MAP.items():
        if tail.endswith(f"-{suffix}"):
            region = form_name
            break
    if region is None and form_text:
        for phrase, form_name in _REGIONAL_FORM_TEXT_MAP.items():
            if phrase in form_text:
                region = form_name
                break
    if region is not None:
        fid = dex_index.get((dex, region))
        if fid is not None:
            return fid
    return dex_index.get((dex, None))


def _iter_evobox_templates(section: str) -> list[dict[str, str]]:
    """Parse `{{Evobox-N|...}}` / `{{Evobox/...}}` templates from a section.

    Bulbapedia uses `Evobox-2/3/4` for linear chains and `Evobox/2branch2`
    style names for branched chains; both are matched case-insensitively.
    """
    out: list[dict[str, str]] = []
    for start, end in _find_templates(section):
        raw = section[start:end]
        if not re.match(r"\{\{[Ee]vobox[/-]", raw):
            continue
        out.append(_parse_template(raw))
    return out


def _evobox_edges(
    template: dict[str, str],
    dex_index: dict[tuple[int, str | None], str],
) -> list[tuple[str, str, str]]:
    """Return (prev_form_id, next_form_id, trigger_text) edges for one evobox.

    Handles linear chains (`no1`, `no2`, `no3`) and single-letter branches
    (`no2a`, `no2b`). Trigger text is taken from the outgoing stage's
    `evoN`/`evoNa`/`evoNb` field, flattened from wikitext to plain text.
    """
    # Collect stages: {(stage_num, letter): form_id}
    stages: dict[tuple[int, str], str] = {}
    for pname, pval in template.items():
        m = re.fullmatch(r"no(\d+)([a-z]?)", pname)
        if not m:
            continue
        n = int(m.group(1))
        letter = m.group(2)
        form_text = template.get(f"form{n}{letter}", "")
        fid = _resolve_evobox_stage(pval, form_text, dex_index)
        if fid is not None:
            stages[(n, letter)] = fid

    by_stage: dict[int, list[tuple[str, str]]] = {}
    for (n, letter), fid in stages.items():
        by_stage.setdefault(n, []).append((letter, fid))

    edges: list[tuple[str, str, str]] = []
    stage_nums = sorted(by_stage)
    for i in range(len(stage_nums) - 1):
        cur_n = stage_nums[i]
        nxt_n = stage_nums[i + 1]
        for prev_letter, prev_fid in by_stage[cur_n]:
            for next_letter, next_fid in by_stage[nxt_n]:
                # Trigger field index depends on where the branch splits.
                # For outgoing branches (pikachu → raichu / raichu-alola)
                # the template keys on the NEXT letter (evo2a / evo2b).
                # For incoming branches (pikachu / pikachu-alola → raichu)
                # it keys on the PREV letter. Try next-letter first, fall
                # back to prev-letter, then to the shared evoN.
                trigger = (
                    (template.get(f"evo{cur_n}{next_letter}") if next_letter else None)
                    or (template.get(f"evo{cur_n}{prev_letter}") if prev_letter else None)
                    or template.get(f"evo{cur_n}", "")
                )
                trigger_flat = _flatten_wikitext(trigger)
                edges.append((prev_fid, next_fid, trigger_flat))
    return edges


def _detect_item_slug(trigger_text: str) -> str | None:
    """Map an evobox trigger's flattened text to a known item slug, if any."""
    for name, slug in _ITEM_NAME_TO_SLUG.items():
        if name in trigger_text:
            return slug
    return None


def _classify_trigger(trigger_text: str, item_slug: str | None) -> str | None:
    """Broad-bucket an evobox trigger into a PokéAPI method_details slug.

    Used when synthesizing fresh evolution rows for regional-variant
    forms (Pass 2): PokéAPI never emitted a row for these, so we can't
    inherit a trigger — we have to derive it from the evobox prose.
    Returns None if the trigger doesn't match any known bucket, in which
    case Pass 2 leaves `method_details` unset rather than guessing.

    Order matters: item names are checked first because phrases like
    "Trade holding a Linking Cord" should classify as `use-item`, not
    `trade`. The default bucket is `level-up` — it covers level-based
    triggers plus friendship / time-of-day / known-move level-ups, which
    all share that PokéAPI slug.
    """
    if item_slug:
        return "use-item"
    # Trade triggers: "Trade", "Trade for X", "Trade holding X" (without
    # a recognized item). Match at word boundary to avoid "Traded" noise.
    if re.search(r"\bTrade\b", trigger_text):
        return "trade"
    # Level-based phrasing is the broadest bucket.
    if re.search(r"\b(?:Level|Lv\.?|friendship|happiness|starting)\b", trigger_text, re.I):
        return "level-up"
    return None


def _parse_excluded_games(section: str) -> set[str]:
    """Find games where evolution is explicitly blocked by prose.

    Matches phrases like "... cannot evolve ... in [[Pokémon X and Y]]".
    Narrow by design: the cost of a false positive (removing a valid row)
    is higher than missing a rare phrasing.
    """
    excluded: set[str] = set()
    # Stop at clause boundaries (comma/semicolon) so contrast phrases like
    # "...cannot evolve in SV, but can be transferred to LA..." don't pull
    # the LA link into the exclusion set.
    pattern = re.compile(
        r"\bcannot\b[^.,;]*?\bevolv[a-z]*\b[^.,;]*",
        re.I,
    )
    for m in pattern.finditer(section):
        phrase = m.group(0)
        for link_text, game_ids in _GAME_LINK_TO_GAMES.items():
            if link_text in phrase:
                excluded.update(game_ids)
    return excluded


# --- CLI entry points -----------------------------------------------------------


def _scrape_sources(
    bulba: RateLimitedClient,
    pokeapi: RateLimitedClient,
    min_dex: int,
    max_dex: int,
) -> int:
    species_forms = load_species_id_to_forms()
    all_new: list[dict[str, Any]] = []

    for dex in range(min_dex, max_dex + 1):
        species = pokeapi.get_json(f"{POKEAPI_BASE}/pokemon-species/{dex}/")
        species_id = species["name"]
        english = _english_name(species)
        if english is None:
            print(f"  #{dex:04d} {species_id}: no English name in PokéAPI; skipping")
            continue
        page_title = _species_page_title(english)
        wikitext = fetch_wikitext(bulba, page_title)
        if wikitext is None:
            print(f"  #{dex:04d} {species_id}: Bulbapedia page '{page_title}' not found")
            continue
        # Availability entries are split per regional-form annotation inside
        # parse_sources_from_wikitext; pass the full set of form_ids this
        # species has so the parser can route each segment correctly.
        form_ids = set(species_forms.get(species_id, ()))
        if species_id not in form_ids:
            # No default-form row in forms.json means we have nothing to
            # anchor unannotated segments to. Skip the whole page.
            print(f"  #{dex:04d} {species_id}: no matching form; skipping")
            continue
        rows = parse_sources_from_wikitext(wikitext, species_id, form_ids)
        all_new.extend(rows)
        print(f"  #{dex:04d} {species_id}: {len(rows)} scraped source(s)")

    merged = merge_by_key(load_existing_sources(), all_new, key_fn=_source_key)
    merged.sort(key=source_sort_key)
    TypeAdapter(list[Source]).validate_python(merged)
    SOURCES_PATH.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(merged)} sources to {SOURCES_PATH.relative_to(REPO_ROOT)}")
    return 0


# Per-method subtype allow-lists for `--mode locations`. `method_details`
# values not in the set for a method are skipped — either the mechanic
# implies a multi-valued "location" (island-scan: day x island x species)
# or the row's a known edge case whose extraction quality we haven't
# vetted.
_LOCATION_TARGET_DETAILS: dict[Method, frozenset[str | None]] = {
    Method.STATIC_ENCOUNTER: frozenset({"pokeflute", "squirt-bottle", "devon-scope", None}),
    Method.GIFT: frozenset({None, "gift-egg"}),
    # Wild-encounter scope is restricted to plain wild segments — those
    # whose area text carries no encounter-mode signal (so
    # `_normalize_wild_encounter` returns None). PokéAPI-emitted rows
    # with `method_details` like ``walk`` / ``surf`` / ``mass-outbreak``
    # encode the encounter mechanic and are out of scope here: the
    # Bulbapedia segment that locates them often surfaces with a
    # different mechanic slug (or none), and joining them across
    # mechanics would attach surf-route locations to walk-grass rows.
    Method.WILD_ENCOUNTER: frozenset({None}),
    # Fishing scope: every canonical-order rod-set `_normalize_fishing` can
    # emit (the seven non-empty subsets of {old, good, super}) plus None
    # for segments without explicit rod text. The consumption loop in
    # `_scrape_locations` then does rod-set *intersection* matching against
    # existing rows, so a Bulbapedia segment for `[[Old Rod]]` applies to
    # any existing row whose `method_details` rod-set contains `old-rod`.
    Method.FISHING: frozenset(
        {
            None,
            "old-rod",
            "good-rod",
            "super-rod",
            "old-rod, good-rod",
            "old-rod, super-rod",
            "good-rod, super-rod",
            "old-rod, good-rod, super-rod",
        }
    ),
    # Raid scope: SwSh max-raid + gmax + Crown Tundra dynamax-adventure
    # — Bulbapedia enumerates dens/zones in the `area=` field for these.
    # `tera-raid` (SV) is intentionally absent: Bulbapedia annotates SV
    # tera raids only by star tier (`{{DL|List of N★ Tera Raid Battles
    # (Paldea)|...|N★}}`) with no per-zone enumeration, so there's no
    # location slug to extract. None is also absent — `_normalize_raid`
    # always emits a slug for raid-classified segments.
    Method.RAID: frozenset({"max-raid", "gmax", "dynamax-adventure"}),
    # Trade scope: in-game NPC trades only. The `None`-detail trade rows
    # are version-exclusive cross-cartridge trades (the partner is a human
    # player holding the paired cartridge, not an NPC at a place), so they
    # have no location to backfill and are excluded from the whitelist.
    Method.TRADE: frozenset({"npc-trade"}),
}


def _rod_set(details: str | None) -> frozenset[str]:
    """Parse a fishing `method_details` slug into a rod-set.

    `_normalize_fishing` emits comma-joined canonical-order rod slugs
    (`"old-rod, good-rod"`); PokéAPI rows follow the same shape.
    Used by `_scrape_locations` to do rod-set intersection matching for
    fishing rows. Empty/None input → empty frozenset (semantic: "no rod
    info"; the consumption loop treats this as "accept any rod").
    """
    if not details:
        return frozenset()
    return frozenset(p.strip() for p in details.split(","))


def _fishing_slugs_for_row(
    row: dict[str, Any],
    location_map: dict[tuple[str, str, str, str | None], list[str]],
) -> list[str]:
    """Aggregate Bulbapedia location slugs for a fishing row via rod-set match.

    Walks every `(form_id, game_id, fishing, *)` key in `location_map`
    and yields the slugs whose Bulbapedia rod-set shares at least one
    rod with the existing row's rod-set. An existing row with empty
    rod-set (`method_details=None`) accepts any Bulbapedia segment —
    PokéAPI just didn't classify the rod. First-seen order preserved.
    """
    existing_rods = _rod_set(row.get("method_details"))
    form_id = row["form_id"]
    game_id = row["game_id"]
    method_value = Method.FISHING.value
    slugs: list[str] = []
    seen: set[str] = set()
    for (f, g, m, d), candidate_slugs in location_map.items():
        if (f, g, m) != (form_id, game_id, method_value):
            continue
        bulba_rods = _rod_set(d)
        if existing_rods and bulba_rods and not (existing_rods & bulba_rods):
            continue
        for slug in candidate_slugs:
            if slug not in seen:
                slugs.append(slug)
                seen.add(slug)
    return slugs


def _encounter_mode_set(details: str | None) -> frozenset[str]:
    """Parse a wild `method_details` slug into an encounter-mode set.

    `_normalize_wild_encounter_set` emits comma-joined canonical-order
    mode slugs (`"surf, walk"`); PokéAPI rows follow the same shape
    (`"walk, yellow-flowers"`, `"bubbling-spots, walk"`). Mirrors
    `_rod_set` but for wild encounters. Empty/None input → empty
    frozenset; the consumption loop treats this as "accept any mode".
    """
    if not details:
        return frozenset()
    return frozenset(p.strip() for p in details.split(","))


def _wild_slugs_for_row(
    row: dict[str, Any],
    location_map: dict[tuple[str, str, str, str | None], list[str]],
) -> list[str]:
    """Aggregate Bulbapedia location slugs for a wild row via mode-set match.

    Mirrors `_fishing_slugs_for_row`. Walks every `(form_id, game_id,
    wild-encounter, *)` key in `location_map` and yields slugs whose
    Bulbapedia mode-set shares at least one mode with the existing row's
    mode-set. Existing rows with empty mode-set (`method_details=None`)
    accept any Bulbapedia segment — tier 17 already handled most of
    those, but the branch is preserved for completeness.

    PokéAPI emits combos Bulbapedia doesn't always reflect (`walk,
    yellow-flowers`, `surf, walk`); intersection lets a Bulbapedia
    `walk` segment apply to the combo row, since `walk` is a real mode
    of that row.
    """
    existing_modes = _encounter_mode_set(row.get("method_details"))
    form_id = row["form_id"]
    game_id = row["game_id"]
    method_value = Method.WILD_ENCOUNTER.value
    slugs: list[str] = []
    seen: set[str] = set()
    for (f, g, m, d), candidate_slugs in location_map.items():
        if (f, g, m) != (form_id, game_id, method_value):
            continue
        bulba_modes = _encounter_mode_set(d)
        if existing_modes and bulba_modes and not (existing_modes & bulba_modes):
            continue
        for slug in candidate_slugs:
            if slug not in seen:
                slugs.append(slug)
                seen.add(slug)
    return slugs


def _iter_location_candidates_from_wikitext(
    wikitext: str,
    species_id: str,
    species_form_ids: set[str],
) -> list[tuple[str, str, str, str | None, str]]:
    """Return (form_id, game_id, method, method_details, location_slug) tuples.

    Mirrors `parse_sources_from_wikitext`'s segment walk but keeps only
    rows whose method is in `_LOCATION_TARGET_DETAILS` and that yield a
    parseable location. Static-encounter uses first-wikilink extraction;
    gift and npc-trade use preposition-led extraction (location follows
    ``in`` / ``at`` / ``on`` in the usual "Received from X in Y" /
    "Trade Y on Z" prose). Used by ``--mode locations`` to build a
    lookup applied to existing sources.json rows in place.
    """
    section = _extract_main_games_section(wikitext)
    if not section:
        return []

    results: list[tuple[str, str, str, str | None, str]] = []
    for tmpl in _iter_availability_templates(section):
        name = tmpl["_name"]
        if "/NA" in name or "/Header" in name or "/Footer" in name:
            continue
        if "Entry" not in name:
            continue
        area = tmpl.get("area", "")
        # Skip the area-level `_is_skippable_area` check that --mode sources
        # uses: locations mode iterates over per-segment splits and an area
        # whose first segment is "Evolve {{p|X}}" (skippable on its own) may
        # also contain a non-evolve trailing segment like an NPC trade.
        # The segment-level check below still drops the evolve segment.

        vs: list[str] = []
        if "Entry1" in name:
            v = tmpl.get("v")
            if v:
                vs.append(v)
        elif "Entry2" in name:
            for key in ("v", "v2"):
                v = tmpl.get(key)
                if v:
                    vs.append(v)

        for segment in split_area_segments(area) or [area]:
            if _is_skippable_area(segment):
                continue
            inferred, details_text = _infer_method(segment)
            if inferred is None:
                # Regular Entry templates default to wild-encounter; /None
                # entries semantically mean "no native wild encounter" and
                # never default-classify, so unmatched /None segments are
                # skipped (matching parse_sources_from_wikitext semantics).
                if "None" in name:
                    continue
                inferred = Method.WILD_ENCOUNTER
            target_details = _LOCATION_TARGET_DETAILS.get(inferred)
            if target_details is None:
                continue
            if inferred is Method.WILD_ENCOUNTER:
                # Wild uses mode-set intersection at consumption time
                # (`_wild_slugs_for_row`), so the iter-side filter doesn't
                # apply: emit the canonical-order mode-set string and let
                # the consumer match it against existing PokéAPI combo
                # method_details. Bypasses `target_details` strict check.
                details = _normalize_wild_encounter_set(details_text)
            elif inferred is Method.TRADE:
                # Bulbapedia trade prose ("Trade {{p|Chansey}} on
                # {{rt|14|Kanto}}", "[[Trade]]<sup>Version 2.0.1+</sup>")
                # never normalizes to a slug, so we hard-pin the details to
                # `npc-trade` and let the no-location gate below drop the
                # bare-`[[Trade]]` version-exclusive segments — those have
                # no preposition-led location and the `[[Trade]]` wikilink
                # itself is filtered by `_GENERIC_LOCATION_SLUGS`.
                details = "npc-trade"
            else:
                details = normalize_method_details(inferred, details_text)
                if details not in target_details:
                    continue
            if inferred in (Method.WILD_ENCOUNTER, Method.FISHING, Method.RAID):
                # Fishing and raid segments enumerate places the same
                # way wild does (`[[Routes ...]] ([[Old Rod]])` /
                # `[[Bridge Field/Dens|Bridge Field]], ... ([[Max Raid
                # Battle]])`). The rod / raid-mechanic link is filtered
                # by `_GENERIC_LOCATION_SLUGS`.
                locations = extract_area_locations(segment)
            else:
                single = extract_area_location(
                    segment,
                    prefer_preposition=inferred in (Method.GIFT, Method.TRADE),
                )
                locations = [single] if single is not None else []
            if not locations:
                continue
            resolved_forms = resolve_form_ids_from_segment(segment, species_id, species_form_ids)
            if not resolved_forms:
                continue
            for v in vs:
                games, _dlc = _resolve_version(v)
                for gid in games:
                    if gid not in IN_SCOPE_GAME_IDS:
                        continue
                    for form_id in resolved_forms:
                        for location in locations:
                            results.append((form_id, gid, inferred.value, details, location))
    return results


# Methods where a `method_details=None` row at a known location is
# redundant when a sibling row with the same key (excluding
# method_details) but a non-None method_details exists. The rich row
# carries strictly more information; the None row is an artefact of
# Bulbapedia sources mode emitting unclassified wild / fishing / gift /
# static segments before locations mode backfills the place name.
_NONE_DETAIL_DEDUP_METHODS: frozenset[str] = frozenset(
    {"wild-encounter", "fishing", "static-encounter", "gift"}
)

# All source-key fields except method_details. Used to detect "same
# encounter, different specificity" pairs.
_DEDUP_KEY_FIELDS: tuple[str, ...] = tuple(f for f in SOURCE_KEY_FIELDS if f != "method_details")


def _drop_redundant_none_detail_rows(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Drop rows where method_details=None and a richer sibling exists.

    Two rows are siblings when they match on every SOURCE_KEY_FIELDS
    value except method_details. For dedup-eligible methods (wild,
    fishing, static, gift), a None-detail row alongside one or more
    rich-detail siblings is redundant — its semantic content is fully
    covered by the rich sibling. Rows whose method is outside the
    dedup-eligible set, or that lack a `location`, are left untouched.
    """
    groups: dict[tuple[Any, ...], list[int]] = {}
    for i, row in enumerate(rows):
        if row.get("method") not in _NONE_DETAIL_DEDUP_METHODS:
            continue
        if row.get("location") is None:
            continue
        key = tuple(row.get(f) for f in _DEDUP_KEY_FIELDS)
        groups.setdefault(key, []).append(i)

    drop_indices: set[int] = set()
    for indices in groups.values():
        if len(indices) < 2:
            continue
        none_idx = [i for i in indices if rows[i].get("method_details") is None]
        rich_idx = [i for i in indices if rows[i].get("method_details") is not None]
        if none_idx and rich_idx:
            drop_indices.update(none_idx)

    if not drop_indices:
        return rows, 0
    kept = [r for i, r in enumerate(rows) if i not in drop_indices]
    return kept, len(drop_indices)


def _scrape_locations(
    bulba: RateLimitedClient,
    pokeapi: RateLimitedClient,
    min_dex: int,
    max_dex: int,
) -> int:
    """Backfill `location` on existing static / gift / wild / fishing / raid / npc-trade rows.

    Walks species pages like `--mode sources`, parses each targeted
    segment (see `_LOCATION_TARGET_DETAILS`), extracts a location slug
    via `extract_area_location` / `extract_area_locations`, and applies
    the resulting slug(s) to rows whose `(form_id, game_id, method,
    method_details)` matches and whose `location` is currently None.

    Static-encounter, gift, and npc-trade rows are filled **in place**:
    each matching row receives the first slug Bulbapedia produces.
    These methods are singletons by mechanic (one cave, one NPC).
    Trade scope is restricted to `method_details=npc-trade` —
    version-exclusive cross-cartridge trades (`method_details=None`)
    have no fixed location and are skipped.

    Wild-encounter, fishing, and raid rows are **row-split** when
    Bulbapedia produces multiple location slugs for the matching key.
    The original null-location row is replaced by N clones, each
    carrying a distinct `location` slug. `location` participates in
    `SOURCE_KEY_FIELDS`, so the post-split row set remains uniquely
    keyed.

    Fishing uses **rod-set intersection** matching instead of strict
    `method_details` equality. PokéAPI emits one fishing row per rod
    tier (`old-rod` / `good-rod` / `super-rod` / comma-joined combos);
    Bulbapedia segments typically annotate only the rod actually
    present at that location. A Bulbapedia segment for `[[Old Rod]]`
    therefore applies to any existing row whose `method_details`
    contains `old-rod`. See `_fishing_slugs_for_row`.

    Wild-encounter uses **mode-set intersection** matching for the
    same reason. PokéAPI emits combos (`walk, yellow-flowers`,
    `surf, walk`, `bubbling-spots, walk`) that Bulbapedia segments
    rarely fully express. `_normalize_wild_encounter_set` walks all
    `_WILD_ENCOUNTER_PATTERNS` matches and returns a canonical-order
    comma-joined slug; `_wild_slugs_for_row` matches against existing
    rows where the mode-sets intersect. Species-name SOS slugs
    (`swellow` / `venomoth`) and region-name slugs (`kanto` / `hoenn`)
    don't intersect with any Bulbapedia-emitted mode and stay
    unfilled — deferred for a future hand-fill tier.

    Raid rows use strict `method_details` matching (`max-raid` / `gmax`
    / `dynamax-adventure` are distinct mechanics, not overlapping
    tiers). Tera-raid (`tera-raid`) is intentionally excluded from
    `_LOCATION_TARGET_DETAILS[Method.RAID]` because Bulbapedia annotates
    SV tera raids only by star tier, with no per-zone enumeration.

    Rows that already have a location are left untouched (idempotent
    on re-run).
    """
    species_forms = load_species_id_to_forms()
    # Slugs accumulate per key. Static/gift consumers take the first
    # slug; wild consumers take all unique slugs and clone the row N
    # times. De-dup on append so multiple species pages annotating the
    # same regional form don't inflate the wild row count.
    location_map: dict[tuple[str, str, str, str | None], list[str]] = {}

    for dex in range(min_dex, max_dex + 1):
        species = pokeapi.get_json(f"{POKEAPI_BASE}/pokemon-species/{dex}/")
        species_id = species["name"]
        english = _english_name(species)
        if english is None:
            continue
        page_title = _species_page_title(english)
        wikitext = fetch_wikitext(bulba, page_title)
        if wikitext is None:
            continue
        form_ids = set(species_forms.get(species_id, ()))
        if species_id not in form_ids:
            continue
        for form_id, gid, method, details, slug in _iter_location_candidates_from_wikitext(
            wikitext, species_id, form_ids
        ):
            slugs = location_map.setdefault((form_id, gid, method, details), [])
            if slug not in slugs:
                slugs.append(slug)
        if dex % 200 == 0:
            print(f"  ...scanned through #{dex:04d}; {len(location_map)} keys so far")

    targeted_methods = frozenset(m.value for m in _LOCATION_TARGET_DETAILS)
    existing = load_existing_sources()
    out: list[dict[str, Any]] = []
    filled_by_method: dict[str, int] = {}
    split_count = 0
    skipped_no_match = 0
    for row in existing:
        method = row.get("method")
        if method not in targeted_methods or row.get("location") is not None:
            out.append(row)
            continue
        if method == Method.WILD_ENCOUNTER.value:
            slugs = _wild_slugs_for_row(row, location_map)
        elif method == Method.FISHING.value:
            slugs = _fishing_slugs_for_row(row, location_map)
        else:
            key = (row["form_id"], row["game_id"], method, row.get("method_details"))
            slugs = location_map.get(key) or []
        if not slugs:
            skipped_no_match += 1
            out.append(row)
            continue
        if (
            method in (Method.WILD_ENCOUNTER.value, Method.FISHING.value, Method.RAID.value)
            and len(slugs) > 1
        ):
            for slug in slugs:
                clone = dict(row)
                clone["location"] = slug
                out.append(clone)
            split_count += 1
            filled_by_method[method] = filled_by_method.get(method, 0) + len(slugs)
        else:
            row["location"] = slugs[0]
            out.append(row)
            filled_by_method[method] = filled_by_method.get(method, 0) + 1

    out, dedup_dropped = _drop_redundant_none_detail_rows(out)
    out.sort(key=source_sort_key)
    TypeAdapter(list[Source]).validate_python(out)
    SOURCES_PATH.write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    total = sum(filled_by_method.values())
    breakdown = ", ".join(f"{n} {m}" for m, n in sorted(filled_by_method.items())) or "none"
    print(
        f"locations: filled {total} row(s) ({breakdown}); "
        f"{split_count} wild/fishing/raid row(s) row-split into multiple locations; "
        f"{skipped_no_match} row(s) had no matching segment; "
        f"{dedup_dropped} redundant None-detail row(s) dropped."
    )
    return 0


def _regional_form_name(form_id: str) -> str | None:
    """Return "galar"/"alola"/"hisui"/"paldea" if form_id ends in that suffix."""
    for form_name in _REGIONAL_SUFFIX_MAP.values():
        if form_id.endswith(f"-{form_name}"):
            return form_name
    return None


def _apply_refinement(
    row: dict[str, Any],
    pre_evo: str | None,
    item_slug: str | None,
    method_details: str | None,
    conditions: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return a new row with structured fields populated from a refinement.

    - `pre_evo` fills `from_form` (regional pre-evolution provenance).
    - `item_slug` fills `item` (specific stone/item when PokéAPI emitted a
      generic `use-item` trigger).
    - `method_details` fills `method_details` when the row lacks it —
      necessary for regional-variant rows that early scraper revisions
      wrote with no trigger slug.
    - `conditions` fills any other structured condition field
      (`time_of_day`, `min_happiness`, …) the override carries.

    Returns None if no change — caller keeps the original row. Idempotent:
    existing structured values win over refinements so re-runs don't
    over-annotate.
    """
    updated = dict(row)
    changed = False
    if pre_evo and updated.get("from_form") is None:
        updated["from_form"] = pre_evo
        changed = True
    # Only attach the refined item when PokéAPI emitted use-item without
    # filling `item`. Don't clobber a PokéAPI-provided item.
    if item_slug and updated.get("method_details") == "use-item" and updated.get("item") is None:
        updated["item"] = item_slug
        changed = True
    if method_details and updated.get("method_details") is None:
        updated["method_details"] = method_details
        changed = True
    for cond_key, cond_val in (conditions or {}).items():
        if updated.get(cond_key) is None:
            updated[cond_key] = cond_val
            changed = True
    return updated if changed else None


def _scrape_evolutions(
    bulba: RateLimitedClient,
    pokeapi: RateLimitedClient,
    min_dex: int,
    max_dex: int,
) -> int:
    """Refine evolution Source rows using Bulbapedia's per-species Evolution wikitext.

    Scope is narrow: PokéAPI's evolution mode remains authoritative for the
    trigger catalog. This pass only (a) annotates pre-evo provenance when
    Bulbapedia shows a regional variant on the pre-evo side, (b) adds
    evolution rows for regional-variant forms that PokéAPI's default-form
    attribution skipped entirely, (c) removes rows for games where prose
    explicitly blocks the evolution, and (d) enriches a generic `use-item`
    trigger with a known item slug when the evobox names a specific item.
    """
    dex_index = load_dex_form_index()
    existing = load_existing_sources()

    # Per-edge refinements indexed on the evolved side: (target_form_id) →
    # {'pre_evo': str | None, 'item': str | None, 'excluded': set[str]}.
    # 'excluded' games come from prose on the target's page and apply to
    # every evolution row whose form_id == target (all triggers, all games).
    refinements: dict[str, dict[str, Any]] = {}

    # Edges keyed by (prev_form_id, next_form_id) so we can cross-reference
    # game scope for regional-form row creation. Value is the trigger_flat.
    edges_seen: dict[tuple[str, str], str] = {}

    for dex in range(min_dex, max_dex + 1):
        species = pokeapi.get_json(f"{POKEAPI_BASE}/pokemon-species/{dex}/")
        species_id = species["name"]
        english = _english_name(species)
        if english is None:
            continue
        page_title = _species_page_title(english)
        wikitext = fetch_wikitext(bulba, page_title)
        if wikitext is None:
            continue
        section = _extract_evolution_section(wikitext)
        if not section:
            continue
        excluded = _parse_excluded_games(section)
        if excluded:
            # Prose exclusions apply to the page's species itself — that's
            # the evolved target the prose describes ("X cannot evolve into
            # <species> in game Y").
            refinements.setdefault(species_id, _new_refinement_entry())["excluded"] |= excluded

        for tmpl in _iter_evobox_templates(section):
            for prev_fid, next_fid, trigger in _evobox_edges(tmpl, dex_index):
                if prev_fid == next_fid:
                    continue
                edges_seen[(prev_fid, next_fid)] = trigger
                prev_is_regional = _regional_form_name(prev_fid) is not None
                next_is_regional = _regional_form_name(next_fid) is not None
                item_slug = _detect_item_slug(trigger)
                # Refine when the edge involves a regional form OR names a
                # game-gating item. Plain level-up chains on default forms
                # are already correctly attributed by PokéAPI mode.
                if not (prev_is_regional or next_is_regional or item_slug):
                    continue
                entry = refinements.setdefault(next_fid, _new_refinement_entry())
                # For regional-target edges we need `from_form` regardless
                # of whether the pre-evo is regional (raichu-alola from
                # pikachu-alola) or default (marowak-alola from cubone):
                # the provenance is equally useful for app-side grouping.
                if (next_is_regional or prev_is_regional) and entry["pre_evo"] is None:
                    entry["pre_evo"] = prev_fid
                if item_slug and entry["item"] is None:
                    entry["item"] = item_slug
                # Classify the trigger into a method_details slug. Only
                # used by Pass 2 (fresh rows for regional forms) — Pass 1
                # inherits method_details from the PokéAPI row.
                if next_is_regional and entry["method_details"] is None:
                    entry["method_details"] = _classify_trigger(trigger, item_slug)

    # Apply regional-trigger overrides as gap-fills. Only sets fields the
    # wikitext scan didn't already populate, so Bulbapedia-derived values
    # win over the hardcoded list.
    for form_id, override in _REGIONAL_TRIGGER_OVERRIDES.items():
        entry = refinements.setdefault(form_id, _new_refinement_entry())
        if override.get("method_details") and entry["method_details"] is None:
            entry["method_details"] = override["method_details"]
        if override.get("item") and entry["item"] is None:
            entry["item"] = override["item"]
        for cond_key in _REFINEMENT_CONDITION_FIELDS:
            if cond_key in override and cond_key not in entry["conditions"]:
                entry["conditions"][cond_key] = override[cond_key]

    if not refinements:
        print("no evolution refinements detected; sources.json unchanged.")
        return 0

    # Pre-compute existing non-evolution rows per form_id — used as the
    # game-scope hint for regional-form evolution rows we're about to add.
    rows_by_form: dict[str, list[dict[str, Any]]] = {}
    for row in existing:
        rows_by_form.setdefault(row["form_id"], []).append(row)

    # Pass 1 — modify or drop existing evolution rows.
    modified = 0
    removed = 0
    kept: list[dict[str, Any]] = []
    for row in existing:
        if row.get("method") != "evolution":
            kept.append(row)
            continue
        fid = row["form_id"]
        ref = refinements.get(fid)
        if ref is None:
            kept.append(row)
            continue
        if row["game_id"] in ref["excluded"]:
            removed += 1
            continue
        updated = _apply_refinement(
            row,
            ref["pre_evo"],
            ref["item"],
            ref["method_details"],
            ref.get("conditions"),
        )
        if updated is not None:
            kept.append(updated)
            modified += 1
        else:
            kept.append(row)

    # Pass 2 — add fresh evolution rows for regional forms PokéAPI skipped.
    added = 0
    new_rows: list[dict[str, Any]] = []
    for next_fid, ref in refinements.items():
        region = _regional_form_name(next_fid)
        if region is None:
            continue
        # If the regional form already has any evolution row, Pass 1 handled it.
        if any(r.get("method") == "evolution" and r["form_id"] == next_fid for r in kept):
            continue
        # Scope: union of games where the regional form appears in
        # non-evolution rows (proof of obtainability) with the hardcoded
        # regional game list for the region. Excluded games are filtered
        # out.
        scoped = {r["game_id"] for r in rows_by_form.get(next_fid, [])}
        scoped |= set(_REGIONAL_GAMES.get(region, ()))
        scoped -= ref["excluded"]
        if not scoped:
            continue
        for game_id in sorted(scoped):
            entry: dict[str, Any] = {
                "form_id": next_fid,
                "game_id": game_id,
                "method": Method.EVOLUTION.value,
            }
            # Prefer classifier output (covers level-up / trade / use-item);
            # fall back to "use-item" when only an item slug was detected
            # (classifier has the item in scope and would have returned
            # use-item, but stay robust if classification was skipped).
            method_details = ref["method_details"] or ("use-item" if ref["item"] else None)
            if method_details:
                entry["method_details"] = method_details
            if ref["item"]:
                entry["item"] = ref["item"]
            if ref["pre_evo"]:
                entry["from_form"] = ref["pre_evo"]
            for cond_key, cond_val in (ref.get("conditions") or {}).items():
                entry[cond_key] = cond_val
            new_rows.append(entry)
            added += 1

    merged = kept + new_rows
    merged.sort(key=source_sort_key)
    TypeAdapter(list[Source]).validate_python(merged)
    SOURCES_PATH.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        f"evolutions refinement: {modified} modified, {removed} removed, "
        f"{added} added; wrote {len(merged)} sources."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("sources", "evolutions", "locations"),
        default="sources",
        help="What to scrape (default: sources)",
    )
    parser.add_argument(
        "--max-dex",
        type=int,
        default=1025,
        help="Highest national-dex number to scrape (default: 1025)",
    )
    parser.add_argument(
        "--min-dex",
        type=int,
        default=1,
        help="Lowest national-dex number to scrape (default: 1)",
    )
    args = parser.parse_args()

    if not FORMS_PATH.exists():
        print("error: data/forms.json is empty; run pokeapi --mode forms first", file=sys.stderr)
        return 1

    with (
        httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30.0) as bulba_raw,
        httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30.0) as pokeapi_raw,
    ):
        bulba = RateLimitedClient(bulba_raw, MIN_REQUEST_INTERVAL, CACHE_DIR)
        pokeapi = RateLimitedClient(pokeapi_raw, MIN_REQUEST_INTERVAL, POKEAPI_CACHE_DIR)
        if args.mode == "sources":
            return _scrape_sources(bulba, pokeapi, args.min_dex, args.max_dex)
        if args.mode == "locations":
            return _scrape_locations(bulba, pokeapi, args.min_dex, args.max_dex)
        return _scrape_evolutions(bulba, pokeapi, args.min_dex, args.max_dex)


if __name__ == "__main__":
    sys.exit(main())
