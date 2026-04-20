"""Scrape Pokémon species and form data from PokéAPI into data/forms.json.

Usage:
    uv run python scrapers/pokeapi.py --max-dex 151

Respects a 1 req/sec rate limit, caches responses under .cache/pokeapi/, and
merges results into data/forms.json (existing entries are preserved by id).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from pydantic import TypeAdapter

from homestretch_data.models import Form, FormCategory

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
CACHE_DIR = REPO_ROOT / ".cache" / "pokeapi"
FORMS_PATH = DATA_DIR / "forms.json"
BASE_URL = "https://pokeapi.co/api/v2"
USER_AGENT = (
    "HomeStretch/0.1 (+https://github.com/geg0046/homestretch-data; "
    "contact: homestretchapp@outlook.com)"
)
MIN_REQUEST_INTERVAL = 1.0

GENERATION_MAP = {
    "generation-i": 1,
    "generation-ii": 2,
    "generation-iii": 3,
    "generation-iv": 4,
    "generation-v": 5,
    "generation-vi": 6,
    "generation-vii": 7,
    "generation-viii": 8,
    "generation-ix": 9,
}

REGIONAL_SUFFIXES = {"alola", "galar", "hisui", "paldea"}

# Species whose non-default forms are purely visual variants (no stat/ability/
# type differences). PokéAPI has no programmatic signal for this; maintain by
# hand.
COSMETIC_SPECIES = {
    "alcremie",
    "deerling",
    "flabebe",
    "floette",
    "florges",
    "furfrou",
    "gastrodon",
    "maushold",
    "minior",
    "polteageist",
    "poltchageist",
    "sawsbuck",
    "scatterbug",
    "shellos",
    "sinistcha",
    "sinistea",
    "spewpa",
    "tatsugiri",
    "unown",
    "vivillon",
}

# Species whose non-default forms differ mechanically (stats, abilities, types,
# or signature moves).
FUNCTIONAL_SPECIES = {
    "arceus",
    "basculin",
    "burmy",
    "calyrex",
    "deoxys",
    "dialga",
    "dudunsparce",
    "enamorus",
    "genesect",
    "gimmighoul",
    "giratina",
    "gourgeist",
    "hoopa",
    "keldeo",
    "kyurem",
    "landorus",
    "lycanroc",
    "mothim",
    "necrozma",
    "ogerpon",
    "oricorio",
    "palkia",
    "pumpkaboo",
    "rockruff",
    "rotom",
    "shaymin",
    "silvally",
    "squawkabilly",
    "thundurus",
    "tornadus",
    "toxtricity",
    "ursaluna",
    "urshifu",
    "wormadam",
    "zygarde",
}

# Forms PokéAPI exposes that cannot be stored separately in a PC box and
# therefore aren't living-dex forms. Koraidon/Miraidon mount modes are
# visual ride transformations of the default; eternatus-eternamax is a
# story-only boss form never given to the player.
SKIP_FORM_IDS = {
    "eternatus-eternamax",
    "koraidon-gliding-build",
    "koraidon-limited-build",
    "koraidon-sprinting-build",
    "koraidon-swimming-build",
    "miraidon-aquatic-mode",
    "miraidon-drive-mode",
    "miraidon-glide-mode",
    "miraidon-low-power-mode",
}

# Forms that PokéAPI can't auto-flag as event-only but are distributed only via
# events/promotions (Pokémon Center, movies, competitions). Game-exclusive forms
# like pikachu-starter / eevee-starter (Let's Go P/E) are NOT event-only — their
# game-locked availability is expressed through sources.json instead.
EVENT_ONLY_FORM_IDS = {
    "floette-eternal",
    "greninja-battle-bond",
    "magearna-original",
    "pichu-spiky-eared",
    "pikachu-alola-cap",
    "pikachu-belle",
    "pikachu-cosplay",
    "pikachu-hoenn-cap",
    "pikachu-kalos-cap",
    "pikachu-libre",
    "pikachu-original-cap",
    "pikachu-partner-cap",
    "pikachu-phd",
    "pikachu-pop-star",
    "pikachu-rock-star",
    "pikachu-sinnoh-cap",
    "pikachu-unova-cap",
    "pikachu-world-cap",
    "zarude-dada",
}


class RateLimitedClient:
    def __init__(self, client: httpx.Client, min_interval: float) -> None:
        self._client = client
        self._min_interval = min_interval
        self._last_request = 0.0

    def get_json(self, url: str) -> dict[str, Any]:
        cache_key = hashlib.sha256(url.encode()).hexdigest()[:16]
        cache_path = CACHE_DIR / f"{cache_key}.json"
        if cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))

        last_error: Exception | None = None
        for attempt in range(5):
            elapsed = time.monotonic() - self._last_request
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            try:
                resp = self._client.get(url)
                resp.raise_for_status()
                self._last_request = time.monotonic()
                data = resp.json()
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps(data), encoding="utf-8")
                return data
            except (httpx.HTTPError, httpx.StreamError) as exc:
                last_error = exc
                self._last_request = time.monotonic()
                backoff = 2**attempt
                print(f"  retry {attempt + 1}/5 after {backoff}s: {url} ({exc})")
                time.sleep(backoff)
        raise RuntimeError(f"failed to fetch {url}") from last_error


def _is_regional(form_name: str) -> bool:
    if form_name.endswith("-cap"):
        return False
    return any(
        form_name == suffix or form_name.startswith(f"{suffix}-") for suffix in REGIONAL_SUFFIXES
    )


def categorize(
    form_id: str,
    species_id: str,
    is_default_form: bool,
    form_data: dict[str, Any],
    variety_name: str,
) -> list[FormCategory]:
    cats: list[FormCategory] = []
    form_name = form_data.get("form_name") or ""
    if form_data.get("is_mega"):
        cats.append(FormCategory.MEGA)
    if _is_regional(form_name):
        cats.append(FormCategory.REGIONAL_VARIANT)
    if form_name == "gmax" or variety_name.endswith("-gmax"):
        cats.append(FormCategory.GIGANTAMAX)
    if form_name == "primal" or variety_name.endswith("-primal"):
        cats.append(FormCategory.PRIMAL)
    if form_name.startswith("totem"):
        cats.append(FormCategory.TOTEM)
    if form_id in EVENT_ONLY_FORM_IDS:
        cats.append(FormCategory.EVENT_ONLY)
    if not is_default_form and form_name in {"female", "male"}:
        cats.append(FormCategory.GENDER_DIFFERENCE)
    if not is_default_form and not cats:
        if species_id in COSMETIC_SPECIES:
            cats.append(FormCategory.COSMETIC)
        elif species_id in FUNCTIONAL_SPECIES:
            cats.append(FormCategory.FUNCTIONAL)
    return cats


def build_forms_for_species(
    species: dict[str, Any], client: RateLimitedClient
) -> list[dict[str, Any]]:
    species_id = species["name"]
    national_dex = species["id"]
    generation = GENERATION_MAP[species["generation"]["name"]]

    results: list[dict[str, Any]] = []
    for variety in species["varieties"]:
        is_default_variety = variety["is_default"]
        variety_name = variety["pokemon"]["name"]
        pokemon = client.get_json(variety["pokemon"]["url"])

        for form_ref in pokemon["forms"]:
            form_data = client.get_json(form_ref["url"])
            if form_data.get("is_battle_only"):
                continue

            is_default_form = bool(form_data.get("is_default")) and is_default_variety
            form_id = species_id if is_default_form else form_ref["name"]
            if form_id in SKIP_FORM_IDS:
                continue
            categories = categorize(form_id, species_id, is_default_form, form_data, variety_name)
            raw_form_name = form_data.get("form_name") or None

            results.append(
                {
                    "id": form_id,
                    "species_id": species_id,
                    "national_dex": national_dex,
                    "form_name": None if is_default_form else raw_form_name,
                    "is_default": is_default_form,
                    "generation_introduced": generation,
                    "categories": [c.value for c in categories],
                }
            )
    return results


def load_existing_forms() -> list[dict[str, Any]]:
    if not FORMS_PATH.exists():
        return []
    raw = json.loads(FORMS_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw, list)
    return raw


def merge_forms(existing: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {f["id"]: f for f in existing}
    for entry in new:
        by_id.setdefault(entry["id"], entry)
    merged = list(by_id.values())
    merged.sort(key=lambda f: (f["national_dex"], f["id"]))
    return merged


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--max-dex",
        type=int,
        default=151,
        help="Highest national-dex number to scrape (default: 151 = Gen 1)",
    )
    parser.add_argument(
        "--min-dex",
        type=int,
        default=1,
        help="Lowest national-dex number to scrape (default: 1)",
    )
    args = parser.parse_args()

    with httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=30.0,
    ) as raw_client:
        client = RateLimitedClient(raw_client, MIN_REQUEST_INTERVAL)

        all_new: list[dict[str, Any]] = []
        for dex in range(args.min_dex, args.max_dex + 1):
            species = client.get_json(f"{BASE_URL}/pokemon-species/{dex}/")
            forms = build_forms_for_species(species, client)
            all_new.extend(forms)
            print(f"  #{dex:04d} {species['name']}: {len(forms)} form(s)")

    existing = load_existing_forms()
    merged = merge_forms(existing, all_new)

    TypeAdapter(list[Form]).validate_python(merged)

    FORMS_PATH.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(merged)} forms to {FORMS_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
