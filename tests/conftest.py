"""Pytest session setup: expose `scrapers/` on sys.path so its helpers are
importable. Scripts in that directory are not packaged and rely on Python
adding the containing dir to sys.path when run directly; tests need the
same access."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRAPERS = Path(__file__).resolve().parent.parent / "scrapers"
if str(_SCRAPERS) not in sys.path:
    sys.path.insert(0, str(_SCRAPERS))
