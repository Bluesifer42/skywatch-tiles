"""The drafts file and the published feeds."""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from .dedupe import expire, find_duplicate, merge_into
from .schema import content_hash, iso, now_utc, parse_iso

# Kinds a human does not need to check before they go live.
AUTO_APPROVE_KINDS = {"meteorShower", "eclipse", "aurora"}
AUTO_APPROVE_MIN_CONFIDENCE = 0.9

LITE_DAYS = 60


def load_drafts(path: Path) -> dict:
    if not path.exists():
        return {"generated": iso(now_utc()), "events": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=False) + "\n", encoding="utf-8")


def ingest(drafts: dict, fetched: list[dict]) -> dict:
    """Merge fetched records into drafts. Returns a change report."""
    events: list[dict] = drafts["events"]
    added, changed, seen = [], [], []
    for new in fetched:
        new["contentHash"] = content_hash(new)
        dup = find_duplicate(new, events)
        if dup is None:
            if new["kind"] in AUTO_APPROVE_KINDS and new["confidence"] >= AUTO_APPROVE_MIN_CONFIDENCE:
                new["status"] = "approved"
                new["review"] = {"by": "pipeline", "at": new["firstSeen"], "note": "auto-approved"}
            events.append(new)
            added.append(new)
        else:
            if merge_into(dup, new):
                changed.append(dup)
            else:
                seen.append(dup)
    drafts["events"] = sorted(expire(events), key=lambda e: e["peak"])
    drafts["generated"] = iso(now_utc())
    return {"added": added, "changed": changed, "unchanged": len(seen)}


def set_status(drafts: dict, event_id: str, status: str, by: str, note: str | None = None) -> bool:
    for e in drafts["events"]:
        if e["id"] == event_id:
            e["status"] = status
            e["review"] = {"by": by, "at": iso(now_utc()), "note": note}
            return True
    return False


def _public(e: dict) -> dict:
    """Strip pipeline-only fields for the app."""
    return {k: v for k, v in e.items() if k not in ("sources", "confidence", "status", "review", "firstSeen", "lastSeen", "contentHash")}


def publish(drafts: dict, out_dir: Path) -> dict:
    now = now_utc()
    approved = [e for e in drafts["events"] if e["status"] == "approved" and parse_iso(e["end"]) >= now - timedelta(days=1)]
    full = {"generated": iso(now), "count": len(approved), "events": [_public(e) for e in approved]}
    horizon = now + timedelta(days=LITE_DAYS)
    lite_events = [e for e in approved if parse_iso(e["peak"]) <= horizon or parse_iso(e["start"]) <= now]
    lite = {"generated": iso(now), "count": len(lite_events), "events": [_public(e) for e in lite_events]}
    save_json(out_dir / "events.json", full)
    save_json(out_dir / "events-lite.json", lite)
    return {"full": len(approved), "lite": len(lite_events)}


def pr_body(report: dict, drafts: dict) -> str:
    lines = ["## Sky events: drafts refreshed", ""]
    if report["added"]:
        lines.append(f"### New ({len(report['added'])})")
        for e in report["added"]:
            lines.append(f"- `{e['status']}` **{e['title']}** · {e['kind']} · {e['peak'][:16]}Z · {e['sourceName']} · confidence {e['confidence']}")
        lines.append("")
    if report["changed"]:
        lines.append(f"### Changed ({len(report['changed'])})")
        for e in report["changed"]:
            lines.append(f"- `{e['status']}` **{e['title']}** · {e['kind']} · {e['peak'][:16]}Z")
        lines.append("")
    pending = [e for e in drafts["events"] if e["status"] == "draft"]
    lines.append(f"{report['unchanged']} unchanged. {len(pending)} drafts awaiting review in total.")
    lines.append("")
    lines.append("To approve: change `status` to `approved` in `events/drafts.json` (or run `python pipeline/fetch_events.py approve <id>`), then merge. Rejected records stay rejected and never resurface.")
    return "\n".join(lines) + "\n"
