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
scripts/         CLI utilities (validate, coverage_audit, export_schemas,
                 seed_manual_sources)
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
11. **Form inclusion is gated by the HOME-deposit test.** A form enters
    `data/forms.json` only if it can be deposited into Pokémon HOME and
    come out as that same form in at least one HOME-compatible game.
    Authoritative reference: <https://www.serebii.net/pokemonhome/nondepositablepokemon.shtml>.
    Scraper-side enforcement and the categorised exclusion list live in
    `scrapers/CLAUDE.md` under `SKIP_FORM_IDS_HOME_UNREACHABLE`.

## ID conventions

All IDs are lowercase, alphanumeric, hyphen-separated: `^[a-z0-9]+(?:-[a-z0-9]+)*$`.

- **Game IDs**: PokéAPI version names directly (`scarlet`, `legends-arceus`,
  `omega-ruby`, `lets-go-pikachu`, `x`, `y`); services that have no PokéAPI
  version keep descriptive names (`go`, `home`, `bank`, `poke-transporter`).
- **Species IDs**: `vulpix`, `ninetales`, `mr-mime`, `porygon-z`.
- **Form IDs**: `<species-id>-<form-suffix>` when non-default:
  `vulpix-alolan`, `rotom-wash`, `vivillon-polar`. The default form's ID
  equals the species ID: `vulpix`.

## Version-exclusive rows

Species catchable only on the paired version (Sword ↔ Shield, BD ↔ SP,
Scarlet ↔ Violet, RBY pairs, GSC pairs, LGPE pair, XY, SM, USUM, ORAS)
get a `method=trade` row on the off-version with
`notes="Version-exclusive; trade from <Paired>."`. No `trade_species` —
the partner is a human player holding the paired cartridge, not a
specific Pokémon. This is distinct from the existing
`method=trade, trade_species=<species>` pattern used for mutual trade
evolutions like Shelmet ↔ Karrablast.

## Common commands

```bash
uv sync                                                   # first-time setup
uv run pytest -q                                          # all tests
uv run pytest tests/test_models.py::test_game_round_trip  # single test
uv run python scripts/validate.py                         # cross-file reference check
uv run python scripts/coverage_audit.py                   # gap report (hits PokéAPI; use --offline to skip)
uv run python scripts/export_schemas.py                   # regenerate schemas/ from models
uv run ruff check . && uv run ruff format .
uv run pip-audit --skip-editable                          # CI mirror
uv run --with packaging --no-project python scripts/check_dep_age.py <pkg>  # 48h rule check

# Scrapers — run on-demand; commit results. seed_manual_sources runs last
# so manual rows (breeding babies, Game Corner prizes) stay in sources.json.
uv run python scrapers/pokeapi.py    --mode forms      --max-dex 1025
uv run python scrapers/pokeapi.py    --mode sources    --max-dex 1025
uv run python scrapers/pokeapi.py    --mode evolutions --max-dex 1025
uv run python scrapers/bulbapedia.py --mode sources    --max-dex 1025
uv run python scrapers/bulbapedia.py --mode evolutions --max-dex 1025
uv run python scrapers/bulbapedia.py --mode locations  --max-dex 1025
uv run python scripts/seed_manual_sources.py
```

## Architecture

- **Pydantic models are the single source of truth.** `schemas/` is
  regenerated from them; tests and scrapers round-trip through
  `TypeAdapter(list[Model])` before writing. Schema changes always start
  at `src/homestretch_data/models/`.
- **`scripts/validate.py` does cross-file reference checking** beyond
  schema validation: every `sources.json` `game_id`/`form_id` and every
  `transfers.json` `from_id`/`to_id` must exist in games.json/forms.json.
  It also enforces five data-health invariants (all pass on current
  data; guard against future regressions):
    1. unique source keys (dedup tuple matches the scraper merge key);
    2. exactly one default form per species;
    3. default form id equals species id;
    4. forms tagged `event-only` only have event / gift / transfer sources;
    5. every default form has ≥1 source row.
  Both CI and pre-commit run it.
- **`scripts/coverage_audit.py` reports gap-level coverage**: per-game
  counts, zero-source forms by category, and a regional-dex
  expected-vs-covered report driven by PokéAPI's pokedex endpoints.
  `HOME_TRANSFER_ONLY_DEX` subtracts HOME-transfer-only species from
  the expected set so the "missing" list reflects real in-game gaps.
  Run after any bulk change; both sections should stay at zero.
- **Scraper merge is idempotent (`setdefault` by id/key).** After changing
  categorization or skip/event logic, `rm data/forms.json` (or
  `data/sources.json`) before re-running, or stale entries survive.
- **`scripts/seed_manual_sources.py` is the third data-authoring tool**
  alongside the PokéAPI and Bulbapedia scrapers. It seeds rows that
  upstreams don't carry (breeding babies, Game Corner prizes, fossil-
  revive / gift / static-encounter location dicts, USUM totems, event
  distributions, gender-difference female mirrors). Re-run it after any
  fresh scrape — manual rows are rebuilt from authoritative dicts and
  merged additively into the scraped data.

Scraper-specific conventions live in [`scrapers/CLAUDE.md`](scrapers/CLAUDE.md).

## Adding a new entity

1. Add the entry to the relevant `data/*.json` file, keeping the array ordered.
2. Run `uv run python scripts/validate.py`. Fix any errors it reports.
3. If you changed a model in `src/homestretch_data/models/`, also run
   `uv run python scripts/export_schemas.py` to refresh `schemas/`.
4. For bulk additions (dozens of rows, new tier), also run
   `uv run python scripts/coverage_audit.py` and confirm both the
   zero-source and regional-dex sections stay empty.
5. Pre-commit will run steps 2–3 automatically on commit.

## Style

- Python: ruff-enforced (see `pyproject.toml`). Type hints on every function.
- JSON: 2-space indent, trailing newline, UTF-8.
- Commit messages: `[area] short imperative summary` (e.g.
  `[data] add Gen 9 games and HOME transfer edges`).
