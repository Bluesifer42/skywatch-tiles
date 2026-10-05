"""One function per source. Each returns a list of event records and never
raises on a bad feed: a source that is down just contributes nothing this
run, and the log says so."""
from __future__ import annotations

import html
import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from . import comet_ephem
from .http import get
from .schema import make_event, now_utc, slug

log = logging.getLogger("skyevents")

# ----------------------------------------------------------------- showers

# IAU code, name, start (m, d), peak (m, d), end (m, d), ZHR, radiant RA/Dec, parent, speed.
SHOWERS = [
    ("QUA", "Quadrantids", (12, 28), (1, 3), (1, 12), 110, 230, 49, "asteroid 2003 EH1", 41),
    ("LYR", "Lyrids", (4, 14), (4, 22), (4, 30), 18, 271, 34, "comet C/1861 G1 Thatcher", 49),
    ("ETA", "Eta Aquariids", (4, 19), (5, 6), (5, 28), 50, 338, -1, "Halley's comet", 66),
    ("SDA", "Southern delta Aquariids", (7, 12), (7, 30), (8, 23), 25, 340, -16, "comet 96P/Machholz", 41),
    ("PER", "Perseids", (7, 17), (8, 12), (8, 24), 100, 48, 58, "comet 109P/Swift-Tuttle", 59),
    ("DRA", "Draconids", (10, 6), (10, 8), (10, 10), 10, 262, 54, "comet 21P/Giacobini-Zinner", 20),
    ("STA", "Southern Taurids", (9, 10), (10, 10), (11, 20), 5, 32, 9, "comet 2P/Encke", 27),
    ("ORI", "Orionids", (10, 2), (10, 21), (11, 7), 20, 95, 16, "Halley's comet", 66),
    ("NTA", "Northern Taurids", (10, 20), (11, 12), (12, 10), 5, 58, 22, "comet 2P/Encke", 29),
    ("LEO", "Leonids", (11, 6), (11, 17), (11, 30), 10, 152, 22, "comet 55P/Tempel-Tuttle", 71),
    ("GEM", "Geminids", (12, 4), (12, 14), (12, 20), 150, 112, 33, "asteroid 3200 Phaethon", 35),
    ("URS", "Ursids", (12, 17), (12, 22), (12, 26), 10, 217, 76, "comet 8P/Tuttle", 33),
]

IMO_CALENDAR = "https://www.imo.net/resources/calendar/"
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def _imo_peaks(text: str) -> dict[str, tuple[int, int, str | None]]:
    """Best-effort: for each shower name find 'Maximum ... <Month> <day> [hh:mm]'
    within the following text. Returns code -> (month, day, time)."""
    out = {}
    flat = re.sub(r"<[^>]+>", " ", text)
    flat = html.unescape(re.sub(r"\s+", " ", flat))
    for code, name, *_ in SHOWERS:
        i = flat.find(name)
        if i < 0:
            continue
        window = flat[i : i + 600]
        m = re.search(r"(?:Maximum|Peak|maximum|peak)[^A-Za-z0-9]{0,20}(?:\w+\s)?(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2})(?:[^0-9]{0,12}(\d{1,2}:\d{2}))?", window)
        if not m:
            continue
        out[code] = (MONTHS[m.group(1).lower()[:3]], int(m.group(2)), m.group(3))
    return out


def fetch_showers(year: int | None = None, use_imo: bool = True) -> list[dict]:
    now = now_utc()
    years = [now.year, now.year + 1] if year is None else [year]
    peaks: dict[str, tuple[int, int, str | None]] = {}
    if use_imo:
        try:
            peaks = _imo_peaks(get(IMO_CALENDAR, fixture="imo_calendar.html"))
            log.info("IMO calendar: refined %d peaks", len(peaks))
        except Exception as ex:  # noqa: BLE001
            log.warning("IMO calendar unavailable: %s", ex)
    out = []
    for y in years:
        for code, name, (sm, sd), (pm, pd), (em, ed), zhr, ra, dec, parent, speed in SHOWERS:
            refined = peaks.get(code)
            hour, minute, conf, src, url = 12, 0, 0.9, "static (IAU MDC)", "https://www.ta3.sk/IAUC22DB/MDC2007/"
            if refined and refined[0] == pm:
                pm, pd = refined[0], refined[1]
                if refined[2]:
                    hour, minute = (int(x) for x in refined[2].split(":"))
                conf, src, url = 0.97, "IMO", IMO_CALENDAR
            peak = datetime(y, pm, pd, hour, minute, tzinfo=timezone.utc)
            start = datetime(y, sm, sd, tzinfo=timezone.utc)
            if start > peak:
                start = datetime(y - 1, sm, sd, tzinfo=timezone.utc)
            end = datetime(y, em, ed, 23, 59, tzinfo=timezone.utc)
            if end < peak:
                end = datetime(y + 1, em, ed, 23, 59, tzinfo=timezone.utc)
            if end < now - timedelta(days=7):
                continue
            out.append(make_event(
                id=f"shower:{code}:{y}",
                kind="meteorShower",
                title=f"{name} peak",
                summary=f"Up to {zhr} an hour under a perfect dark sky, fewer in practice. Debris from {parent}, hitting the atmosphere at {speed} km/s. Best after midnight when the radiant is high.",
                peak=peak, start=start, end=end,
                date_only=refined is None or refined[2] is None,
                radiant_ra_deg=ra, radiant_dec_deg=dec,
                source_name=src, source_url=url, confidence=conf,
            ))
    return out


# ----------------------------------------------------------------- comets

MPC_OBSERVABLE = "https://minorplanetcenter.net/iau/Ephemerides/Comets/Soft00Cmt.txt"
COMET_BRIGHT_LIMIT = 9.0  # binoculars from a dark site
COMET_MIN_ELONGATION = 25.0


def fetch_comets(days: int = 90) -> list[dict]:
    try:
        text = get(MPC_OBSERVABLE, fixture="Soft00Cmt.txt")
    except Exception as ex:  # noqa: BLE001
        log.warning("MPC comet elements unavailable: %s", ex)
        return []
    now = now_utc()
    out = []
    for line in text.splitlines():
        el = comet_ephem.parse_mpc_line(line)
        if el is None:
            continue
        # Sample the window; keep the brightest observable moment.
        best = None
        for d in range(0, days + 1, 3):
            t = now + timedelta(days=d)
            try:
                s = comet_ephem.state_at(el, t)
            except (ValueError, ZeroDivisionError, OverflowError):
                break
            if s.elongation_deg < COMET_MIN_ELONGATION:
                continue
            if best is None or s.magnitude < best[1].magnitude:
                best = (t, s)
        if best is None or best[1].magnitude > COMET_BRIGHT_LIMIT:
            continue
        t, s = best
        first = next((now + timedelta(days=d) for d in range(0, days + 1, 3) if comet_ephem.state_at(el, now + timedelta(days=d)).magnitude <= COMET_BRIGHT_LIMIT + 1), t)
        instrument = "nakedEye" if s.magnitude <= 5 else "binoculars" if s.magnitude <= 8 else "telescope"
        out.append(make_event(
            id=f"comet:{slug(el.designation)}",
            kind="comet",
            title=f"Comet {el.name} at its brightest",
            summary=(f"Predicted magnitude {s.magnitude:.1f} around {t.day} {t:%b}, {s.elongation_deg:.0f}° from the Sun, "
                     f"{s.delta_au:.2f} AU from Earth. Comet brightness is notoriously unpredictable: this is the orbit's "
                     f"forecast, not an observation. Perihelion {el.perihelion_time.day} {el.perihelion_time:%b %Y} at {el.q_au:.2f} AU."),
            peak=t, start=first, end=t + timedelta(days=14),
            instrument=instrument, magnitude=s.magnitude,
            radiant_ra_deg=round(s.ra_deg, 2), radiant_dec_deg=round(s.dec_deg, 2),
            body_ids=[el.designation],
            source_name="MPC", source_url=MPC_OBSERVABLE, confidence=0.6,
        ))
    return out


# ----------------------------------------------------------------- aurora

SWPC_KP_FORECAST = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index-forecast.json"
KP_ALERT = 5


def fetch_aurora() -> list[dict]:
    try:
        rows = json.loads(get(SWPC_KP_FORECAST, fixture="kp_forecast.json"))
    except Exception as ex:  # noqa: BLE001
        log.warning("NOAA SWPC Kp forecast unavailable: %s", ex)
        return []
    out = []
    # SWPC has served this both as a list of objects and as rows with a
    # header line; accept either.
    peaks: dict[str, tuple[datetime, float, str]] = {}
    for row in rows:
        try:
            if isinstance(row, dict):
                when, kp, kind, scale = row.get("time_tag", ""), float(row.get("kp", 0)), row.get("observed", ""), row.get("noaa_scale") or ""
            else:
                if row[0] == "time_tag":
                    continue
                when, kp, kind, scale = row[0], float(row[1]), row[2], (row[3] if len(row) > 3 else "") or ""
            t = datetime.fromisoformat(when.replace(" ", "T")).replace(tzinfo=timezone.utc)
        except (ValueError, IndexError, TypeError, AttributeError):
            continue
        if kind not in ("predicted", "estimated"):
            continue
        if kp < KP_ALERT:
            continue
        day = t.strftime("%Y-%m-%d")
        if day not in peaks or kp > peaks[day][1]:
            peaks[day] = (t, kp, scale)
    for day, (t, kp, scale) in peaks.items():
        lat = "about 55° latitude and poleward (Scotland, Scandinavia, Canada)" if kp < 6 else "about 50° and poleward (northern England, Germany, the northern US)" if kp < 7 else "mid-latitudes: much of the UK, Europe and the US"
        out.append(make_event(
            id=f"aurora:{day}",
            kind="aurora",
            title=f"Aurora forecast: Kp {kp:.0f}{(' (' + scale + ')') if scale else ''}",
            summary=f"NOAA forecasts a geomagnetic storm peaking around {t:%H:%M} UTC. Aurora possible from {lat}. Look north from a dark spot after dusk; a phone camera on night mode sees it before your eyes do.",
            peak=t, start=t - timedelta(hours=3), end=t + timedelta(hours=6),
            needs_dark=True, body_ids=["sun"],
            source_name="NOAA SWPC", source_url=SWPC_KP_FORECAST, confidence=0.95,
        ))
    return out


# ----------------------------------------------------------------- eclipses

ECLIPSES = [
    (2026, 2, 17, "Annular solar", "Antarctica; partial from southern Africa and South America"),
    (2026, 3, 3, "Total lunar", "Pacific, Americas, eastern Asia, Australia"),
    (2026, 8, 12, "Total solar", "Greenland, Iceland, northern Spain; deep partial across Europe"),
    (2026, 8, 28, "Partial lunar", "Americas, Europe, Africa"),
    (2027, 2, 6, "Annular solar", "South America, Atlantic, west Africa"),
    (2027, 8, 2, "Total solar", "North Africa and the Middle East; six minutes at Luxor"),
    (2028, 1, 12, "Partial lunar", "Americas, Europe, Africa"),
    (2028, 1, 26, "Annular solar", "South America, Atlantic, Iberia"),
    (2028, 7, 6, "Partial lunar", "Africa, Europe, Asia, Australia"),
    (2028, 7, 22, "Total solar", "Australia and New Zealand"),
    (2028, 12, 31, "Total lunar", "Europe, Africa, Asia, Australia"),
    (2029, 1, 14, "Partial solar", "North America"),
    (2029, 6, 26, "Total lunar", "Americas, Europe, Africa"),
    (2029, 12, 20, "Total lunar", "Americas, Europe, Africa, Asia"),
    (2030, 6, 1, "Annular solar", "Europe, Russia, Japan"),
    (2030, 11, 25, "Total solar", "Southern Africa and Australia"),
]


def fetch_eclipses() -> list[dict]:
    now = now_utc()
    out = []
    for y, m, d, kind, where in ECLIPSES:
        peak = datetime(y, m, d, 12, tzinfo=timezone.utc)
        if peak < now - timedelta(days=2):
            continue
        solar = kind.endswith("solar")
        out.append(make_event(
            id=f"eclipse:{kind.split()[0].lower()}-{kind.split()[1]}:{y}-{m:02d}-{d:02d}",
            kind="eclipse",
            title=f"{kind} eclipse",
            summary=f"Visible from: {where}. " + ("Never look at the Sun without a proper solar filter, eclipse or not." if solar else "Lunar eclipses are safe to watch with any instrument and need no filter."),
            peak=peak, start=peak.replace(hour=0), end=peak.replace(hour=23, minute=59),
            date_only=True, needs_dark=not solar, body_ids=["sun" if solar else "moon"], target_id="sun" if solar else "moon",
            source_name="NASA eclipse catalogue", source_url="https://eclipse.gsfc.nasa.gov/eclipse.html", confidence=0.95,
        ))
    return out


# ------------------------------------------------------------------- news

RSS_FEEDS = [
    ("NASA", "https://www.nasa.gov/news-release/feed/", "nasa.xml"),
    ("ESA", "https://www.esa.int/rssfeed/Our_Activities/Space_Science", "esa.xml"),
    ("EarthSky", "https://earthsky.org/feed/", "earthsky.xml"),
    ("Universe Today", "https://www.universetoday.com/feed", "universetoday.xml"),
]
NEWS_MAX_AGE_DAYS = 7
NEWS_PER_FEED = 8
_SKY_WORDS = re.compile(r"\b(comet|meteor|aurora|eclipse|moon|lunar|planet|jupiter|saturn|mars|venus|mercury|nebula|galaxy|telescope|conjunction|supernova|nova|asteroid|occultation|sky|stars?|observ)", re.I)


def _strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def _parse_rss_date(s: str) -> datetime | None:
    from email.utils import parsedate_to_datetime

    try:
        t = parsedate_to_datetime(s)
        return t.astimezone(timezone.utc) if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def fetch_news() -> list[dict]:
    now = now_utc()
    out = []
    for name, url, fixture in RSS_FEEDS:
        try:
            root = ET.fromstring(get(url, fixture=fixture))
        except Exception as ex:  # noqa: BLE001
            log.warning("%s feed unavailable: %s", name, ex)
            continue
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        items = root.findall(".//item") or root.findall(".//atom:entry", ns)
        count = 0
        for it in items:
            title = _strip_html((it.findtext("title") or it.findtext("atom:title", namespaces=ns) or ""))
            link = (it.findtext("link") or "").strip()
            if not link:
                a = it.find("atom:link", ns)
                link = a.get("href", "") if a is not None else ""
            desc = _strip_html(it.findtext("description") or it.findtext("atom:summary", namespaces=ns) or it.findtext("atom:content", namespaces=ns) or "")
            when = _parse_rss_date(it.findtext("pubDate") or it.findtext("atom:published", namespaces=ns) or it.findtext("atom:updated", namespaces=ns) or "")
            if not title or not link or when is None:
                continue
            if when < now - timedelta(days=NEWS_MAX_AGE_DAYS):
                continue
            if not _SKY_WORDS.search(title + " " + desc[:200]):
                continue
            out.append(make_event(
                id=f"news:{slug(link.split('://', 1)[-1])}",
                kind="news",
                title=title[:140],
                summary=(desc[:220].rsplit(" ", 1)[0] + "…") if len(desc) > 220 else desc,
                peak=when, start=when, end=when + timedelta(days=NEWS_MAX_AGE_DAYS),
                needs_dark=False, instrument="nakedEye",
                source_name=name, source_url=link, confidence=0.7,
            ))
            count += 1
            if count >= NEWS_PER_FEED:
                break
    return out


# ------------------------------------------------------------------- APOD

APOD = "https://api.nasa.gov/planetary/apod"


def fetch_apod() -> list[dict]:
    key = os.environ.get("NASA_API_KEY", "DEMO_KEY")
    try:
        j = json.loads(get(f"{APOD}?api_key={key}&thumbs=true", fixture="apod.json"))
    except Exception as ex:  # noqa: BLE001
        log.warning("APOD unavailable: %s", ex)
        return []
    if not isinstance(j, dict) or "date" not in j or not j.get("explanation") or not j.get("title"):
        log.warning("APOD returned no picture (%s)", str(j)[:80])
        return []
    when = datetime.strptime(j["date"], "%Y-%m-%d").replace(hour=12, tzinfo=timezone.utc)
    expl = (j.get("explanation") or "").strip()
    credit = j.get("copyright")
    return [make_event(
        id=f"news:apod-{j['date']}",
        kind="news",
        title=f"Picture of the day: {j.get('title', '').strip()}",
        summary=((expl[:260].rsplit(' ', 1)[0] + '…') if len(expl) > 260 else expl) + (f" Image credit: {credit.strip()}." if credit else " Image: NASA APOD."),
        peak=when, start=when, end=when + timedelta(days=3),
        needs_dark=False,
        source_name="NASA APOD", source_url=j.get("url") or "https://apod.nasa.gov/apod/", confidence=0.9,
    )]


ALL_FETCHERS = {
    "showers": fetch_showers,
    "comets": fetch_comets,
    "aurora": fetch_aurora,
    "eclipses": fetch_eclipses,
    "news": fetch_news,
    "apod": fetch_apod,
}
