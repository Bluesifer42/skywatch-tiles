"""The event record. Field names match SkyEvent.fromJson in the app
(lib/core/events/sky_event.dart); the pipeline adds status, confidence,
sources and review, which the app ignores."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

KINDS = {"meteorShower", "eclipse", "moonPhase", "opposition", "elongation", "conjunction", "comet", "aurora", "news"}
INSTRUMENTS = {"nakedEye", "binoculars", "telescope"}
STATUSES = {"draft", "approved", "rejected", "expired"}


def iso(t: datetime) -> str:
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime:
    s = s.replace("Z", "+00:00")
    t = datetime.fromisoformat(s)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc)


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def make_event(
    *,
    id: str,
    kind: str,
    title: str,
    summary: str,
    peak: datetime,
    start: datetime | None = None,
    end: datetime | None = None,
    date_only: bool = False,
    needs_dark: bool = True,
    instrument: str = "nakedEye",
    radiant_ra_deg: float | None = None,
    radiant_dec_deg: float | None = None,
    magnitude: float | None = None,
    target_id: str | None = None,
    body_ids: list[str] | None = None,
    source_name: str,
    source_url: str,
    confidence: float,
    fetched_at: datetime | None = None,
) -> dict:
    assert kind in KINDS, kind
    assert instrument in INSTRUMENTS, instrument
    fetched = fetched_at or now_utc()
    return {
        "id": id,
        "kind": kind,
        "title": title.strip(),
        "summary": summary.strip(),
        "start": iso(start or peak),
        "peak": iso(peak),
        "end": iso(end or peak),
        "dateOnly": date_only,
        "needsDark": needs_dark,
        "instrument": instrument,
        "radiantRaDeg": radiant_ra_deg,
        "radiantDecDeg": radiant_dec_deg,
        "magnitude": None if magnitude is None else round(magnitude, 1),
        "targetId": target_id,
        "bodyIds": body_ids or [],
        "sourceName": source_name,
        "sourceUrl": source_url,
        # Pipeline-only fields.
        "sources": [{"name": source_name, "url": source_url, "fetched": iso(fetched)}],
        "confidence": round(confidence, 2),
        "status": "draft",
        "review": {"by": None, "at": None, "note": None},
        "firstSeen": iso(fetched),
        "lastSeen": iso(fetched),
        "contentHash": "",
    }


CONTENT_FIELDS = ("title", "summary", "start", "peak", "end", "dateOnly", "needsDark", "instrument", "radiantRaDeg", "radiantDecDeg", "magnitude", "targetId", "bodyIds", "sourceUrl")


def content_hash(e: dict) -> str:
    h = hashlib.sha1()
    for f in CONTENT_FIELDS:
        h.update(f"{f}={e.get(f)!r};".encode())
    return h.hexdigest()[:12]


def slug(s: str) -> str:
    out = []
    for ch in s.lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-")[:60]
