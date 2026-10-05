"""Tiny HTTP helper with a polite user agent and a cache for offline tests."""
from __future__ import annotations

import os
import urllib.request
from pathlib import Path

UA = "SkywatchEventsPipeline/1.0 (+https://github.com/Bluesifer42/skywatch-tiles)"
TIMEOUT = 30

FIXTURE_DIR = Path(os.environ.get("SKYEVENTS_FIXTURES", "")) if os.environ.get("SKYEVENTS_FIXTURES") else None


def get(url: str, *, fixture: str | None = None) -> str:
    """Fetch text. With SKYEVENTS_FIXTURES set, read <dir>/<fixture> instead."""
    if FIXTURE_DIR is not None and fixture:
        p = FIXTURE_DIR / fixture
        if p.exists():
            return p.read_text(encoding="utf-8")
        raise FileNotFoundError(f"fixture {p} missing (offline mode)")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", errors="replace")
