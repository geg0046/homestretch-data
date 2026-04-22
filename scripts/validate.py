"""Validate every data file against its Pydantic model and check cross-file references.

Run via: `uv run python scripts/validate.py`
Exits non-zero if validation fails. Intended to run in CI and pre-commit.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from homestretch_data.models import Form, Game, Source, Transfer

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"

# Lowercase hyphen-slug pattern (same shape as FormId / GameId). Applied to
# Source string fields where Pydantic only knows they're `str | None`:
# item, held_item, location, known_move, known_move_type, party_type.
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SLUG_FIELDS: tuple[str, ...] = (
    "item",
    "held_item",
    "location",
    "known_move",
    "known_move_type",
    "party_type",
)


def _load_list[T](path: Path, model: type[T]) -> list[T]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    adapter = TypeAdapter(list[model])
    return adapter.validate_python(raw)


def _check_unique(items: Iterable[object], attr: str, kind: str, errors: list[str]) -> None:
    seen: dict[str, int] = {}
    for i, item in enumerate(items):
        key = getattr(item, attr)
        if key in seen:
            errors.append(
                f"{kind}: duplicate {attr}={key!r} at index {i} (first seen at {seen[key]})"
            )
        else:
            seen[key] = i


def main() -> int:
    errors: list[str] = []

    try:
        games = _load_list(DATA_DIR / "games.json", Game)
        forms = _load_list(DATA_DIR / "forms.json", Form)
        sources = _load_list(DATA_DIR / "sources.json", Source)
        transfers = _load_list(DATA_DIR / "transfers.json", Transfer)
    except ValidationError as exc:
        print("schema validation failed:", file=sys.stderr)
        print(exc, file=sys.stderr)
        return 1

    _check_unique(games, "id", "games.json", errors)
    _check_unique(forms, "id", "forms.json", errors)

    game_ids = {g.id for g in games}
    form_ids = {f.id for f in forms}

    for i, s in enumerate(sources):
        if s.game_id not in game_ids:
            errors.append(f"sources.json[{i}]: unknown game_id={s.game_id!r}")
        if s.form_id not in form_ids:
            errors.append(f"sources.json[{i}]: unknown form_id={s.form_id!r}")
        for ref_field in ("trade_species", "party_species", "from_form"):
            ref = getattr(s, ref_field)
            if ref is not None and ref not in form_ids:
                errors.append(f"sources.json[{i}]: unknown {ref_field}={ref!r}")
        for slug_field in _SLUG_FIELDS:
            val = getattr(s, slug_field)
            if val is not None and not _SLUG_RE.match(val):
                errors.append(f"sources.json[{i}]: {slug_field}={val!r} is not a lowercase slug")
        if s.method_details is not None and not _SLUG_RE.match(s.method_details):
            # method_details may be a comma-joined slug list for fishing rods.
            parts = [p.strip() for p in s.method_details.split(",")]
            if not all(_SLUG_RE.match(p) for p in parts if p):
                errors.append(
                    f"sources.json[{i}]: method_details={s.method_details!r} "
                    "is not a slug (or comma-joined slug list)"
                )
        if s.method_details is not None and s.method_details == s.method.value:
            errors.append(
                f"sources.json[{i}]: method_details={s.method_details!r} "
                "duplicates method; drop the field (rule 7)"
            )

    for i, t in enumerate(transfers):
        if t.from_id not in game_ids:
            errors.append(f"transfers.json[{i}]: unknown from_id={t.from_id!r}")
        if t.to_id not in game_ids:
            errors.append(f"transfers.json[{i}]: unknown to_id={t.to_id!r}")

    if errors:
        print(f"{len(errors)} validation error(s):", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print(
        f"OK: {len(games)} games, {len(forms)} forms, "
        f"{len(sources)} sources, {len(transfers)} transfers"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
