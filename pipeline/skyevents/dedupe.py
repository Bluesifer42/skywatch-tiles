"""Merging fetched records into the drafts store.

Canonical ids do most of the work (shower codes, MPC designations, dates).
Records without a natural id (news) are matched by kind, a 48-hour window
and title similarity. Review status always survives a merge.
"""
from __future__ import annotations

import re
from datetime import timedelta
from difflib import SequenceMatcher

from .schema import content_hash, iso, now_utc, parse_iso

# Which source wins a numeric or text field when two disagree.
AUTHORITY = {
    "meteorShower": ["IMO", "AMS", "static"],
    "comet": ["MPC", "COBS", "JPL"],
    "aurora": ["NOAA SWPC"],
    "news": ["NASA", "ESA", "Sky & Telescope", "EarthSky", "Universe Today"],
}

FUZZY_THRESHOLD = 0.85
FUZZY_WINDOW = timedelta(hours=48)

_STOP = {"the", "a", "an", "of", "in", "on", "at", "to", "and", "for", "with", "is", "are", "its"}


def _norm_title(t: str) -> str:
    words = re.findall(r"[a-z0-9]+", t.lower())
    return " ".join(w for w in words if w not in _STOP)


def similar(a: str, b: str) -> float:
    """Headline similarity, 0..1: the better of a character-level ratio and a
    word-set score, so reordered or trimmed headlines still match while
    stories that merely share a subject do not."""
    na, nb = _norm_title(a), _norm_title(b)
    seq = SequenceMatcher(None, na, nb).ratio()
    ta = {w for w in na.split() if len(w) > 1}
    tb = {w for w in nb.split() if len(w) > 1}
    if not ta or not tb:
        return seq
    inter = len(ta & tb)
    jaccard = inter / len(ta | tb)
    containment = inter / min(len(ta), len(tb))
    return max(seq, 0.5 * jaccard + 0.5 * containment)


def _rank(kind: str, source: str) -> int:
    order = AUTHORITY.get(kind, [])
    for i, s in enumerate(order):
        if source.startswith(s):
            return i
    return len(order)


def find_duplicate(new: dict, existing: list[dict]) -> dict | None:
    """An existing record that is the same event as [new], or None."""
    for e in existing:
        if e["id"] == new["id"]:
            return e
    if new["kind"] not in ("news",):
        return None
    peak = parse_iso(new["peak"])
    for e in existing:
        if e["kind"] != new["kind"]:
            continue
        if abs(parse_iso(e["peak"]) - peak) > FUZZY_WINDOW:
            continue
        if similar(e["title"], new["title"]) >= FUZZY_THRESHOLD:
            return e
    return None


def merge_into(existing: dict, new: dict) -> bool:
    """Fold [new] into [existing]. Returns True when content changed."""
    changed = False
    existing["lastSeen"] = new["lastSeen"]
    # Record every source once.
    known = {(s["name"], s["url"]) for s in existing["sources"]}
    for s in new["sources"]:
        if (s["name"], s["url"]) not in known:
            existing["sources"].append(s)
            known.add((s["name"], s["url"]))
    # Approved content is frozen: a human signed off on those words.
    if existing["status"] == "approved":
        return False
    before = content_hash(existing)
    new_wins = _rank(new["kind"], new["sourceName"]) <= _rank(existing["kind"], existing["sourceName"])
    for f in ("title", "summary", "start", "peak", "end", "dateOnly", "needsDark", "instrument", "radiantRaDeg", "radiantDecDeg", "magnitude", "targetId", "bodyIds", "sourceName", "sourceUrl"):
        if new_wins or existing.get(f) in (None, "", []):
            if existing.get(f) != new.get(f):
                existing[f] = new.get(f)
    existing["confidence"] = max(existing["confidence"], new["confidence"])
    after = content_hash(existing)
    if after != before:
        existing["contentHash"] = after
        changed = True
    return changed


def expire(events: list[dict], keep_expired_days: int = 30) -> list[dict]:
    now = now_utc()
    out = []
    for e in events:
        end = parse_iso(e["end"])
        if end < now - timedelta(days=2) and e["status"] != "expired":
            e["status"] = "expired"
            e["review"]["at"] = iso(now)
        if e["status"] == "expired" and end < now - timedelta(days=keep_expired_days):
            continue
        out.append(e)
    return out
