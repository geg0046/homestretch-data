"""Shared utilities for scrapers in this directory.

Import as `from utils import RateLimitedClient` when running a scraper
script directly — Python adds the script's parent directory to sys.path,
so sibling modules resolve without package plumbing.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import httpx


class RateLimitedClient:
    """HTTP client with per-request throttling, retry/backoff, and JSON cache.

    Caches the parsed JSON body of each URL at `cache_dir/<sha>.json`. Callers
    that need to fetch non-JSON payloads (e.g. wikitext wrapped in a JSON
    envelope) should call `get_json` and unwrap downstream.
    """

    def __init__(
        self,
        client: httpx.Client,
        min_interval: float,
        cache_dir: Path,
    ) -> None:
        self._client = client
        self._min_interval = min_interval
        self._cache_dir = cache_dir
        self._last_request = 0.0

    def get_json(self, url: str) -> dict[str, Any]:
        cache_key = hashlib.sha256(url.encode()).hexdigest()[:16]
        cache_path = self._cache_dir / f"{cache_key}.json"
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
                self._cache_dir.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps(data), encoding="utf-8")
                return data
            except (httpx.HTTPError, httpx.StreamError) as exc:
                last_error = exc
                self._last_request = time.monotonic()
                backoff = 2**attempt
                print(f"  retry {attempt + 1}/5 after {backoff}s: {url} ({exc})")
                time.sleep(backoff)
        raise RuntimeError(f"failed to fetch {url}") from last_error


# Full semantic identity of a Source row for dedup/merge. Alternative
# evolution paths share (form_id, game_id, method) but differ in
# method_details and the structured condition fields, so the merge key
# must span them all. Fields must match Source model attribute names.
SOURCE_KEY_FIELDS: tuple[str, ...] = (
    "form_id",
    "game_id",
    "method",
    "method_details",
    "item",
    "held_item",
    "location",
    "known_move",
    "known_move_type",
    "trade_species",
    "party_species",
    "party_type",
    "from_form",
    "time_of_day",
    "gender",
    "relative_physical_stats",
    "min_happiness",
    "min_affection",
    "min_beauty",
    "needs_overworld_rain",
    "turn_upside_down",
    "needs_multiplayer",
    "requires_dlc",
    "requires_trade",
)


def source_key(entry: dict[str, Any]) -> tuple[Any, ...]:
    """Canonical merge/dedup key for a Source row dict."""
    return tuple(entry.get(f) for f in SOURCE_KEY_FIELDS)


def source_sort_key(entry: dict[str, Any]) -> tuple[str, str, str, str]:
    """Stable sort order for sources.json: groups rows per form/game/method
    and uses method_details as tiebreaker across alternative paths."""
    return (
        entry["form_id"],
        entry["game_id"],
        entry["method"],
        entry.get("method_details") or "",
    )


def merge_by_key[T, K](
    existing: Iterable[T],
    new: Iterable[T],
    key_fn: Callable[[T], K],
) -> list[T]:
    """Merge two iterables keyed by key_fn; existing entries win on conflict.

    Preserves insertion order: existing entries appear first (in their
    original order), then any new entries whose keys weren't already seen.
    Callers that need sorted output should sort the result themselves.
    """
    by_key: dict[K, T] = {}
    for entry in existing:
        by_key.setdefault(key_fn(entry), entry)
    for entry in new:
        by_key.setdefault(key_fn(entry), entry)
    return list(by_key.values())
