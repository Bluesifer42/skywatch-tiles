"""python -m unittest discover -s pipeline/tests"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
os.environ["SKYEVENTS_FIXTURES"] = str(HERE / "fixtures")

from skyevents import comet_ephem, dedupe, fetchers, store  # noqa: E402
from skyevents.schema import make_event, now_utc  # noqa: E402


def ev(id, kind, title, peak, source="NASA", url="https://x/1", **kw):
    return make_event(id=id, kind=kind, title=title, summary="s", peak=peak, source_name=source, source_url=url, confidence=kw.pop("confidence", 0.7), **kw)


class CometEphemeris(unittest.TestCase):
    def test_halley_1986_perihelion_distance(self):
        # 1P/Halley: q 0.5871, e 0.9673, peri 111.33, node 58.42, i 162.26, T 1986-02-09.
        el = comet_ephem.CometElements("1P", "1P/Halley", datetime(1986, 2, 9, 11, tzinfo=timezone.utc), 0.5871, 0.9673, 111.33, 58.42, 162.26, 5.5, 4)
        s = comet_ephem.state_at(el, datetime(1986, 2, 9, 11, tzinfo=timezone.utc))
        self.assertAlmostEqual(s.r_au, 0.5871, places=3)
        # Two months later it was about 0.4 AU from Earth and magnitude ~3.
        s2 = comet_ephem.state_at(el, datetime(1986, 4, 11, tzinfo=timezone.utc))
        self.assertLess(s2.delta_au, 0.5)
        self.assertLess(s2.magnitude, 5)

    def test_parabolic_and_hyperbolic_orbits_do_not_blow_up(self):
        t0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
        for e in (1.0, 1.0003, 0.99998):
            el = comet_ephem.CometElements("X", "X", t0, 0.8, e, 10, 20, 30, 8, 4)
            for d in (-60, 0, 60):
                s = comet_ephem.state_at(el, t0 + timedelta(days=d))
                self.assertTrue(0.7 < s.r_au < 3, (e, d, s.r_au))

    def test_mpc_line_parses(self):
        line = "    CK23A030  2024 09 27.7320  0.391424  1.000089  308.4867   21.5596  139.1121  2024 09 24  5.0  4.0  C/2023 A3 (Tsuchinshan-ATLAS)                             MPEC 2024-R64"
        el = comet_ephem.parse_mpc_line(line)
        self.assertIsNotNone(el)
        self.assertAlmostEqual(el.q_au, 0.391424)
        self.assertEqual(el.designation, "C/2023 A3")
        self.assertEqual(el.perihelion_time.year, 2024)


class Dedupe(unittest.TestCase):
    def test_canonical_ids_merge_and_sources_accumulate(self):
        t = now_utc() + timedelta(days=10)
        drafts = {"generated": "", "events": []}
        a = ev("shower:ORI:2026", "meteorShower", "Orionids peak", t, source="static (IAU MDC)", url="https://s", confidence=0.9)
        r = store.ingest(drafts, [a])
        self.assertEqual(len(r["added"]), 1)
        self.assertEqual(drafts["events"][0]["status"], "approved")  # auto-approved kind
        b = ev("shower:ORI:2026", "meteorShower", "Orionids peak", t + timedelta(hours=5), source="IMO", url="https://imo", confidence=0.97)
        r = store.ingest(drafts, [b])
        self.assertEqual(len(r["added"]), 0)
        e = drafts["events"][0]
        self.assertEqual(len(e["sources"]), 2)
        # Approved content is frozen even though IMO outranks the static table.
        self.assertEqual(e["peak"], a["peak"])

    def test_draft_content_updates_from_a_better_source(self):
        t = now_utc() + timedelta(days=10)
        drafts = {"generated": "", "events": []}
        a = ev("comet:c-2026-x1", "comet", "Comet X at its brightest", t, source="COBS", url="https://c", confidence=0.5)
        store.ingest(drafts, [a])
        b = ev("comet:c-2026-x1", "comet", "Comet X at its brightest", t + timedelta(days=1), source="MPC", url="https://m", confidence=0.6)
        r = store.ingest(drafts, [b])
        self.assertEqual(len(r["changed"]), 1)
        self.assertEqual(drafts["events"][0]["peak"], b["peak"])
        self.assertEqual(drafts["events"][0]["status"], "draft")

    def test_news_fuzzy_merge_across_outlets(self):
        t = now_utc()
        drafts = {"generated": "", "events": []}
        a = ev("news:nasa-1", "news", "NASA's Europa Clipper spots a plume on Jupiter's moon", t, source="NASA", url="https://nasa/1")
        b = ev("news:esa-9", "news", "Europa Clipper spots plume on Jupiter moon Europa", t + timedelta(hours=10), source="ESA", url="https://esa/9")
        c = ev("news:ut-3", "news", "Perseverance finds a new rock on Mars", t, source="Universe Today", url="https://ut/3")
        r = store.ingest(drafts, [a, b, c])
        self.assertEqual(len(drafts["events"]), 2)
        merged = next(e for e in drafts["events"] if e["id"] == "news:nasa-1")
        self.assertEqual(len(merged["sources"]), 2)
        self.assertGreaterEqual(dedupe.similar(a["title"], b["title"]), 0.85)

    def test_rejected_stays_rejected_and_expiry(self):
        t = now_utc() - timedelta(days=5)
        drafts = {"generated": "", "events": []}
        a = ev("news:old", "news", "Old story", t, end=t + timedelta(days=1))
        store.ingest(drafts, [a])
        store.set_status(drafts, "news:old", "rejected", by="tester")
        store.ingest(drafts, [ev("news:old", "news", "Old story", t, end=t + timedelta(days=1))])
        self.assertEqual(drafts["events"][0]["status"], "expired")  # ended 4 days ago
        self.assertEqual(len(drafts["events"]), 1)

    def test_publish_strips_pipeline_fields_and_filters(self):
        t = now_utc()
        drafts = {"generated": "", "events": []}
        store.ingest(drafts, [
            ev("eclipse:total-lunar:2099-01-01", "eclipse", "Total lunar eclipse", t + timedelta(days=400), source="NASA eclipse catalogue", url="https://e", confidence=0.95),
            ev("aurora:2026-10-10", "aurora", "Aurora forecast", t + timedelta(days=2), source="NOAA SWPC", url="https://n", confidence=0.95),
            ev("news:pending", "news", "Pending story", t),
        ])
        with tempfile.TemporaryDirectory() as d:
            r = store.publish(drafts, Path(d))
            self.assertEqual(r["full"], 2)   # the news draft is not published
            self.assertEqual(r["lite"], 1)   # the eclipse is beyond 60 days
            import json
            lite = json.loads((Path(d) / "events-lite.json").read_text())
            self.assertNotIn("status", lite["events"][0])
            self.assertIn("summary", lite["events"][0])


class Fetchers(unittest.TestCase):
    def test_static_showers_cover_the_year(self):
        s = fetchers.fetch_showers(year=2026, use_imo=False)
        ids = {e["id"] for e in s}
        self.assertIn("shower:GEM:2026", ids)
        gem = next(e for e in s if e["id"] == "shower:GEM:2026")
        self.assertEqual(gem["peak"][:10], "2026-12-14")
        self.assertEqual(gem["radiantRaDeg"], 112)

    def test_imo_peak_parser(self):
        text = "<h3>Orionids (ORI)</h3><p>Active: October 2–November 7. Maximum: October 21, 05:00 UT. ZHR 20.</p>"
        self.assertEqual(fetchers._imo_peaks(text)["ORI"], (10, 21, "05:00"))

    def test_aurora_fixture(self):
        out = fetchers.fetch_aurora()
        self.assertEqual(len(out), 1)
        self.assertIn("Kp 6", out[0]["title"])

    def test_news_fixture_filters_to_sky_stories(self):
        out = fetchers.fetch_news()
        titles = [e["title"] for e in out]
        self.assertTrue(any("comet" in t.lower() for t in titles))
        self.assertFalse(any("budget" in t.lower() for t in titles))


if __name__ == "__main__":
    unittest.main()
