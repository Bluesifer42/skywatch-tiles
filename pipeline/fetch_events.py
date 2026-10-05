#!/usr/bin/env python3
"""Sky-events pipeline command line.

  fetch   [--only showers,comets,...] [--no-imo]   refresh events/drafts.json
  publish                                           write events/events.json + events-lite.json
  approve <id> [--note ...] | reject <id> [--note ...] | list [--status draft]

Run from the repo root. Set SKYEVENTS_FIXTURES=<dir> to read saved feed
responses instead of the network (tests).
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from skyevents import fetchers, store  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EVENTS_DIR = ROOT / "events"
DRAFTS = EVENTS_DIR / "drafts.json"
OUT_DIR = ROOT / "pipeline" / "out"


def cmd_fetch(args: argparse.Namespace) -> int:
    names = args.only.split(",") if args.only else list(fetchers.ALL_FETCHERS)
    drafts = store.load_drafts(DRAFTS)
    fetched: list[dict] = []
    for n in names:
        fn = fetchers.ALL_FETCHERS[n]
        try:
            got = fn(use_imo=not args.no_imo) if n == "showers" else fn()
        except Exception as ex:  # noqa: BLE001
            logging.getLogger("skyevents").error("%s failed: %s", n, ex)
            got = []
        logging.getLogger("skyevents").info("%s: %d records", n, len(got))
        fetched.extend(got)
    report = store.ingest(drafts, fetched)
    store.save_json(DRAFTS, drafts)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "pr_body.md").write_text(store.pr_body(report, drafts), encoding="utf-8")
    print(f"added {len(report['added'])}, changed {len(report['changed'])}, unchanged {report['unchanged']}, total {len(drafts['events'])}")
    for e in report["added"]:
        print(f"  + [{e['status']}] {e['peak'][:10]} {e['kind']:13} {e['title']}")
    for e in report["changed"]:
        print(f"  ~ [{e['status']}] {e['peak'][:10]} {e['kind']:13} {e['title']}")
    return 0


def cmd_publish(_: argparse.Namespace) -> int:
    drafts = store.load_drafts(DRAFTS)
    r = store.publish(drafts, EVENTS_DIR)
    print(f"published {r['full']} events ({r['lite']} in the 60-day lite feed)")
    return 0


def cmd_status(args: argparse.Namespace, status: str) -> int:
    drafts = store.load_drafts(DRAFTS)
    if not store.set_status(drafts, args.id, status, by=args.by, note=args.note):
        print(f"no event with id {args.id}", file=sys.stderr)
        return 1
    store.save_json(DRAFTS, drafts)
    print(f"{args.id}: {status}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    drafts = store.load_drafts(DRAFTS)
    for e in drafts["events"]:
        if args.status and e["status"] != args.status:
            continue
        print(f"[{e['status']:8}] {e['peak'][:10]} {e['kind']:13} {e['id']:40} {e['title']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--only")
    f.add_argument("--no-imo", action="store_true")
    sub.add_parser("publish")
    for name in ("approve", "reject"):
        s = sub.add_parser(name)
        s.add_argument("id")
        s.add_argument("--note")
        s.add_argument("--by", default="human")
    ls = sub.add_parser("list")
    ls.add_argument("--status")
    args = p.parse_args(argv)
    return {
        "fetch": cmd_fetch,
        "publish": cmd_publish,
        "approve": lambda a: cmd_status(a, "approved"),
        "reject": lambda a: cmd_status(a, "rejected"),
        "list": cmd_list,
    }[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
