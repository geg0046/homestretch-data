"""Scrape Gen 8/9 source rows from Bulbapedia's per-species Game locations.

Usage:
    uv run python scrapers/bulbapedia.py --mode sources    --max-dex 1025
    uv run python scrapers/bulbapedia.py --mode evolutions --max-dex 1025

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
from pydantic import TypeAdapter
from utils import RateLimitedClient, merge_by_key

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
# "unknown" — sources mode filters emissions to GEN_8_9_GAME_IDS below.
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
    "Let's Go, Pikachu!": "lets-go-pikachu",
    "Let's Go, Eevee!": "lets-go-eevee",
}

# DLC / expansion labels that appear as `v=` values. Each maps to the base
# game IDs it applies to plus a DLC slug recorded in `requires_dlc`. The
# first DLC for SwSh (Isle of Armor / Crown Tundra) predates our schema's
# requires_dlc convention but is captured here for completeness.
DLC_TO_GAMES: dict[str, tuple[tuple[str, ...], str]] = {
    "Expansion Pass": (("sword", "shield"), "expansion-pass"),
    "The Isle of Armor": (("sword", "shield"), "isle-of-armor"),
    "The Crown Tundra": (("sword", "shield"), "crown-tundra"),
    "The Teal Mask": (("scarlet", "violet"), "teal-mask"),
    "The Indigo Disk": (("scarlet", "violet"), "indigo-disk"),
    "The Hidden Treasure of Area Zero": (
        ("scarlet", "violet"),
        "hidden-treasure-of-area-zero",
    ),
}

# Sources mode emits rows only for these games; everything else is dropped
# after parsing (PokéAPI already covers those).
GEN_8_9_GAME_IDS = frozenset(
    {
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


def fetch_wikitext(
    bulba: RateLimitedClient,
    page_title: str,
) -> str | None:
    """Return the raw wikitext of a Bulbapedia page, or None if missing."""
    url = (
        f"{BULBAPEDIA_API}?action=parse"
        f"&page={quote(page_title, safe='')}"
        f"&prop=wikitext&format=json&formatversion=2"
    )
    data = bulba.get_json(url)
    if "error" in data:
        return None
    return data.get("parse", {}).get("wikitext")


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
    """
    m = re.search(r"==\s*Game locations\s*==+", wikitext)
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


def _infer_method(area: str) -> tuple[Method, str]:
    """Pick a Method for an Availability/Entry area. Default: wild-encounter."""
    flat = _flatten_wikitext(area)
    for pat, method in _METHOD_PATTERNS:
        if pat.search(flat):
            return (method, flat)
    return (Method.WILD_ENCOUNTER, flat)


def _is_skippable_area(area: str) -> bool:
    flat = _flatten_wikitext(area).strip().lower()
    if flat in {"", "unobtainable", "none", "trade"}:
        return True
    if flat.startswith("unobtainable"):
        return True
    # Evolve-only rows are already covered by --mode evolutions (both the
    # PokéAPI and Bulbapedia variants); don't emit a parallel wild/static
    # row that would just duplicate an evolution source.
    return flat.startswith("evolve")


def parse_sources_from_wikitext(wikitext: str, form_id: str) -> list[dict[str, Any]]:
    """Emit Source dicts for each base-game location entry on a species page.

    Only rows whose resolved game_id lies in GEN_8_9_GAME_IDS are returned;
    pre-Gen-8 games are already covered by PokéAPI.
    """
    section = _extract_main_games_section(wikitext)
    if not section:
        return []

    rows: list[dict[str, Any]] = []
    for tmpl in _iter_availability_templates(section):
        name = tmpl["_name"]
        if "/None" in name or "/NA" in name or "/Header" in name or "/Footer" in name:
            continue
        if "Entry" not in name:
            continue
        area = tmpl.get("area", "")
        if _is_skippable_area(area):
            continue
        method, details_text = _infer_method(area)

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

        for v in vs:
            games, dlc = _resolve_version(v)
            for gid in games:
                if gid not in GEN_8_9_GAME_IDS:
                    continue
                entry: dict[str, Any] = {
                    "form_id": form_id,
                    "game_id": gid,
                    "method": method.value,
                }
                if details_text and details_text.lower() != method.value:
                    entry["method_details"] = details_text
                if dlc:
                    entry["requires_dlc"] = dlc
                rows.append(entry)
    return rows


# --- Disk state -----------------------------------------------------------------


def _source_key(entry: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        entry["form_id"],
        entry["game_id"],
        entry["method"],
        entry.get("method_details") or "",
    )


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
        # Attribute to the default form (species_id). Regional variants
        # living on the same page are not split here — that refinement
        # happens in evolutions mode for branched regional lines.
        default_form_id = species_id if species_id in species_forms else None
        if default_form_id is None:
            print(f"  #{dex:04d} {species_id}: no matching form; skipping")
            continue
        rows = parse_sources_from_wikitext(wikitext, default_form_id)
        all_new.extend(rows)
        print(f"  #{dex:04d} {species_id}: {len(rows)} Gen 8/9 source(s)")

    merged = merge_by_key(load_existing_sources(), all_new, key_fn=_source_key)
    merged.sort(key=_source_key)
    TypeAdapter(list[Source]).validate_python(merged)
    SOURCES_PATH.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(merged)} sources to {SOURCES_PATH.relative_to(REPO_ROOT)}")
    return 0


def _scrape_evolutions(
    bulba: RateLimitedClient,
    pokeapi: RateLimitedClient,
    min_dex: int,
    max_dex: int,
) -> int:
    # Placeholder: evolutions refinement mode is tracked for Phase D of the
    # plan. The stub here makes the CLI surface match pokeapi.py's for
    # consistency; implementation will land in a follow-up commit.
    print(
        "bulbapedia --mode evolutions not yet implemented; see plan Phase D for scope.",
        file=sys.stderr,
    )
    return 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("sources", "evolutions"),
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
        return _scrape_evolutions(bulba, pokeapi, args.min_dex, args.max_dex)


if __name__ == "__main__":
    sys.exit(main())
