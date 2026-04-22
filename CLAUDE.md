# CLAUDE.md — conventions for HomeStretch data

Guidance for AI coding assistants (Claude Code, Cursor, etc.) working in this repo.
Prefer these rules over defaults when they conflict.

## What this repo is

`homestretch-data` is the **data repository** for the HomeStretch project — a
form-aware, Pokémon-HOME-transfer-aware living-dex planner. The app that
consumes this data lives in a separate repo (`homestretch-app`).

The crown jewels are the JSON files under `data/`. Python code in `src/` is
only scaffolding: Pydantic models that serve as the canonical schema, plus
scripts for validation and schema export.

## Layout

```
data/            authoritative JSON data (games, forms, sources, transfers)
schemas/         auto-generated JSON Schema files (DO NOT hand-edit)
src/homestretch_data/
  models/        Pydantic models — the source of truth for the schema
scripts/         CLI utilities (validate, export_schemas)
scrapers/        site-specific scrapers that write into data/
tests/           pytest suites
```

## Hard rules

1. **Never hand-edit files in `schemas/`.** They are generated from
   `src/homestretch_data/models/` by `scripts/export_schemas.py`. If a schema
   needs to change, change the model.
2. **Every dependency pin must be ≥48 hours old at the time of pinning.**
   Check PyPI's upload time before adding or bumping anything. Applies to
   transitive deps too — constrain them in `[tool.uv.constraint-dependencies]`
   if resolution picks a version <48h old.
3. **All installs live inside `.venv/`.** Use `uv add` / `uv run`. Never
   `pip install` at the system level.
4. **Data files are JSON arrays**, one per entity type (`games.json`,
   `forms.json`, `sources.json`, `transfers.json`). Keep them sorted by `id`
   (or logical ordering) so diffs stay reviewable.
5. **Scrapers run only on developer machines or in GitHub Actions**, never
   in response to end-user traffic. Set a descriptive `User-Agent` with a
   contact email; rate-limit to 1 req/sec; respect `robots.txt`.
6. **No secrets in the repo.** Use `.env` (git-ignored) locally; GitHub
   Actions secrets in CI.
7. **`Source.method_details` is omitted when it equals the method enum
   value.** A row like `{method:"gift", method_details:"gift"}` duplicates
   information — either drop the field or fill it with genuine extra detail.
8. **No runtime backend.** This dataset is consumed as static JSON by a
   static-site app.
9. **Don't put "Pokémon" in repo names, domains, or user-facing branding.**
   The Pokémon Company is aggressive about trademark; public-facing copy
   must note the project is unofficial.
10. **Never skip git hooks (`--no-verify`).** If a hook fails, investigate
    and fix the underlying issue rather than bypassing it.

## ID conventions

All IDs are lowercase, alphanumeric, hyphen-separated: `^[a-z0-9]+(?:-[a-z0-9]+)*$`.

- **Game IDs**: PokéAPI version names directly (`scarlet`, `legends-arceus`,
  `omega-ruby`, `lets-go-pikachu`, `x`, `y`); services that have no PokéAPI
  version keep descriptive names (`go`, `home`, `bank`, `poke-transporter`).
- **Species IDs**: `vulpix`, `ninetales`, `mr-mime`, `porygon-z`.
- **Form IDs**: `<species-id>-<form-suffix>` when non-default:
  `vulpix-alolan`, `rotom-wash`, `vivillon-polar`. The default form's ID
  equals the species ID: `vulpix`.

## Common commands

```bash
uv sync                                                   # first-time setup
uv run pytest -q                                          # all tests
uv run pytest tests/test_models.py::test_game_round_trip  # single test
uv run python scripts/validate.py                         # cross-file reference check
uv run python scripts/export_schemas.py                   # regenerate schemas/ from models
uv run ruff check . && uv run ruff format .
uv run pip-audit --skip-editable                          # CI mirror
uv run --with packaging --no-project python scripts/check_dep_age.py <pkg>  # 48h rule check

# Scrapers — run on-demand; commit results
uv run python scrapers/pokeapi.py    --mode forms      --max-dex 1025
uv run python scrapers/pokeapi.py    --mode sources    --max-dex 1025
uv run python scrapers/pokeapi.py    --mode evolutions --max-dex 1025
uv run python scrapers/bulbapedia.py --mode sources    --max-dex 1025
uv run python scrapers/bulbapedia.py --mode evolutions --max-dex 1025
```

## Architecture

- **Pydantic models are the single source of truth.** `schemas/` is
  regenerated from them; tests and scrapers round-trip through
  `TypeAdapter(list[Model])` before writing. Schema changes always start
  at `src/homestretch_data/models/`.
- **`scripts/validate.py` does cross-file reference checking** beyond
  schema validation: every `sources.json` `game_id`/`form_id` and every
  `transfers.json` `from_id`/`to_id` must exist in games.json/forms.json.
  Both CI and pre-commit run it.
- **Scraper merge is idempotent (`setdefault` by id/key).** After changing
  categorization or skip/event logic, `rm data/forms.json` (or
  `data/sources.json`) before re-running, or stale entries survive.

Scraper-specific conventions live in [`scrapers/CLAUDE.md`](scrapers/CLAUDE.md).

## Adding a new entity

1. Add the entry to the relevant `data/*.json` file, keeping the array ordered.
2. Run `uv run python scripts/validate.py`. Fix any errors it reports.
3. If you changed a model in `src/homestretch_data/models/`, also run
   `uv run python scripts/export_schemas.py` to refresh `schemas/`.
4. Pre-commit will do both of the above automatically on commit.

## Style

- Python: ruff-enforced (see `pyproject.toml`). Type hints on every function.
- JSON: 2-space indent, trailing newline, UTF-8.
- Commit messages: `[area] short imperative summary` (e.g.
  `[data] add Gen 9 games and HOME transfer edges`).
