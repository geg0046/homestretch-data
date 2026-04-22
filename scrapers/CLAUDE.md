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
- **Bulbapedia** — <https://bulbapedia.bulbagarden.net>. Structured wikitext
  accessed via MediaWiki `api.php`. Covers the Gen 8/9 encounter data
  PokéAPI omits and the branched-regional-evolution provenance PokéAPI
  can't express. See the Bulbapedia section below for merge semantics.
- **Serebii** — prose encounter and availability info; a potential future
  fallback. Use the same UA pattern if/when added.
- **pret decomps** (pokeemerald, pokeheartgold, pokeruby, pokecrystal) —
  authoritative encounter tables for older generations, MIT-ish license.
  Parse data files rather than scraping rendered wikis when possible.

## Bulbapedia scraper

`scrapers/bulbapedia.py` supplements PokéAPI where its coverage is weakest.
Two modes, both reading Bulbapedia's `api.php?action=parse&prop=wikitext`
endpoint (no HTML scraping) at 1 req/sec, cached under `.cache/bulbapedia/`:

- `--mode sources` — closes the Gen 8/9 encounter gap. Parses
  `==Game locations==` `{{Availability/EntryN}}` templates for each
  species. Emissions are filtered to the eight Gen 8/9 game IDs
  (`GEN_8_9_GAME_IDS`); pre-Gen-8 rows stay PokéAPI-sourced. Method labels
  are inferred by regex over the area text (`_METHOD_PATTERNS`) — unmatched
  areas default to `wild-encounter`. Merge is **additive**: rows are
  keyed on `(form_id, game_id, method, method_details)` and existing
  entries win on conflict, so a re-run never rewrites PokéAPI-sourced rows.
- `--mode evolutions` — refines the `method=evolution` rows PokéAPI
  already emits. Three narrow refinements only; the PokéAPI trigger
  catalog is otherwise authoritative:
  1. **Regional pre-evo provenance.** When Bulbapedia's `==Evolution==`
     evobox shows a regional variant on the pre-evo side (e.g.
     `no1=0562Yamask-Galar` for Runerigus, `form2=Galarian Form` for
     Mr. Rime's 3-stage chain), the evolved species's existing evolution
     rows get a `from <regional-form-id>` suffix appended to their
     `method_details` (e.g. `take-damage` → `take-damage from yamask-galar`).
     Evobox templates `Evobox-N` and `Evobox/Nbranch*` are both parsed.
  2. **New rows for regional-form species PokéAPI skipped.** PokéAPI
     attributes every evolution row to the default form; Alolan Raichu,
     Galarian Slowking, Hisuian Typhlosion, etc. therefore have zero
     evolution rows in `data/sources.json`. Evolutions mode emits fresh
     rows for these, scoped to the union of `_REGIONAL_GAMES[region]`
     and any games where the form already appears in non-evolution rows.
  3. **Per-game gating via prose + evobox item detection.** Prose phrases
     like "...cannot evolve in [[Pokémon Scarlet and Violet]]..." remove
     matching rows (regex stops at clause boundaries so contrast phrases
     don't over-match). Evobox trigger text naming a specific item from
     `_ITEM_NAME_TO_SLUG` (Black Augurite, Peat Block, Linking Cord,
     Auspicious/Malicious Armor) appends the item slug to `method_details`
     so `use-item` becomes `use-item black-augurite` for Kleavor/LA.
  
  Merge semantics for evolutions mode diverge from sources mode:
  existing rows may be **rewritten or removed**, and new rows may be
  **added** for regional forms. Re-runs are idempotent (annotation
  suffixes are not re-appended if already present).

`fetch_wikitext` follows `#redirect` pages once — Bulbapedia canonicalises
apostrophe variants this way (Sirfetch'd/Sirfetch’d), so the shared cache
stays effective across both spellings.

UA: `HomeStretch/0.1 (+https://github.com/geg0046/homestretch-data;
contact: homestretchapp@outlook.com)` — same as the PokéAPI scraper, using
the project email.

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
  pre-evolution provenance. The Bulbapedia `--mode evolutions` pass
  refines this afterwards — appending `from <regional-form-id>` to
  `method_details` and filling in the regional-variant rows PokéAPI
  skipped entirely.

Method mapping: any PokéAPI trigger name of `trade` maps to `Method.TRADE`,
everything else (`level-up`, `use-item`, `shed`, `agile-style-move`,
`three-critical-hits`, etc.) maps to `Method.EVOLUTION`. The raw trigger
name is preserved in `method_details` so the app can distinguish
stone-use from level-up if it wants.

## Known gaps in PokéAPI encounter coverage

These are PokéAPI limitations, not scraper bugs — they need separate
passes (Bulbapedia for Gen 8/9 and branched regional evolutions, curated
rows for event-only forms and game-locked starters, pret decomps for
older-gen precision):

- Breeding-only mons (e.g. Cleffa, Smoochum in Gen 2)
- Fossils (partial gift/static coverage at best)
- Event-only forms (curated with `method=event`)
- Game-locked starter forms (curated with `method=gift`)

Gen 8/9 encounter coverage and branched regional-form evolution
attribution are both addressed by `scrapers/bulbapedia.py` — see the
Bulbapedia scraper section above.
