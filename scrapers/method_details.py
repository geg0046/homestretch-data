"""Normalize `method_details` strings to short categorical slugs.

Fixes the Bulbapedia-sources leak where the flattened wikitext area
ships as `method_details` when regex inference fails (raid location
strings, wild-encounter prose with `{{rt|...}}` templates, etc.).

Strategy: strip wiki markup, then for each Method run a small recognizer
that picks a slug out of the cleaned text, or returns None when no
recognized signal remains. `game_id` already scopes availability, so
dropping the location prose is not information loss for planning.

Evolution rows are NOT processed here — they come from PokéAPI with
structured fields populated, and their method_details are already
clean trigger slugs. If an evolution row somehow ends up with prose,
it'll be returned as-is (the pass-through clause catches it).
"""

from __future__ import annotations

import re

from homestretch_data.models import Method

# --- Wiki-markup stripping ------------------------------------------------

_TEMPLATE_WITH_ARG_RE = re.compile(
    r"\{\{(?:rt|rtn|tt|color|TM|HM|Sup/[A-Za-z0-9]+|sup/[A-Za-z0-9]+|DL|tt)\|([^{}]*?)\}\}",
    re.IGNORECASE,
)
_TEMPLATE_SIMPLE_RE = re.compile(r"\{\{[^{}]*?\}\}")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WIKI_LINK_PIPED_RE = re.compile(r"\[\[[^\[\]|]*\|([^\[\]]+)\]\]")
_WIKI_LINK_PLAIN_RE = re.compile(r"\[\[([^\[\]|]+)\]\]")
_BOLD_ITALIC_RE = re.compile(r"'{2,}")
_NBSP_RE = re.compile(r"&nbsp;|&#160;")
_ALPHA_LINK_RE = re.compile(r"link=Alpha Pokémon\|\d+px", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")


def _strip_wiki(text: str) -> str:
    """Remove Bulbapedia-specific wiki markup, leaving plain text."""
    # Templates like {{rt|10|Galar}} → "10", {{tt|text|tooltip}} → "text"
    prev = None
    while prev != text:
        prev = text
        text = _TEMPLATE_WITH_ARG_RE.sub(lambda m: m.group(1).split("|")[0], text)
    text = _TEMPLATE_SIMPLE_RE.sub("", text)
    text = _WIKI_LINK_PIPED_RE.sub(r"\1", text)
    text = _WIKI_LINK_PLAIN_RE.sub(r"\1", text)
    text = _HTML_TAG_RE.sub(" ", text)
    text = _BOLD_ITALIC_RE.sub("", text)
    text = _NBSP_RE.sub(" ", text)
    text = _ALPHA_LINK_RE.sub("", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


# --- Per-method recognizers ----------------------------------------------

_FISHING_RODS = ("old-rod", "good-rod", "super-rod")
_FISHING_ROD_PATTERNS = {
    "old-rod": re.compile(r"\bold\s+rod\b", re.IGNORECASE),
    "good-rod": re.compile(r"\bgood\s+rod\b", re.IGNORECASE),
    "super-rod": re.compile(r"\bsuper\s+rod\b", re.IGNORECASE),
}


def _normalize_fishing(cleaned: str) -> str | None:
    rods = [rod for rod, pat in _FISHING_ROD_PATTERNS.items() if pat.search(cleaned)]
    if not rods:
        return None
    # Canonical order: old, good, super (ascending capability).
    ordered = [r for r in _FISHING_RODS if r in rods]
    return ", ".join(ordered)


_WILD_ENCOUNTER_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bmass(?:ive)?\s+mass\s+outbreak", re.IGNORECASE), "mass-outbreak"),
    (re.compile(r"\bmass\s+outbreak", re.IGNORECASE), "mass-outbreak"),
    (re.compile(r"\bspace[- ]time\s+distortion", re.IGNORECASE), "space-time-distortion"),
    (re.compile(r"\bsos\s+encounter", re.IGNORECASE), "sos-encounter"),
    (re.compile(r"\bbubbling\s+spots?\b", re.IGNORECASE), "bubbling-spots"),
    (re.compile(r"\bhorde\s+encounter", re.IGNORECASE), "horde"),
    (re.compile(r"\brough\s+terrain\b", re.IGNORECASE), "rough-terrain"),
    (re.compile(r"\brock\s+smash\b", re.IGNORECASE), "rock-smash"),
    (re.compile(r"\bsurf(?:ing)?\b", re.IGNORECASE), "surf"),
    (re.compile(r"\boverworld\b", re.IGNORECASE), "overworld"),
    (re.compile(r"\bgrass\b", re.IGNORECASE), "walk"),
)


def _normalize_wild_encounter(cleaned: str) -> str | None:
    """First-match single-slug emitter — used by `--mode sources` parse path.

    Order in `_WILD_ENCOUNTER_PATTERNS` matters: more specific patterns
    (mass-outbreak / sos-encounter / horde) come before broader ones
    (surf / overworld / walk). Used by the main scraper path; changing
    its return shape would alter `SOURCE_KEY_FIELDS` for newly-emitted
    rows and produce duplicates under additive merge.
    """
    for pat, slug in _WILD_ENCOUNTER_PATTERNS:
        if pat.search(cleaned):
            return slug
    return None


def _normalize_wild_encounter_set(cleaned: str) -> str | None:
    """All-matches canonical-order emitter — used only by `--mode locations`.

    Walks every pattern in `_WILD_ENCOUNTER_PATTERNS`, collects the
    matching slugs, and returns them as a comma-joined slug in the
    pattern's tuple order (mass-outbreak / space-time-distortion / sos-
    encounter / bubbling-spots / horde / rough-terrain / rock-smash /
    surf / overworld / walk). The locations-mode consumption loop uses
    this with mode-set intersection against PokéAPI-emitted comma-joined
    rows (`walk, yellow-flowers`, `surf, walk`, `bubbling-spots, walk`),
    mirroring fishing's rod-set match.
    """
    matched_slugs: set[str] = set()
    for pat, slug in _WILD_ENCOUNTER_PATTERNS:
        if pat.search(cleaned):
            matched_slugs.add(slug)
    if not matched_slugs:
        return None
    seen: set[str] = set()
    ordered: list[str] = []
    for _, slug in _WILD_ENCOUNTER_PATTERNS:
        if slug in matched_slugs and slug not in seen:
            ordered.append(slug)
            seen.add(slug)
    return ", ".join(ordered)


# Note: `only-one` / `one-time` is intentionally NOT recognized here.
# Every static-encounter row is by definition a one-time placement; the
# slug added no discriminating information beyond `method=static-encounter`
# itself, so it's treated as vacuous (see `_VACUOUS_DETAILS_BY_METHOD`).
# Plain singleton statics land with `method_details=None`.
_STATIC_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bpok[ée]\s*flute\b", re.IGNORECASE), "pokeflute"),
    (re.compile(r"\bsquirt\s*bottle\b", re.IGNORECASE), "squirt-bottle"),
    (re.compile(r"\bwailmer\s*pail\b", re.IGNORECASE), "wailmer-pail"),
    (re.compile(r"\bdevon\s+scope\b", re.IGNORECASE), "devon-scope"),
    (re.compile(r"\bisland\s+scan\b", re.IGNORECASE), "island-scan"),
    (re.compile(r"\broaming\b", re.IGNORECASE), "roaming"),
)


def _normalize_static_encounter(cleaned: str) -> str | None:
    for pat, slug in _STATIC_PATTERNS:
        if pat.search(cleaned):
            return slug
    return None


_RAID_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bgigantamax\b|\bgmax\b", re.IGNORECASE), "gmax"),
    (re.compile(r"\bdynamax\s+adventure", re.IGNORECASE), "dynamax-adventure"),
    (re.compile(r"\btera\s+raid", re.IGNORECASE), "tera-raid"),
    (re.compile(r"\bmax\s+raid", re.IGNORECASE), "max-raid"),
)
_RAID_TIER_RE = re.compile(r"(\d)\s*[★*]")


def _normalize_raid(cleaned: str) -> str | None:
    # Prefer a specific kind slug (gmax / dynamax-adventure) when present.
    for pat, slug in _RAID_PATTERNS:
        if pat.search(cleaned):
            return slug
    # Fall back to the lowest tier number we see, if any.
    tiers = sorted({int(m.group(1)) for m in _RAID_TIER_RE.finditer(cleaned)})
    if tiers:
        return f"{tiers[0]}-star"
    return None


# --- Pass-through slug detection -----------------------------------------

# If the raw value already looks like a short slug (lowercase, short,
# no wiki markup, no weird punctuation), return it unchanged. Covers
# PokéAPI-sourced rows, existing clean rows, and anything the
# scrapers already normalize.
_SLUG_CHAR_RE = re.compile(r"^[a-z0-9][a-z0-9 ,\-]*$")
_MAX_SLUG_LEN = 64


def _is_slug_shaped(raw: str) -> bool:
    if len(raw) > _MAX_SLUG_LEN:
        return False
    if any(c in raw for c in "{}<>|[]'\"\n\r\t"):
        return False
    return bool(_SLUG_CHAR_RE.match(raw))


# --- Public entry point --------------------------------------------------


# Slugs that never add information. `only-one` / `one-time` mark singleton
# obtainability, which every static-encounter / gift / fossil-revive / event
# row is by definition — wild / fishing / raid are the exceptions and
# those methods don't produce this marker. Drop the slug so the field only
# appears when it actually disambiguates something.
_UNIVERSALLY_VACUOUS_DETAILS: frozenset[str] = frozenset({"only-one", "one-time"})


def normalize_method_details(method: Method, raw: str | None) -> str | None:
    """Normalize a scraper-emitted `method_details` string to a slug.

    Returns None when no recognizable slug remains after stripping wiki
    markup and applying per-method recognizers. This is distinct from
    the rule-7 collapse (None when details == method.value).
    """
    if raw is None:
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    if stripped.lower() == method.value:
        return None  # Rule 7.
    if stripped.lower() in _UNIVERSALLY_VACUOUS_DETAILS:
        return None  # Rule-7 generalization.

    # Fast path: already slug-shaped; trust it.
    if _is_slug_shaped(stripped):
        return stripped

    cleaned = _strip_wiki(stripped)
    if not cleaned or cleaned == method.value:
        return None

    # After stripping, some values are clean and short — trust them too.
    # Require at least one letter to reject bare route numbers like "10"
    # left behind by templates such as {{rt|10|Kalos}}, and reject
    # multi-word prose by forbidding spaces outside comma-joined lists.
    if (
        _is_slug_shaped(cleaned.lower())
        and len(cleaned) <= _MAX_SLUG_LEN
        and any(c.isalpha() for c in cleaned)
        and " " not in cleaned.lower().replace(", ", "_")
    ):
        return cleaned.lower()

    # Per-method recognizers.
    if method is Method.FISHING:
        return _normalize_fishing(cleaned)
    if method is Method.WILD_ENCOUNTER:
        return _normalize_wild_encounter(cleaned)
    if method is Method.STATIC_ENCOUNTER:
        return _normalize_static_encounter(cleaned)
    if method is Method.RAID:
        return _normalize_raid(cleaned)

    # For other methods (gift, event, breeding, purchase, fossil-revive,
    # evolution, trade, transfer), prose is rare and drops to None.
    return None
