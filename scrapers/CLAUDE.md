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
- **`SKIP_FORM_IDS_HOME_UNREACHABLE`** — forms that can be obtained in
  their origin game but do not survive a HOME deposit as that specific
  form (the "HOME-deposit test" in the root CLAUDE.md). Grouped by
  failure mode:
  - **Held-item form changes** that revert when HOME strips the item on
    deposit: Arceus plates (17), Silvally memories (17), Ogerpon masks
    (3), Genesect drives (4), Giratina / Dialga / Palkia Origin.
  - **Fused legendaries** explicitly on Serebii's non-depositable list:
    Kyurem-Black / -White, Calyrex-Ice / -Shadow, Necrozma-Dawn / -Dusk.
  - **Let's Go partner Pokémon**: `pikachu-starter`, `eevee-starter`.
  - **Ash-Greninja** (`greninja-battle-bond`): not acknowledged by the
    HOME Pokémon Guidebook.
  - **Spiky-eared Pichu**, ORAS cosplay Pikachu family, all 8 cap
    Pikachus: pre-HOME Bank-block cases documented by Bulbapedia.
  Authoritative reference: <https://www.serebii.net/pokemonhome/nondepositablepokemon.shtml>.
  The literal `frozenset` in `pokeapi.py` is the canonical enumeration
  — don't duplicate it here.
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
- **Serebii** — <https://www.serebii.net>. Used for HOME-compatibility
  decisions (the non-depositable list at
  `/pokemonhome/nondepositablepokemon.shtml` is the authoritative source
  for the `SKIP_FORM_IDS_HOME_UNREACHABLE` set). Manual cross-checks
  only — no scraper.

## Bulbapedia scraper

`scrapers/bulbapedia.py` supplements PokéAPI where its coverage is weakest.
Two modes, both reading Bulbapedia's `api.php?action=parse&prop=wikitext`
endpoint (no HTML scraping) at 1 req/sec, cached under `.cache/bulbapedia/`:

- `--mode sources` — authoritative encounter source for all 24 in-scope
  mainline games (RBY, GSC, XY, ORAS, SM, USUM, LGPE, SwSh, BDSP, PLA,
  SV, LZA). Parses `==Game locations==` `{{Availability/EntryN}}` and
  `{{Availability/EntryN/None}}` templates for each species. Emissions
  are filtered to `IN_SCOPE_GAME_IDS` (mirror of
  `pokeapi.py::IN_SCOPE_VERSIONS`); out-of-scope versions (RSE, FRLG,
  DPPt, HGSS, BW, B2W2) are dropped after parsing. Method labels are
  inferred by regex over the area text (`_METHOD_PATTERNS`). For regular
  entries, unmatched areas default to `wild-encounter`; for `/None`
  entries (semantically "no native wild encounter") only explicit
  matches are emitted — covers post-patch trade additions
  (`[[Trade]]<sup>Version 2.0.1+</sup>`) and raid search paths while
  suppressing false wild-encounter fallbacks. Merge is **additive**:
  rows are keyed on `(form_id, game_id, method, method_details, …)` and
  existing entries win on conflict, so a re-run never rewrites
  PokéAPI-sourced or manually-seeded rows.
- `--mode evolutions` — refines the `method=evolution` rows PokéAPI
  already emits. Three narrow refinements only; the PokéAPI trigger
  catalog is otherwise authoritative:
  1. **Regional pre-evo provenance.** When Bulbapedia's `==Evolution==`
     evobox shows a regional variant on the pre-evo side (e.g.
     `no1=0562Yamask-Galar` for Runerigus, `form2=Galarian Form` for
     Mr. Rime's 3-stage chain), the evolved species's existing evolution
     rows get the structured `from_form` field populated with the
     regional form id (e.g. `from_form=yamask-galar`). Evobox templates
     `Evobox-N` and `Evobox/Nbranch*` are both parsed.
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
     Auspicious/Malicious Armor) fills the structured `item` field so
     `use-item` + `item=black-augurite` for Kleavor/LA.
  
  Merge semantics for evolutions mode diverge from sources mode:
  existing rows may be **rewritten or removed**, and new rows may be
  **added** for regional forms. Re-runs are idempotent (annotation
  suffixes are not re-appended if already present).
- `--mode locations` — backfill the `location` field on existing
  static-encounter rows. Walks species pages, extracts a location slug
  from each static segment (first wikilink display text, or `{{rt|N|R}}`
  /`{{FB|R|P}}` shortcuts, with `<small>`-footnote metadata scrubbed),
  then updates matching rows in `data/sources.json` **in place**. Rows
  that already have a `location` are left untouched. A 40-character
  slug length gate drops any extraction that's really condition prose
  rather than a named place, so the failure mode is "row keeps
  `location=None`," never "row gets a garbage slug." Island-scan
  (SM/USUM) rows are explicitly excluded — their location is a
  (species, day, island) triple that doesn't collapse to a single slug.
  In-place update (not merge) is required because `location` is part
  of `SOURCE_KEY_FIELDS`, so additive merge would split the existing
  row instead of filling it.

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
  refines this afterwards — populating the structured `from_form` field
  with the regional pre-evolution id, and filling in the regional-variant
  rows PokéAPI skipped entirely.

Method mapping: any PokéAPI trigger name of `trade` maps to `Method.TRADE`,
everything else (`level-up`, `use-item`, `shed`, `agile-style-move`,
`three-critical-hits`, etc.) maps to `Method.EVOLUTION`. The raw trigger
name is preserved in `method_details` so the app can distinguish
stone-use from level-up if it wants.

## `method_details` and structured conditions

`method_details` is a **short categorical slug**, never prose. For
evolution rows it's the raw PokéAPI trigger slug (pass-through from
`evolution_detail.trigger.name`). For non-evolution rows it's the
acquisition subtype — the canonical set lives in
`ENCOUNTER_METHOD_TO_METHOD` in `pokeapi.py` (wild subtypes) and in
the seed rows in `scripts/seed_manual_sources.py` (raid subtypes,
`only-one`, `game-corner`, etc.). Root CLAUDE.md rule 7 applies: omit
when the slug equals the method enum value.

**One row per PokéAPI `evolution_detail`.** Alternative paths become
alternative rows, not comma-joined. Crabominable in SM/USUM emits two
rows per game: `level-up` + `location=mount-lanakila` and `use-item` +
`item=ice-stone`.

**Specifics live in structured fields** — `item`, `held_item`, `location`,
`known_move`, `known_move_type`, `trade_species`, `party_species`,
`party_type`, `from_form`, `time_of_day`, `min_happiness`, `min_affection`,
`min_beauty`, `gender`, `relative_physical_stats`, `needs_overworld_rain`,
`turn_upside_down`, `needs_multiplayer`. All are optional and default to
`None` / `False`; `Source.model_dump(exclude_none=True, exclude_defaults=True)`
keeps JSON clean.

**Per-game gating** for evolution rows beyond species-level scope:
`LOCATION_TO_GAMES` and `ITEM_TO_GAMES` in `scrapers/evolution_details.py`
intersect with the species scope when `location` / `item` / `held_item`
is set. Location/item slugs not listed there fall through unchanged. The
Bulbapedia `==Evolution==` "cannot evolve in [[X]]" prose filter remains
the fallback for cases PokéAPI can't express.

**Normalization** for Bulbapedia-sourced prose lives in
`scrapers/method_details.py::normalize_method_details`. It strips wiki
markup (`{{rt|...}}`, `{{tt|...}}`, `[[...|x]]`, `<br>`, `'''`, `&nbsp;`,
etc.) and applies per-method recognizers. Unrecognized prose drops to
`None` rather than leaking — `game_id` already scopes availability.

## Known gaps in PokéAPI encounter coverage

These are PokéAPI limitations, not scraper bugs. Status as of the most
recent scrape:

All gaps below are covered by [`scripts/seed_manual_sources.py`](../scripts/seed_manual_sources.py).
Re-run the script after any fresh scrape to re-apply manual rows.

- **Breeding-only mons** (Gen 2 babies: Pichu, Cleffa, Igglybuff, etc.)
  — `method=breeding` + `from_form=<parent>`. Seeded per `(baby, game)`
  where the game's generation ≥ the baby's intro generation and the
  parent is obtainable.
- **Game Corner prize Pokémon** (Abra/Dratini/Porygon in RBY/GSC) —
  `method=purchase`, `method_details=game-corner`.
- **Fossils** — Gen 8/9 scraped; pre-Gen-8 manual (GSC Kabuto/Omanyte
  at the Ruins of Alph, ORAS Anorith/Lileep via Mirage Spots).
- **Event-only forms** — `method=event` rows for Zarude-Dada and
  Magearna-Original (both tagged `event-only` in `categories`).
  Ash-Greninja was pruned in tier 8; see the HOME-deposit section above.
- **Pre-Gen-8 regional-dex completeness** — tier-12/13 manual seeds
  closed the audit gaps (RBY/GSC/XY/ORAS/SM/USUM/LGPE); tier 14 seeded
  the 189 cosmetic variants; tier 15 widened `--mode sources` scope so
  future scrapes auto-populate pre-Gen-8 Bulbapedia data on top of the
  manual rows (manual rows stay as a safety net per the additive merge).

## Regional-dex audit: expected set

`scripts/coverage_audit.py` uses PokéAPI's `/api/v2/pokedex/<name>/`
endpoint verbatim as the "expected species" denominator per game.
There is no manual override: every species Bulbapedia confirms has an
in-game path (wild, raid, trade, event, gift, or a version-exclusive
cross-trade from the paired cartridge) must have a matching source row
in `data/sources.json`. `--mode sources` parses `Entry*/None` templates
for trade/raid areas, so version-exclusive trades added in post-launch
patches (e.g. Vulpix in SV 2.0.1+) produce a `method=trade` row
automatically; species truly HOME-only (no cross-version path at all)
do not exist in any current in-scope game, so no override is needed.

## Game scope

`data/games.json` covers Gen 1–2 and Gen 6–9 plus HOME/Bank/GO/Transporter.
Gen 3–5 mainline games (RSE, FRLG, DPPt, HGSS, BW, B2W2) are intentionally
out-of-scope: scrapers should filter emitted rows to `game_ids` present in
`games.json` and log skipped out-of-scope rows rather than error. This came
up during the Game Corner pass — FRLG/HGSS prize data is on Bulbapedia but
was deliberately skipped.
