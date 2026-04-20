"""Print the newest PyPI release of each given package that is ≥48h old.

Used to enforce the "dependencies must be ≥48h old at pin time" rule
(CLAUDE.md, hard rule #2). Also checks transitive pickups after
`uv add`/`uv lock` changes.

Run via: `uv run --with packaging --no-project python scripts/check_dep_age.py pkg1 pkg2 ...`

Exits non-zero if any package has no release old enough to pin, so it can
be wired into CI or pre-commit if desired.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from datetime import UTC, datetime, timedelta

from packaging.version import InvalidVersion, Version

CUTOFF_HOURS = 48
_SEMVER_RE = re.compile(r"^\d+(\.\d+)*$")


def _upload_time(files: list[dict]) -> datetime | None:
    if not files:
        return None
    iso = files[0]["upload_time_iso_8601"].replace("Z", "+00:00")
    return datetime.fromisoformat(iso)


def newest_eligible(pkg: str, cutoff: datetime) -> tuple[str, datetime] | None:
    with urllib.request.urlopen(f"https://pypi.org/pypi/{pkg}/json") as resp:
        data = json.load(resp)
    eligible: list[tuple[str, datetime]] = []
    for version_str, files in data["releases"].items():
        if not _SEMVER_RE.match(version_str):
            continue
        upload = _upload_time(files)
        if upload is None or upload > cutoff:
            continue
        try:
            Version(version_str)
        except InvalidVersion:
            continue
        eligible.append((version_str, upload))
    if not eligible:
        return None
    eligible.sort(key=lambda row: Version(row[0]), reverse=True)
    return eligible[0]


def main(argv: list[str]) -> int:
    if not argv:
        print(f"usage: {sys.argv[0]} pkg [pkg ...]", file=sys.stderr)
        return 2
    cutoff = datetime.now(UTC) - timedelta(hours=CUTOFF_HOURS)
    failed = False
    for pkg in argv:
        result = newest_eligible(pkg, cutoff)
        if result is None:
            print(f"{pkg}: NO release ≥{CUTOFF_HOURS}h old", file=sys.stderr)
            failed = True
            continue
        version, upload = result
        print(f"{pkg}: {version} (uploaded {upload.isoformat()})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
