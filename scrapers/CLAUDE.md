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

`method_details` is a **short categorical slug**, never prose. For evolution
rows it's the raw PokéAPI trigger slug (`level-up`, `use-item`, `trade`,
`shed`, `spin`, `three-critical-hits`, `take-damage`, `recoil-damage`,
`agile-style-move`, `strong-style-move`, `tower-of-darkness`,
`tower-of-waters`, `gimmmighoul-coins`, `three-defeated-bisharp`,
`use-move`, `other`). For non-evolution rows it's the acquisition subtype
slug (`walk`, `surf`, `overworld`, `mass-outbreak`, `space-time-distortion`,
`sos-encounter`, `horde`, `old-rod` / `good-rod` / `super-rod` (or a
comma-joined subset), `tera-raid`, `max-raid`, `gmax`, `dynamax-adventure`,
`N-star`, `only-one`, `pokeflute`, `squirt-bottle`, `wailmer-pail`,
`devon-scope`, `island-scan`, `roaming`, `game-corner`). Rule 7 still
applies: omit when it equals the method enum.

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

- **Breeding-only mons** (Gen 2 babies: Pichu, Cleffa, Igglybuff, Smoochum,
  Elekid, Magby, etc.) — covered with `method=breeding` rows emitted by
  [`scripts/seed_manual_sources.py`](../scripts/seed_manual_sources.py).
  The script seeds `(baby, game)` pairs where the game's generation ≥
  the baby's intro generation and the parent species is obtainable in
  that game; the structured `from_form` field points to the parent
  species. Re-run after a fresh scrape.
- **Game Corner prize Pokémon** (Abra/Dratini/Porygon in RBY/GSC) —
  covered with `method=purchase` / `method_details=game-corner` rows
  emitted by [`scripts/seed_manual_sources.py`](../scripts/seed_manual_sources.py)
  alongside the breeding seeds. Species list was originally compiled from
  Bulbapedia's `Celadon_Game_Corner` and `Goldenrod_Game_Corner` Prize
  Corner sections. Mauville (RSE) and Veilstone (DPPt/Platinum) are
  excluded — their prize lists are TMs/items only.
- **Fossils** — Gen 8/9 covered via PokéAPI `fossil-revive` + Bulbapedia
  raid rows. Pre-Gen-8 fossil revivals (GSC Kabuto / Omanyte at the
  Ruins of Alph; ORAS Anorith / Lileep via Mirage Spots + Devon Corp)
  are covered by tier-12 / tier-13 manual seeds in `seed_manual_sources.py`.
- **Event-only forms** (Zarude-Dada, Magearna-Original) — covered with
  `method=event` rows. Ash-Greninja (`greninja-battle-bond`) was pruned
  in tier 8 because HOME does not acknowledge it as a distinct form.
- **Game-locked starter forms** — pre-tier-8 `pikachu-starter` /
  `eevee-starter` were sourced as `method=gift`, but both were pruned
  in tier 8 once Bulbapedia confirmed Let's Go partner Pokémon cannot
  be deposited in HOME (permanently save-bound). Both form ids live in
  `SKIP_FORM_IDS_HOME_UNREACHABLE` now.
- **Pre-Gen-8 regional-dex completeness** — Tier 12 closed Gen 1 / 2 /
  Gen 6 XY / SM / USUM / LGPE via `seed_manual_sources.py`; tier 13
  closed ORAS (47 species × 2 versions via manual wild-encounter +
  trade + fossil + evolution rows). Tier 14 closed the 189 cosmetic
  variants (Vivillon patterns, Alcremie, Unown letters, etc.). Extending
  `GEN_8_9_GAME_IDS` in `bulbapedia.py` to include pre-Gen-8 games is
  a known future-infrastructure option that would let re-scrapes
  supersede those manual seeds.

Gen 8/9 encounter coverage and branched regional-form evolution attribution
are both addressed by `scrapers/bulbapedia.py` — see the Bulbapedia scraper
section above. Older-gen precision (Gen 2–5 exact route lists) would benefit
from a pret-decomps pass (MIT-licensed decompilations at
<https://github.com/pret>) but is not currently tracked as a blocking gap.

## Coverage-audit `HOME_TRANSFER_ONLY_DEX`

`scripts/coverage_audit.py` holds a `HOME_TRANSFER_ONLY_DEX` dict keyed
by `game_id` listing species that PokéAPI's aggregated regional dex
includes for that game but which have no legitimate in-game
acquisition — registered only via HOME transfer. Entries are subtracted
from the "expected" set before the regional-dex audit counts missing
species, so the audit reports genuine in-game gaps rather than dex
metadata artefacts. Populate it only when Bulbapedia confirms a species
has no wild / raid / trade / event / gift path in the named game.

## Game scope

`data/games.json` covers Gen 1–2 and Gen 6–9 plus HOME/Bank/GO/Transporter.
Gen 3–5 mainline games (RSE, FRLG, DPPt, HGSS, BW, B2W2) are intentionally
out-of-scope: scrapers should filter emitted rows to `game_ids` present in
`games.json` and log skipped out-of-scope rows rather than error. This came
up during the Game Corner pass — FRLG/HGSS prize data is on Bulbapedia but
was deliberately skipped.
