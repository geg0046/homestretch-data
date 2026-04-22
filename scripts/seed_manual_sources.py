"""Merge manually-curated Source rows into `data/sources.json`.

Covers two categories upstream doesn't express as structured data:

1. **Breeding-only babies** (Pichu, Cleffa, Elekid, etc.) — one row per
   (baby, game) pair for games that: (a) are >= the baby's introduction
   generation and (b) have the parent species available. Encoded as
   `method=breeding`, `from_form=<parent>`.
2. **Game Corner prize Pokémon** (Abra/Dratini/Porygon in RBY/GSC, plus
   a handful of others) — encoded as `method=purchase,
   method_details=game-corner`.

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
