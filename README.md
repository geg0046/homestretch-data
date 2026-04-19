# homestretch-data

Open data for **HomeStretch** — a form-aware, Pokémon-HOME-transfer-aware
living-dex planner. Given the games you own and the forms you still need,
HomeStretch tells you the most efficient path to complete your HOME dex.

This repo holds the **dataset + schema**. The web app that consumes it lives
in `homestretch-app` (separate repo).

## What's here

| Path            | Purpose                                                |
| --------------- | ------------------------------------------------------ |
| `data/*.json`   | Authoritative data: games, forms, sources, transfers. |
| `schemas/`      | Auto-generated JSON Schema per entity type.            |
| `src/`          | Pydantic models (the schema source of truth).          |
| `scripts/`      | `validate.py`, `export_schemas.py`.                    |
| `scrapers/`     | Site-specific scrapers (run offline, commit results).  |
| `CLAUDE.md`     | Conventions for AI coding assistants.                  |

## Data model

- **Game** — a game or service (Scarlet, Violet, HOME, Bank, GO, ...).
- **Form** — a species + form variant (Alolan Vulpix, Vivillon-Polar, ...).
- **Source** — how to obtain a Form in a specific Game.
- **Transfer** — a directed edge describing how Pokémon move between
  Games/Services toward Pokémon HOME.

## Getting started (contributors)

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync              # create .venv and install deps
uv run pytest        # run tests
uv run python scripts/validate.py       # validate data/ files
uv run python scripts/export_schemas.py # regenerate schemas/
pre-commit install   # optional: run checks automatically on commit
```

## Contributing data

1. Edit the relevant JSON file under `data/`.
2. Run `uv run python scripts/validate.py`.
3. Open a PR. CI will re-run validation.

## Licensing

- **Data** (`data/`, `schemas/`) — Creative Commons Attribution-ShareAlike
  4.0 International (see [`LICENSE-DATA`](LICENSE-DATA)).
- **Code** (`src/`, `scripts/`, `scrapers/`, `tests/`) — MIT
  (see [`LICENSE`](LICENSE)).

Pokémon and all related trademarks are © Nintendo / Creatures / GAME FREAK.
This project is unofficial and not affiliated with or endorsed by any
trademark holder.

## Data sourcing & attribution

Where a data entry is derived from an external source, record that source in
the entry's `notes` field (or in a sibling `CREDITS.md` for bulk imports).
Scrapers live in `scrapers/` and are run by maintainers — **user-facing apps
consume this repo's static JSON, never the upstream sources**.
