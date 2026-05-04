"""Validate every data file against its Pydantic model and check cross-file references.

Run via: `uv run python scripts/validate.py`
Exits non-zero if validation fails. Intended to run in CI and pre-commit.
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from homestretch_data.models import Form, Game, Source, Transfer

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"

sys.path.insert(0, str(REPO_ROOT / "scrapers"))
from utils import SOURCE_KEY_FIELDS  # noqa: E402

# Methods a form with the `event-only` category may legitimately have. Any
# other method on such a form indicates miscategorisation.
_EVENT_ONLY_ALLOWED_METHODS: frozenset[str] = frozenset({"event", "gift", "transfer"})

# Sprite URLs must come from PokéAPI's GitHub mirror. Tightens the
# `^https://` regex on the model to flag scraper bugs that would otherwise
# point sprite_url somewhere unexpected. Loosen if we ever self-host.
_SPRITE_URL_PREFIX = "https://raw.githubusercontent.com/PokeAPI/sprites/"
_SPRITE_URL_SUFFIX = ".png"

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

    for f in forms:
        if not (
            f.sprite_url.startswith(_SPRITE_URL_PREFIX)
            and f.sprite_url.endswith(_SPRITE_URL_SUFFIX)
        ):
            errors.append(
                f"forms.json: {f.id!r} sprite_url={f.sprite_url!r} must start with "
                f"{_SPRITE_URL_PREFIX!r} and end with {_SPRITE_URL_SUFFIX!r}"
            )

    game_ids = {g.id for g in games}
    form_ids = {f.id for f in forms}

    # Invariant: each species has exactly one default form, and that
    # default form's id equals the species id (per CLAUDE.md ID conventions).
    by_species: dict[str, list[Form]] = defaultdict(list)
    for f in forms:
        by_species[f.species_id].append(f)
    for species_id, fs in by_species.items():
        defaults = [f for f in fs if f.is_default]
        if len(defaults) == 0:
            alt_ids = [f.id for f in fs]
            errors.append(
                f"forms.json: species={species_id!r} has no default form (alt forms: {alt_ids})"
            )
        elif len(defaults) > 1:
            default_ids = [f.id for f in defaults]
            errors.append(
                f"forms.json: species={species_id!r} has {len(defaults)} "
                f"default forms (expected 1): {default_ids}"
            )
        else:
            default = defaults[0]
            if default.id != species_id:
                errors.append(
                    f"forms.json: default form {default.id!r} must equal "
                    f"its species_id={species_id!r}"
                )

    for i, s in enumerate(sources):
        if s.game_id not in game_ids:
            errors.append(f"sources.json[{i}]: unknown game_id={s.game_id!r}")
        if s.form_id not in form_ids:
            errors.append(f"sources.json[{i}]: unknown form_id={s.form_id!r}")
        for ref_field in ("trade_species", "party_species", "from_form"):
            ref = getattr(s, ref_field)
            if ref is not None and ref not in form_ids:
                errors.append(f"sources.json[{i}]: unknown {ref_field}={ref!r}")
        if s.from_game is not None and s.from_game not in game_ids:
            errors.append(f"sources.json[{i}]: unknown from_game={s.from_game!r}")
        if s.from_game is not None and s.from_game == s.game_id:
            errors.append(
                f"sources.json[{i}]: from_game={s.from_game!r} equals game_id; "
                "from_game must reference a different (paired) game"
            )
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
        if s.method_details is not None and s.method_details in form_ids:
            errors.append(
                f"sources.json[{i}]: method_details={s.method_details!r} "
                "is a form_id; likely a {{p|<species>}} partner leaked "
                "through as method_details prose"
            )

    # Invariant: every source row must be unique on its identity tuple.
    # Reuses SOURCE_KEY_FIELDS so the invariant tracks whatever the scraper
    # merge key tracks — if the scraper considers two rows distinct, so does
    # this check, and vice versa.
    key_first_idx: dict[tuple, int] = {}
    for i, s in enumerate(sources):
        key = tuple(getattr(s, f, None) for f in SOURCE_KEY_FIELDS)
        # Normalise enum -> value so the tuple is hashable and comparable
        # across JSON-loaded and Pydantic-object flows.
        key = tuple(v.value if hasattr(v, "value") else v for v in key)
        if key in key_first_idx:
            errors.append(
                f"sources.json[{i}]: duplicate source key, first seen at index {key_first_idx[key]}"
            )
        else:
            key_first_idx[key] = i

    # Invariant: forms tagged `event-only` may only have event / gift /
    # transfer method rows. Anything else indicates miscategorisation.
    event_only_form_ids = {f.id for f in forms if "event-only" in (f.categories or [])}
    for i, s in enumerate(sources):
        if s.form_id in event_only_form_ids and s.method.value not in _EVENT_ONLY_ALLOWED_METHODS:
            errors.append(
                f"sources.json[{i}]: form={s.form_id!r} is event-only but "
                f"method={s.method.value!r} is not event/gift/transfer"
            )

    # Invariant: every default form has at least one source row. If a
    # species is in forms.json we must be able to say how to obtain it.
    default_form_ids = {f.id for f in forms if f.is_default}
    covered_form_ids = {s.form_id for s in sources}
    for fid in sorted(default_form_ids - covered_form_ids):
        errors.append(f"forms.json: default form {fid!r} has zero source rows in sources.json")
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
