# CLAUDE.md — conventions for scrapers/

Scraper-specific durable decisions. See top-level `CLAUDE.md` for repo-wide
rules.

## Philosophy

**Scrape > hand-curate** when upstream (PokéAPI, pret decomps) carries the
data. Hand curation is reserved for facts upstream doesn't express: event
distributions, game-locked gift/starter sources, known gaps.

## Form inclusion / exclusion

Inside `scrapers/pokeapi.py` these sets govern what ends up in
`data/forms.json`:

- **Battle-only forms are dropped automatically** via PokéAPI's
  `is_battle_only` flag. This covers megas, Gmax, primals, Aegislash-blade,
  Ash-Greninja, etc. — forms that revert after battle and don't occupy a
  distinct HOME storage slot.
- **`SKIP_FORM_IDS_INGAME`** — forms not storable in their origin game
  regardless of the battle-only flag. Koraidon/Miraidon mount-modes
  (visual ride transformations of the default) and `eternatus-eternamax`
  (story-only boss).
- **`SKIP_FORM_IDS_HOME_UNREACHABLE`** — storable in origin game but
  explicitly blocked from Bank/HOME transfer. Spiky-eared Pichu, the
  ORAS cosplay Pikachu family, all 8 cap Pikachus. Rule: if it can't
  reach HOME, it doesn't get a HOME-living-dex slot.
- **`EVENT_ONLY_FORM_IDS`** — HOME-storable forms with no wild encounters,
  obtained only via event distributions. Tagged `event-only` in
  `categories`; their acquisition is recorded in `sources.json` with
  `method=event`.
- **`FUNCTIONAL_FORM_IDS`** — form-level override for species whose
  non-default forms are species-level-categorized incorrectly (e.g.
  `floette-eternal` on a species otherwise in `COSMETIC_SPECIES`).

**Don't re-enable megas / Gmax or remove `SKIP_FORM_IDS` entries without
user sign-off.** These exclusions are deliberate.

## Gender differences

- **One gender-difference entry per species**, not one per colour × gender.
  The canonical default is the species-default entry; the alternate gender
  is one explicit entry tagged `gender-difference`.
- **Tagged ONLY `gender-difference`**, never also `functional`, even when
  the forms have different abilities or stats. The semantic meaning is
  gender; keep category lists short.

## Starter game-locks

`pikachu-starter` / `eevee-starter` are Let's Go P/E partner starters.
They are **NOT** `event-only` — their game-locking is expressed via
`sources.json` rows (`method=gift`, `game_id=lets-go-pikachu` /
`lets-go-eevee`), not via `FormCategory`.

## Idempotency

Scraper merge uses `setdefault` by id/key. After changing categorization
or skip/event logic, `rm data/forms.json` (or `data/sources.json`) before
re-running — otherwise stale entries survive. The PokéAPI response cache
at `.cache/pokeapi/` is separate and safe to delete independently if you
want a clean refetch (a full scrape is ~25 min at 1 req/sec; cached runs
are seconds).

## HTTP hygiene

- **User-Agent**: `HomeStretch/0.1 (+https://github.com/geg0046/homestretch-data;
  contact: homestretchapp@outlook.com)`. Use the **project email**
  (`homestretchapp@outlook.com`), never a maintainer's personal address,
  in any outward-facing string.
- 1 req/sec rate limit via `RateLimitedClient`.
- Retry with exponential backoff (5 attempts).
- Respect `robots.txt` on sites without an API.

## Sources mode: method mapping

`ENCOUNTER_METHOD_TO_METHOD` collapses PokéAPI's fine-grained encounter
method names onto the coarser `Method` enum (rods → fishing; walk / surf /
sos / overworld → wild-encounter; only-one / pokeflute / squirt-bottle /
wailmer-pail / devon-scope / island-scan → static-encounter; gift /
gift-egg → gift; npc-trade → trade). **Methods absent from this map log
a warning and are skipped**, not silently miscategorised. If a full scrape
emits warnings, either extend the map or confirm the omissions are
intentional (spin-off-only methods from out-of-scope versions).

`IN_SCOPE_VERSIONS` filters encounters to the set of PokéAPI version names
that match game IDs in `data/games.json`. Game IDs and version names are
the same string, so this is just a membership check.

## Upstream data sources

- **PokéAPI** — <https://pokeapi.co>. Species, abilities, forms, partial
  encounter coverage. GitHub mirror at PokeAPI/api-data for bulk pulls.
- **Serebii / Bulbapedia** — prose encounter and availability info;
  future scraper targets. Use the same UA pattern as the PokéAPI scraper.
- **pret decomps** (pokeemerald, pokeheartgold, pokeruby, pokecrystal) —
  authoritative encounter tables for older generations, MIT-ish license.
  Parse data files rather than scraping rendered wikis when possible.

## Evolution mode: game-scope mapping

`--mode evolutions` walks each species's `evolution_chain` and emits
`method=evolution` / `method=trade` Source rows for evolved species. Two
design decisions worth knowing:

- **Game scope comes from `species.pokedex_numbers`, not `pokemon.game_indices`**.
  PokéAPI's `game_indices` is only populated through Gen 5, so it can't
  tell us which Gen 6+ games an evolution is available in. `POKEDEX_TO_GAMES`
  maps each in-scope regional dex (`kanto`, `galar`, `hisui`, `paldea`,
  `lumiose-city`, etc.) to the game IDs that share it. Pokédexes whose
  only games are out of scope (RS/E `hoenn`, DP `original-sinnoh`,
  B/W `original-unova`) are intentionally omitted. So is `champions`
  (cross-gen achievement list with unclear semantics) and `national`
  (too broad).
- **Attribution is to the evolved species's default form** (`form_id ==
  species_id`). Branched regional evolutions (yamask-galar → runerigus,
  exeggcute → exeggutor-alola) emit a default-form row that loses the
  pre-evolution provenance. This under-specifies but doesn't mis-specify;
  refining to per-branch rows is a future pass.

Method mapping: any PokéAPI trigger name of `trade` maps to `Method.TRADE`,
everything else (`level-up`, `use-item`, `shed`, `agile-style-move`,
`three-critical-hits`, etc.) maps to `Method.EVOLUTION`. The raw trigger
name is preserved in `method_details` so the app can distinguish
stone-use from level-up if it wants.

## Known gaps in PokéAPI encounter coverage

These are PokéAPI limitations, not scraper bugs — they need separate
passes (hand-curated event rows, branched regional-evolution pass, or
future Bulbapedia/pret scrapes):

- Breeding-only mons (e.g. Cleffa, Smoochum in Gen 2)
- Fossils (partial gift/static coverage at best)
- Gen 8/9 encounter data is weak — expect gaps
- Event-only forms (hand-curated with `method=event`)
- Game-locked starter forms (hand-curated with `method=gift`)
- Branched regional-form evolutions (currently attributed to default form)
