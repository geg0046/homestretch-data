# Credits & attribution

HomeStretch's data is assembled from publicly-available sources. This file
acknowledges the upstreams whose work made the dataset possible.

## Upstream data sources

### PokéAPI

<https://pokeapi.co/> — the primary source for species, forms, encounter
methods, evolution chains, and per-game version metadata. PokéAPI's
data is dedicated to the public domain under
[CC0 1.0 Universal](https://creativecommons.org/publicdomain/zero/1.0/).

Used for:

- The form catalog (`data/forms.json` — names, sprites, default-form
  detection, regional-form taxonomy).
- The base game catalog (`data/games.json` — version names, generation
  scoping).
- Per-game encounter rows for in-scope titles where PokéAPI's encounter
  endpoints carry the data (Gen 1–2, Gen 6+).
- Evolution-chain rows for `method=evolution`.

### Bulbapedia

<https://bulbapedia.bulbagarden.net/> — fills gaps where PokéAPI's
encounter coverage is thin (locations, raid dens, fishing rod tiers,
regional-variant evolution provenance, post-launch trade additions).
Bulbapedia content is licensed under
[Creative Commons Attribution-NonCommercial-ShareAlike 2.5](https://creativecommons.org/licenses/by-nc-sa/2.5/).

Used for (via `scrapers/bulbapedia.py`):

- The `==Game locations==` Availability tables, parsed as MediaWiki
  wikitext via the public `api.php` endpoint.
- Branched-evolution provenance and item-trigger refinements
  (`--mode evolutions`).
- Post-launch trade additions parsed from `Entry*/None` templates.

The dataset captures factual game state (where a Pokémon can be caught,
what method is required, etc.) — facts that exist independent of
Bulbapedia's editorial work. Where Bulbapedia's wikitext is the
extraction source, the project credits Bulbapedia here and shares the
dataset under a compatible BY-SA license.

### Serebii

<https://www.serebii.net/> — used as the authoritative reference for
HOME-deposit compatibility decisions. Specifically the non-depositable
list at <https://www.serebii.net/pokemonhome/nondepositablepokemon.shtml>
governs the `SKIP_FORM_IDS_HOME_UNREACHABLE` set in
`scrapers/pokeapi.py`. No automated scraping of Serebii — manual
cross-checks only.

## Trademark

Pokémon, Pokédex, Pokémon HOME, and all related names, marks, sprites,
and game titles are © Nintendo / Creatures / GAME FREAK. **HomeStretch
is an unofficial, fan-made project** and is not affiliated with,
endorsed by, or sponsored by Nintendo, The Pokémon Company, Creatures
Inc., GAME FREAK, or any other rightsholder.

The repository name and any user-facing branding deliberately avoid
"Pokémon" per the project's hard rules
(see [CLAUDE.md](CLAUDE.md), rule 9).

## Licensing of this repository

- **Data** (`data/`, `schemas/`) — Creative Commons Attribution-ShareAlike
  4.0 International. See [LICENSE-DATA](LICENSE-DATA).
- **Code** (`src/`, `scripts/`, `scrapers/`, `tests/`) — MIT.
  See [LICENSE](LICENSE).

If you redistribute or remix the dataset, please credit HomeStretch
with a link to <https://github.com/geg0046/homestretch-data> and
preserve the upstream attributions above.

## Contact

Project email: `homestretchapp@outlook.com` (used for scraper
User-Agent strings and outward-facing identifiers; see
`scrapers/CLAUDE.md`).
