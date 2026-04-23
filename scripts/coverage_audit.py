"""Audit source coverage for the HomeStretch dataset.

Read-only. Produces a text (or markdown) report answering:
  1. Per-game: how many sources and forms are covered?
  2. Per-form-category: which forms have zero source rows?
  3. For Gen 8/9 games: how does species coverage compare to the
     PokéAPI regional dex?
  4. Which zero-source forms are known event/item-only (gap-filling
     should route to `scripts/seed_manual_sources.py`, not scrapers)?

Run via: `uv run python scripts/coverage_audit.py`
Write markdown: `uv run python scripts/coverage_audit.py --out COVERAGE.md`
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scrapers"))
from utils import RateLimitedClient

from homestretch_data.models.form import FormCategory

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
CACHE_DIR = REPO_ROOT / ".cache" / "pokeapi"

USER_AGENT = (
    "HomeStretch/0.1 (+https://github.com/geg0046/homestretch-data; "
    "contact: homestretchapp@outlook.com)"
)

# Mirrors POKEDEX_TO_GAMES in scrapers/pokeapi.py but keyed by game for
# the audit's denominator lookups. Each Gen 8/9 game lists the PokéAPI
# pokedex names whose union defines its "expected species" set.
GAME_TO_POKEDEXES: dict[str, tuple[str, ...]] = {
    "sword": ("galar", "isle-of-armor", "crown-tundra"),
    "shield": ("galar", "isle-of-armor", "crown-tundra"),
    "brilliant-diamond": ("extended-sinnoh",),
    "shining-pearl": ("extended-sinnoh",),
    "legends-arceus": ("hisui",),
    "scarlet": ("paldea", "kitakami", "blueberry"),
    "violet": ("paldea", "kitakami", "blueberry"),
    "legends-za": ("lumiose-city",),
}

# Functional forms that are known event/item-only acquisitions. Used to
# flag which zero-source forms should be routed to seed_manual_sources.py
# versus treated as a scraper gap. Not exhaustive — the goal is to
# highlight the obvious item/plate/drive families, not re-categorise.
KNOWN_ITEM_OR_EVENT_ONLY: frozenset[str] = frozenset(
    {
        # Arceus plates
        *(
            f"arceus-{t}"
            for t in (
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
                "unknown",
            )
        ),
        # Silvally memories
        *(
            f"silvally-{t}"
            for t in (
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
        ),
        # Genesect drives
        "genesect-burn",
        "genesect-chill",
        "genesect-douse",
        "genesect-shock",
        # Form-change items / story items
        "deoxys-attack",
        "deoxys-defense",
        "deoxys-speed",
        "dialga-origin",
        "palkia-origin",
        "giratina-origin",
        "shaymin-sky",
        "hoopa-unbound",
        "keldeo-resolute",
        "floette-eternal",
        "tornadus-therian",
        "thundurus-therian",
        "landorus-therian",
        "enamorus-therian",
        "zygarde-10-power-construct",
        "zygarde-50-power-construct",
    }
)


def _load(name: str) -> list[dict[str, Any]]:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def _fetch_pokedex_species(client: RateLimitedClient | None, pokedex_name: str) -> set[str]:
    """Return the set of species IDs in the named PokéAPI pokedex.

    If `client` is None (offline mode), returns an empty set.
    """
    if client is None:
        return set()
    url = f"https://pokeapi.co/api/v2/pokedex/{pokedex_name}/"
    data = client.get_json(url)
    return {entry["pokemon_species"]["name"] for entry in data["pokemon_entries"]}


def _per_game_table(
    games: list[dict[str, Any]],
    forms: list[dict[str, Any]],
    sources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    form_to_species = {f["id"]: f["species_id"] for f in forms}
    rows_by_game: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in sources:
        rows_by_game[s["game_id"]].append(s)

    out: list[dict[str, Any]] = []
    for g in games:
        gid = g["id"]
        rows = rows_by_game.get(gid, [])
        form_ids = {r["form_id"] for r in rows}
        species_ids = {form_to_species[fid] for fid in form_ids if fid in form_to_species}
        out.append(
            {
                "game_id": gid,
                "generation": g.get("generation"),
                "sources": len(rows),
                "forms": len(form_ids),
                "species": len(species_ids),
            }
        )
    return out


def _category_gaps(
    forms: list[dict[str, Any]],
    sources: list[dict[str, Any]],
) -> tuple[Counter[tuple[str, ...]], dict[str, list[dict[str, Any]]]]:
    covered = {s["form_id"] for s in sources}
    by_cats: Counter[tuple[str, ...]] = Counter()
    bucketed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for f in forms:
        if f["id"] in covered:
            continue
        cats = tuple(sorted(f.get("categories") or []))
        by_cats[cats] += 1
        # Primary bucket for enumeration: pick the first category, or
        # "uncategorised" when the form has none.
        bucket = cats[0] if cats else "uncategorised"
        bucketed[bucket].append(f)
    return by_cats, bucketed


def _regional_dex_section(
    games: list[dict[str, Any]],
    forms: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    client: RateLimitedClient | None,
) -> list[dict[str, Any]]:
    form_to_species = {f["id"]: f["species_id"] for f in forms}
    covered_by_game: dict[str, set[str]] = defaultdict(set)
    for s in sources:
        sp = form_to_species.get(s["form_id"])
        if sp is not None:
            covered_by_game[s["game_id"]].add(sp)

    out: list[dict[str, Any]] = []
    for g in games:
        if g.get("generation") not in (8, 9):
            continue
        gid = g["id"]
        pokedex_names = GAME_TO_POKEDEXES.get(gid, ())
        expected: set[str] = set()
        for pdn in pokedex_names:
            expected |= _fetch_pokedex_species(client, pdn)
        covered = covered_by_game.get(gid, set())
        missing = sorted(expected - covered) if expected else []
        out.append(
            {
                "game_id": gid,
                "pokedexes": pokedex_names,
                "expected": len(expected),
                "covered_from_expected": len(expected & covered) if expected else 0,
                "missing": missing,
            }
        )
    return out


def _render(
    per_game: list[dict[str, Any]],
    by_cats: Counter[tuple[str, ...]],
    bucketed: dict[str, list[dict[str, Any]]],
    regional: list[dict[str, Any]],
    totals: dict[str, int],
    as_markdown: bool,
) -> str:
    def h1(t: str) -> str:
        return f"# {t}\n" if as_markdown else f"\n=== {t} ===\n"

    def h2(t: str) -> str:
        return f"\n## {t}\n" if as_markdown else f"\n--- {t} ---\n"

    lines: list[str] = []
    lines.append(h1("HomeStretch coverage audit"))
    lines.append(
        f"\nTotals: {totals['games']} games, {totals['forms']} forms, "
        f"{totals['sources']} sources.\n"
    )

    lines.append(h2("Per-game coverage"))
    lines.append("\n| game | gen | sources | forms | species |")
    lines.append("|---|---|---:|---:|---:|")
    for row in per_game:
        lines.append(
            f"| {row['game_id']} | {row['generation']} | "
            f"{row['sources']} | {row['forms']} | {row['species']} |"
        )
    lines.append("")

    lines.append(h2("Zero-source forms by category tuple"))
    lines.append("")
    for cats, n in by_cats.most_common():
        label = ",".join(cats) if cats else "(none)"
        lines.append(f"- {n:>4}  {label}")
    lines.append("")

    for bucket in (
        FormCategory.FUNCTIONAL.value,
        FormCategory.TOTEM.value,
        FormCategory.GENDER_DIFFERENCE.value,
    ):
        entries = bucketed.get(bucket, [])
        if not entries:
            continue
        lines.append(h2(f"Zero-source `{bucket}` forms ({len(entries)})"))
        lines.append("")
        for f in sorted(entries, key=lambda x: (x["national_dex"], x["id"])):
            flag = "  [item/event-only]" if f["id"] in KNOWN_ITEM_OR_EVENT_ONLY else ""
            lines.append(f"- #{f['national_dex']:>4}  {f['id']}{flag}")
        lines.append("")

    if regional:
        lines.append(h2("Regional-dex completeness (Gen 8/9)"))
        lines.append("")
        lines.append("| game | expected | covered | missing (first 20) |")
        lines.append("|---|---:|---:|---|")
        for row in regional:
            if row["expected"] == 0:
                missing_preview = "(offline — no PokéAPI fetch)"
            else:
                missing_preview = ", ".join(row["missing"][:20])
                if len(row["missing"]) > 20:
                    missing_preview += f", … (+{len(row['missing']) - 20} more)"
                missing_preview = missing_preview or "—"
            lines.append(
                f"| {row['game_id']} | {row['expected']} | "
                f"{row['covered_from_expected']} | {missing_preview} |"
            )
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="Write markdown to this path")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Skip PokéAPI fetches (regional-dex section will be empty)",
    )
    args = parser.parse_args()

    games = _load("games.json")
    forms = _load("forms.json")
    sources = _load("sources.json")

    per_game = _per_game_table(games, forms, sources)
    by_cats, bucketed = _category_gaps(forms, sources)

    client: RateLimitedClient | None = None
    raw_client: httpx.Client | None = None
    if not args.offline:
        raw_client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30.0)
        client = RateLimitedClient(raw_client, 1.0, CACHE_DIR)

    try:
        regional = _regional_dex_section(games, forms, sources, client)
    finally:
        if raw_client is not None:
            raw_client.close()

    totals = {"games": len(games), "forms": len(forms), "sources": len(sources)}
    as_markdown = args.out is not None
    report = _render(per_game, by_cats, bucketed, regional, totals, as_markdown)

    if args.out is not None:
        args.out.write_text(report, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
