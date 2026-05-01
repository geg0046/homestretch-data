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
    Kyurem-Black / -White, Calyrex-Ice / -Shadow, Necrozma-Dawn / -Dusk / -Ultra.
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

- **A `<form_id>-female` entry is synthesised per non-female form whose
  variety has a distinct `pokemon.sprites.front_female`** in PokéAPI,
  at the end of `build_forms_for_species`. Species-level
  `has_gender_differences` is the outer guard; the per-variety
  `front_female` is the authoritative gate, because the species-level
  flag is true even for varieties without observed dimorphism (Alolan
  Rattata/Raticate/Raichu, Paldean Wooper) and would otherwise produce
  female forms PokéAPI has no artwork for. Regional variants with real
  dimorphism (e.g. `sneasel-hisui-female`) still synthesise via the
  same path. PokéAPI exposes 7 species as native form entries
  (Frillish, Jellicent, Pyroar, Meowstic, Indeedee, Basculegion,
  Oinkologne — picked up by the existing `form_name in {"female",
  "male"}` branch in `categorize`); the remaining ~95 species with
  cosmetic-only dimorphism (Venusaur, Pikachu, Hippowdon, etc.) are
  synthesised.
- **One gender-difference entry per form**, not one per colour × gender.
  The default form is implicitly the male sprite; we don't emit a
  redundant `<id>-male`. Use `SKIP_FORM_IDS_HOME_UNREACHABLE` to suppress
  any synthesised female that fails the HOME-deposit test.
- **Tagged ONLY `gender-difference`**, never also `functional`, even when
  the forms have different abilities or stats. The semantic meaning is
  gender; keep category lists short.
- **Source rows for female forms are mirrored** from the default-form
  rows by `scripts/seed_manual_sources.py`, with `gender=female` set.
  The pair list there is derived from `data/forms.json` (every form
  whose id ends in `-female` paired with its non-suffixed counterpart),
  so it stays in sync with the scraper's synthesis output without a
  hand-maintained list.

## Sprite URLs

Every form row carries a required `sprite_url` pointing at PokéAPI's
GitHub sprite mirror (`raw.githubusercontent.com/PokeAPI/sprites/...`).
`build_forms_for_species` picks the URL with `_pick_sprite_url` (variant
forms: `pokemon-form/{name}.sprites.front_default` → variety-level
fallback → species-default fallback) and `_female_sprite_url`
(synthesised `-female` rows: `front_female` on the base variety →
species default's `front_female` → `front_default` fallbacks).

The scraper raises if every fallback in the chain is null — silent
missing sprites would defeat the consumer-side simplification this field
exists to enable. Format is enforced in two places: Pydantic
(`SpriteUrl` annotation, `^https://`) and `scripts/validate.py`
(prefix/suffix lock to the GitHub mirror). HEAD-reachability is checked
out-of-band via `scripts/validate_sprite_urls.py`; that's not in
pre-commit because it's network-bound and slow.

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

`VACUOUS_ENCOUNTER_METHOD_NAMES` (mirror of `_UNIVERSALLY_VACUOUS_DETAILS`
in `method_details.py`) is a rule-7 generalization: slugs that duplicate
the method semantics are dropped at collapse time. Currently `only-one` —
every static-encounter / gift / fossil-revive row is a singleton by
definition, so the slug adds no discriminator beyond the method itself.
Mechanic-specific subtypes (`pokeflute`, `island-scan`, `devon-scope`,
`squirt-bottle`) do constrain *how* you trigger the encounter and stay
as `method_details`.

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
     Each synthesized row gets `method_details`, `item` (if applicable),
     and `from_form` filled from three fallback sources in order:
     (a) Bulbapedia evobox trigger text run through `_detect_item_slug`
     / `_classify_trigger`; (b) `_REGIONAL_TRIGGER_OVERRIDES` — a small
     hand-authored table for forms whose evobox leaves `evoN` empty
     (most Alolan/Galarian/Hisuian variants do); (c) left unset.
     `from_form` is captured whenever either side of an evobox edge is
     regional, so default → regional evolutions (marowak-alola from
     cubone) also get provenance, not just regional → regional chains.
  3. **Per-game gating via prose + evobox item detection.** Prose phrases
     like "...cannot evolve in [[Pokémon Scarlet and Violet]]..." remove
     matching rows (regex stops at clause boundaries so contrast phrases
     don't over-match). Evobox trigger text naming a specific item from
     `_ITEM_NAME_TO_SLUG` fills the structured `item` field so
     `use-item` + `item=black-augurite` for Kleavor/LA. The dict covers
     two populations: (a) Gen 8/9 items PokéAPI lumps under generic
     `use-item` (Black Augurite, Peat Block, Linking Cord,
     Auspicious/Malicious Armor, Galarica Cuff/Wreath); (b) common
     evolution stones (Thunder/Fire/Water/Leaf/Ice/Sun/Moon/Dusk/Dawn/
     Shiny) needed by Pass 2 synthesis where there's no PokéAPI row
     to inherit from. Pass 1 refinement only fills items for (a) since
     PokéAPI already supplies stone names for default-form rows.
  
  Merge semantics for evolutions mode diverge from sources mode:
  existing rows may be **rewritten or removed**, and new rows may be
  **added** for regional forms. Re-runs are idempotent (annotation
  suffixes are not re-appended if already present).
- `--mode locations` — backfill the `location` field on existing
  static-encounter, gift, wild-encounter, fishing, and raid rows. Walks species
  pages, extracts a location slug from each targeted segment, then
  applies the resulting slug(s) to matching rows in
  `data/sources.json`. Rows that already have a `location` are left
  untouched (re-runs are idempotent). A 40-character slug length
  gate drops any extraction that's really condition prose rather
  than a named place, so the failure mode is "row keeps
  `location=None`," never "row gets a garbage slug." Extraction
  strategy is method-aware, set by `prefer_preposition` on
  `extract_area_location`:
    - **Static-encounter** (``prefer_preposition=False``): first
      wikilink wins. Static segments open with the place
      (``[[Cerulean Cave]] ([[...|Only one]])``).
    - **Gift** (``prefer_preposition=True``): the location follows an
      ``in`` / ``at`` / ``on`` preposition — ``Received from
      [[Bill]] in [[Goldenrod City]]`` picks ``goldenrod-city``, not
      ``bill``. Falls back to first-wikilink for gifts written
      without a preposition.
    - **Wild-encounter**: uses the multi-location extractor
      `extract_area_locations()` (plural), which walks **every**
      wikilink and `{{rt|}}` / `{{rtn|}}` / `{{FB|}}` template in
      the segment. A single Availability ``area=`` typically
      enumerates many places (``[[Route]]s {{rtn|201|Sinnoh}},
      {{rtn|202|Sinnoh}}, [[Lake Verity]]``), so emitting only the
      first link drops most of the data. `_GENERIC_LOCATION_SLUGS`
      filters out common-noun first wikilinks (`[[Route]]`,
      `[[Cave]]`, region names, Pokémon types, mechanic names like
      `[[National Pokédex]]` / `[[SOS Battle]]` /
      `[[Pokémon Breeding|Breed]]`, fishing-rod links
      `[[Old Rod]]` / `[[Good Rod]]` / `[[Super Rod]]`). Wikilinks
      with a `(type)` / `(move)` / `(ability)` / `(species)`
      disambiguator suffix are dropped whole — `[[Fire
      (type)|Fire]]`-style links describe element coverage, not
      location.
    - **Fishing**: same multi-location extractor as wild — fishing
      Availability segments enumerate routes the same way
      (`[[Routes]] {{rtn|12|Kanto}}, {{rtn|13|Kanto}} ([[Old
      Rod]])`). The rod link is filtered by the generic-skip set
      above, leaving the routes as the only location slugs.
    - **Raid**: same multi-location extractor as wild and fishing.
      SwSh raid Availability segments enumerate dens/zones
      (`[[Bridge Field/Dens|Bridge Field]], [[Lake of Outrage/Dens|
      Lake of Outrage]] ([[Max Raid Battle]])`); raid-mechanic
      links (`[[Max Raid Battle]]` / `[[Pokémon Den]]` /
      `[[Dynamax Adventure]]`) are filtered by the generic-skip
      set, leaving the den/zone slugs. `[[Max Lair]]` is the one
      raid-mechanic-adjacent wikilink that *is* a real location
      slug (Crown Tundra dynamax-adventure dungeon) and is left
      out of the skip set on purpose.
  All five paths scrub the "Only one" event-list wikilink,
  `<small>`/`<sup>` footnote metadata, `{{tt|...}}` tooltips, and
  trailing ``after X`` / ``during Y`` condition clauses.

  **Update strategy diverges by method.** Static and gift are
  filled **in place** — these mechanics are singletons (one cave,
  one NPC), so the first slug is the location. Wild-encounter,
  fishing, and raid rows are **row-split**: when Bulbapedia
  produces N distinct location slugs for the matching key, the
  original null-location row is replaced by N clones, each carrying
  a distinct `location` slug. `location` participates in
  `SOURCE_KEY_FIELDS`, so the post-split row set remains uniquely
  keyed and `validate.py`'s unique-source-key check still passes.
  Single-slug wild/fishing/raid rows fall through to the in-place
  path.

  **Fishing and wild-encounter matching is set intersection, not
  strict equality.** PokéAPI emits one fishing row per rod tier
  (`old-rod` / `good-rod` / `super-rod` / comma-joined combos) and
  multi-mode wild rows (`walk, yellow-flowers`, `surf, walk`,
  `bubbling-spots, walk`). Bulbapedia segments typically annotate only
  the rod / encounter-mode actually present at the location. The
  consumption loop uses set-intersection match: a Bulbapedia segment
  for `[[Old Rod]]` applies to any existing row whose `method_details`
  rod-set contains `old-rod`; a Bulbapedia segment normalized to
  `walk` applies to any existing row whose mode-set contains `walk`
  (`walk`, `walk, yellow-flowers`, `surf, walk`, etc.). Existing rows
  with `method_details=None` accept any Bulbapedia segment. See
  `_fishing_slugs_for_row` and `_wild_slugs_for_row`.

  Wild segments use `_normalize_wild_encounter_set` (locations-mode
  only) instead of `_normalize_wild_encounter` (sources-mode). The
  set version walks every `_WILD_ENCOUNTER_PATTERNS` match and
  returns a canonical-order comma-joined slug; the single version
  returns the first match. Splitting the two preserves the merge-key
  shape of `--mode sources`.

  Targeted rows are whitelisted by `_LOCATION_TARGET_DETAILS`:
  static subtypes with a single discrete spot (None / pokeflute /
  squirt-bottle / devon-scope), gift subtypes (None / gift-egg),
  fishing rows with any of the seven non-empty rod-set slugs
  `_normalize_fishing` can emit plus None, and raid rows with one of
  `max-raid` / `gmax` / `dynamax-adventure` for the SwSh dens that
  Bulbapedia enumerates by zone. `tera-raid` (SV) is intentionally
  excluded from raid scope: Bulbapedia annotates SV tera raids only
  by star tier (`{{DL|List of N★ Tera Raid Battles (Paldea)|...|N★}}`)
  with no per-zone enumeration, so there's no location slug to
  extract; a future tier can hand-fill the 1.1k tera-raid rows
  uniformly with `paldea` or similar if the planner UX wants it.
  Wild-encounter has its own dispatch in the iter function: it
  bypasses the `target_details` strict check and emits a mode-set
  slug for every classified segment, letting the consumption loop's
  intersection match be the correctness gate. Species-name SOS slugs
  (`swellow` / `venomoth`), region-name slugs (`kanto` / `hoenn`),
  and SwSh flower-color habitat names aren't in
  `_WILD_ENCOUNTER_PATTERNS`, so wild rows tagged solely with those
  details have no Bulbapedia counterpart and stay null —
  intentionally deferred. Island-scan (SM/USUM) is excluded because
  its location is a (species, day, island) triple that doesn't
  collapse to a slug.

  **Final dedup pass.** `_drop_redundant_none_detail_rows` runs at the
  end of `--mode locations`, after row-splitting and location backfill.
  PokéAPI sources mode emits rich rows like `(kadabra, blue,
  wild-encounter, walk)` while Bulbapedia sources mode emits a parallel
  `method_details=None` row whenever the area text classifies as
  wild / fishing / static / gift but doesn't carry a mode keyword. Both
  rows survive the source-key merge because `method_details` is part of
  the key, and the permissive locations-mode matcher fills the same
  location onto both. The dedup pass groups rows on every
  `SOURCE_KEY_FIELDS` value except `method_details` and drops the
  `None`-detail row in any group that also contains a rich-detail
  sibling — the rich row is strictly more informative. Limited to
  `wild-encounter` / `fishing` / `static-encounter` / `gift`; evolution
  / trade / raid / breeding / event / purchase keep all their rows.

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

**Regional-variant detail filtering.** PokéAPI's `evolution_chain`
endpoint folds every path into the default child species's
`evolution_details` list, including paths that actually belong to a
regional variant of the parent (e.g. rattata-alola → raticate-alola
needs `time_of_day=night`, but the detail is attributed to `raticate`).
`REGIONAL_VARIANT_DETAILS` in `scrapers/evolution_details.py` is the
curated table of `(default_form_id → [(signature, owner_form_id)])`
mappings that drop these details from the default form's emitted rows;
`detail_belongs_to_regional_variant` is the predicate the PokéAPI
scraper calls in `build_evolution_sources_for_chain`. The owner regional
form picks up the same condition fields via `_REGIONAL_TRIGGER_OVERRIDES`
in `bulbapedia.py` (which now carries `time_of_day` / `min_happiness` /
… alongside `method_details` and `item`). When adding a new regional
variant whose evolution criterion strictly differs from its default
sibling, edit both tables together.

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
