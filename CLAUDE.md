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

## ID conventions

All IDs are lowercase, alphanumeric, hyphen-separated: `^[a-z0-9]+(?:-[a-z0-9]+)*$`.

- **Game IDs**: PokéAPI version names directly (`scarlet`, `legends-arceus`,
  `omega-ruby`, `lets-go-pikachu`, `x`, `y`); services that have no PokéAPI
  version keep descriptive names (`go`, `home`, `bank`, `poke-transporter`).
- **Species IDs**: `vulpix`, `ninetales`, `mr-mime`, `porygon-z`.
- **Form IDs**: `<species-id>-<form-suffix>` when non-default:
  `vulpix-alolan`, `rotom-wash`, `vivillon-polar`. The default form's ID
  equals the species ID: `vulpix`.

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
