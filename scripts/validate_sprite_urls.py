"""HEAD-request every unique sprite_url in data/forms.json; fail on non-200.

Run via: `uv run python scripts/validate_sprite_urls.py`
Network-dependent and slow (~25 min cold, seconds warm). Intentionally NOT
in pre-commit or CI by default — run locally before opening a PR that
touches sprite data.

Caches HEAD response statuses under .cache/sprite-heads/ so re-runs after
fixing one bad URL skip the URLs already proven 200.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
FORMS_PATH = REPO_ROOT / "data" / "forms.json"
CACHE_DIR = REPO_ROOT / ".cache" / "sprite-heads"
USER_AGENT = (
    "HomeStretch/0.1 (+https://github.com/geg0046/homestretch-data; "
    "contact: homestretchapp@outlook.com)"
)
MIN_REQUEST_INTERVAL = 1.0


def _cache_path(url: str) -> Path:
    import hashlib

    digest = hashlib.sha256(url.encode()).hexdigest()[:16]
    return CACHE_DIR / f"{digest}.json"


def _check_url(client: httpx.Client, url: str, last_request: list[float]) -> int:
    """Return HTTP status. Cached statuses skip the network call."""
    cache_path = _cache_path(url)
    if cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        return int(cached["status"])

    last_error: Exception | None = None
    for attempt in range(5):
        elapsed = time.monotonic() - last_request[0]
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)
        try:
            resp = client.head(url, follow_redirects=True)
            last_request[0] = time.monotonic()
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps({"status": resp.status_code}), encoding="utf-8")
            return resp.status_code
        except (httpx.HTTPError, httpx.StreamError) as exc:
            last_error = exc
            last_request[0] = time.monotonic()
            backoff = 2**attempt
            print(f"  retry {attempt + 1}/5 after {backoff}s: {url} ({exc})")
            time.sleep(backoff)
    raise RuntimeError(f"failed to HEAD {url}") from last_error


def main() -> int:
    forms = json.loads(FORMS_PATH.read_text(encoding="utf-8"))
    urls = sorted({f["sprite_url"] for f in forms})
    print(f"checking {len(urls)} unique sprite URL(s) (of {len(forms)} forms)")

    failures: list[tuple[str, int, list[str]]] = []
    by_url_form_ids: dict[str, list[str]] = {}
    for f in forms:
        by_url_form_ids.setdefault(f["sprite_url"], []).append(f["id"])

    last_request = [0.0]
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30.0) as client:
        for i, url in enumerate(urls, 1):
            status = _check_url(client, url, last_request)
            if status != 200:
                failures.append((url, status, by_url_form_ids[url]))
                print(f"  [{i}/{len(urls)}] {status} {url}")
            elif i % 100 == 0:
                print(f"  [{i}/{len(urls)}] ok")

    if failures:
        print(f"\n{len(failures)} URL(s) failed:", file=sys.stderr)
        for url, status, form_ids in failures:
            print(f"  {status} {url} — used by {form_ids}", file=sys.stderr)
        return 1

    print(f"OK: {len(urls)} sprite URLs all return 200")
    return 0


if __name__ == "__main__":
    sys.exit(main())
