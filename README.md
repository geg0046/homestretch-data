# homestretch-data

Open data for **HomeStretch** — a form-aware, HOME-transfer-aware living-dex
planner. Given the games you own and the forms you still need, HomeStretch
tells you the most efficient path to complete your Pokémon HOME dex.

This repo is the **dataset + schema**. The web app that consumes it lives in
`homestretch-app` (separate repo) and ships the JSON files below as static
assets — no runtime backend.

## Scope

Covers the 24 in-scope mainline games (RBY, GSC, XY, ORAS, SM/USUM, LGPE,
SwSh, BDSP, PLA, SV, LZA) plus the HOME / Bank / GO / Poké Transporter
service nodes. Gen 3–5 mainline (RSE, FRLG, DPPt, HGSS, BW, B2W2) is
intentionally excluded — see [CLAUDE.md](CLAUDE.md).

Current scale: **28 games · 1,338 forms · ~54 k source rows · 29 transfer
edges** (~360 KB gzipped JSON for the whole dataset).

## What's here

| Path             | Purpose                                                |
| ---------------- | ------------------------------------------------------ |
| `data/*.json`    | Authoritative data: games, forms, sources, transfers.  |
| `schemas/`       | Auto-generated JSON Schema per entity type.            |
| `src/`           | Pydantic models — the schema source of truth.          |
| `scripts/`       | `validate.py`, `coverage_audit.py`, `export_schemas.py`, `seed_manual_sources.py`. |
| `scrapers/`      | Site-specific scrapers (run on developer machines, results committed). |
| `CLAUDE.md`      | Conventions for contributors and AI coding assistants. |
| `CREDITS.md`     | Upstream attribution (PokéAPI, Bulbapedia, Serebii).   |

## Data model

- **Game** — a game or service (Scarlet, Violet, HOME, Bank, GO, …).
- **Form** — a species + form variant (Alolan Vulpix, Vivillon-Polar, …).
  Form inclusion is gated by the HOME-deposit test: a form is tracked
  iff it can be deposited into Pokémon HOME and come out as that same
  form in at least one HOME-compatible game.
- **Source** — how to obtain a Form in a specific Game (method, optional
  location, optional method-specific details like rod tier or raid type).
- **Transfer** — a directed edge describing how Pokémon move between
  Games/Services toward Pokémon HOME.

## Consuming the data

The dataset is shipped as static JSON. Typical consumption patterns:

- Load `data/games.json` + `data/forms.json` + `data/transfers.json` once
  (small, ~17 KB gzip combined).
- Either load `data/sources.json` once (~360 KB gzip) or shard by
  `game_id` and lazy-load per game (each shard is 4–48 KB gzip).
- Validate against `schemas/*.schema.json` if you want to cross-check
  the format from non-Python consumers.

JSON files are 2-space indented with a trailing newline (UTF-8). Records
are sorted by `id` or logical key so diffs stay reviewable.

## Getting started (contributors)

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync                                 # create .venv and install deps
uv run pytest -q                        # run tests
uv run python scripts/validate.py       # cross-file reference + invariant check
uv run python scripts/coverage_audit.py # gap report (hits PokéAPI)
uv run python scripts/export_schemas.py # regenerate schemas/ from Pydantic models
uv run ruff check . && uv run ruff format .
pre-commit install                      # optional: run validate + format on commit
```

## Contributing

Most of the dataset is scraper-driven. To extend or refresh it:

- **Bulk data** — re-run the relevant scraper under `scrapers/` (PokéAPI
  for forms / encounters / evolutions; Bulbapedia for locations and
  evolution refinements). Then `uv run python scripts/seed_manual_sources.py`
  to re-apply hand-curated rows. Commit results.
- **Hand-curated rows** — edit `scripts/seed_manual_sources.py`. Lookups
  there cover breeding babies, Game Corner prizes, fossil-revive
  locations, gift NPCs, USUM totems, event distributions, and other
  cases PokéAPI / Bulbapedia don't natively express.
- **Schema** — edit Pydantic models under `src/homestretch_data/models/`,
  then run `scripts/export_schemas.py` to regenerate `schemas/`.

Pre-commit and CI run `validate.py`. Bulk additions should also leave
`coverage_audit.py --offline` clean (no new zero-source forms, no new
regional-dex gaps). See [CLAUDE.md](CLAUDE.md) for the full conventions
and [scrapers/CLAUDE.md](scrapers/CLAUDE.md) for scraper-specific rules.

## Licensing

- **Data** (`data/`, `schemas/`) — Creative Commons Attribution-ShareAlike
  4.0 International. See [LICENSE-DATA](LICENSE-DATA).
- **Code** (`src/`, `scripts/`, `scrapers/`, `tests/`) — MIT. See
  [LICENSE](LICENSE).

Pokémon and all related trademarks are © Nintendo / Creatures / GAME FREAK.
This project is unofficial and not affiliated with or endorsed by any
trademark holder. Upstream data attribution and licensing constraints
are documented in [CREDITS.md](CREDITS.md).
