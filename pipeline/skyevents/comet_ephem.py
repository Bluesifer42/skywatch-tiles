"""Two-body comet ephemeris from Minor Planet Center orbital elements.

Good to a few arcminutes over months for the question we ask: how bright
and how far from the Sun a comet will be. Not for pointing a telescope.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

K_GAUSS = 0.01720209895  # rad/day for 1 AU
OBLIQUITY_DEG = 23.4393


@dataclass
class CometElements:
    designation: str
    name: str
    perihelion_time: datetime  # TT ~ UTC for our purposes
    q_au: float
    e: float
    arg_peri_deg: float
    node_deg: float
    incl_deg: float
    h_mag: float
    slope: float  # n in m = H + 5 log Δ + 2.5 n log r


@dataclass
class CometState:
    r_au: float
    delta_au: float
    elongation_deg: float
    ra_deg: float
    dec_deg: float
    magnitude: float


def _rev(deg: float) -> float:
    return deg % 360.0


def jd(t: datetime) -> float:
    t = t.astimezone(timezone.utc)
    y, m = t.year, t.month
    d = t.day + (t.hour + t.minute / 60 + t.second / 3600) / 24
    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    return int(365.25 * (y + 4716)) + int(30.6001 * (m + 1)) + d + b - 1524.5


def sun_geocentric(t: datetime) -> tuple[float, float, float]:
    """Schlyter low-precision Sun: geocentric ecliptic rectangular, AU."""
    d = jd(t) - 2451543.5
    w = _rev(282.9404 + 4.70935e-5 * d)
    e = 0.016709 - 1.151e-9 * d
    m = math.radians(_rev(356.0470 + 0.9856002585 * d))
    ea = m + e * math.sin(m) * (1 + e * math.cos(m))
    xv = math.cos(ea) - e
    yv = math.sqrt(1 - e * e) * math.sin(ea)
    v = math.atan2(yv, xv)
    r = math.hypot(xv, yv)
    lon = v + math.radians(w)
    return r * math.cos(lon), r * math.sin(lon), 0.0


def _true_anomaly_and_r(el: CometElements, t: datetime) -> tuple[float, float]:
    dt = jd(t) - jd(el.perihelion_time)
    q, e = el.q_au, el.e
    if abs(e - 1) < 1e-6:
        # Parabolic: Barker's equation.
        w = 3 * K_GAUSS / (math.sqrt(2) * q**1.5) * dt
        s = 0.0
        for _ in range(60):
            f = s**3 + 3 * s - w
            s -= f / (3 * s * s + 3)
        nu = 2 * math.atan(s)
        return nu, q * (1 + s * s)
    if e < 1:
        a = q / (1 - e)
        n = K_GAUSS / a**1.5
        m = (n * dt) % (2 * math.pi)
        ea = m if e < 0.8 else math.pi
        for _ in range(100):
            ea -= (ea - e * math.sin(ea) - m) / (1 - e * math.cos(ea))
        nu = 2 * math.atan2(math.sqrt(1 + e) * math.sin(ea / 2), math.sqrt(1 - e) * math.cos(ea / 2))
        return nu, a * (1 - e * math.cos(ea))
    a = q / (e - 1)
    n = K_GAUSS / a**1.5
    m = n * dt
    h = math.asinh(m / e) if e > 1.5 else m
    for _ in range(100):
        h -= (e * math.sinh(h) - h - m) / (e * math.cosh(h) - 1)
    nu = 2 * math.atan(math.sqrt((e + 1) / (e - 1)) * math.tanh(h / 2))
    return nu, a * (e * math.cosh(h) - 1)


def state_at(el: CometElements, t: datetime) -> CometState:
    nu, r = _true_anomaly_and_r(el, t)
    w = math.radians(el.arg_peri_deg)
    om = math.radians(el.node_deg)
    i = math.radians(el.incl_deg)
    u = nu + w
    xh = r * (math.cos(om) * math.cos(u) - math.sin(om) * math.sin(u) * math.cos(i))
    yh = r * (math.sin(om) * math.cos(u) + math.cos(om) * math.sin(u) * math.cos(i))
    zh = r * math.sin(u) * math.sin(i)
    xs, ys, zs = sun_geocentric(t)
    xg, yg, zg = xh + xs, yh + ys, zh + zs
    delta = math.sqrt(xg * xg + yg * yg + zg * zg)
    rs = math.hypot(xs, ys)
    cos_el = (rs * rs + delta * delta - r * r) / (2 * rs * delta)
    elong = math.degrees(math.acos(max(-1.0, min(1.0, cos_el))))
    eps = math.radians(OBLIQUITY_DEG)
    xe, ye, ze = xg, yg * math.cos(eps) - zg * math.sin(eps), yg * math.sin(eps) + zg * math.cos(eps)
    ra = _rev(math.degrees(math.atan2(ye, xe)))
    dec = math.degrees(math.atan2(ze, math.hypot(xe, ye)))
    mag = el.h_mag + 5 * math.log10(delta) + 2.5 * el.slope * math.log10(r)
    return CometState(r_au=r, delta_au=delta, elongation_deg=elong, ra_deg=ra, dec_deg=dec, magnitude=mag)


def parse_mpc_line(line: str) -> CometElements | None:
    """Fixed-column MPC comet orbit format (CometEls.txt / Soft00Cmt.txt)."""
    if len(line) < 100 or line.startswith("#"):
        return None
    try:
        year = int(line[14:18])
        month = int(line[19:21])
        day = float(line[22:29])
        q = float(line[30:39])
        e = float(line[41:49])
        peri = float(line[51:59])
        node = float(line[61:69])
        incl = float(line[71:79])
        h = float(line[91:95])
        g = float(line[96:100])
        name = line[102:158].strip()
    except ValueError:
        return None
    dday = int(day)
    frac = day - dday
    t = datetime(year, month, max(1, dday), tzinfo=timezone.utc)
    from datetime import timedelta

    t += timedelta(days=frac)
    # Designation is the first token(s) of the name field, e.g. "C/2023 A3 (Tsuchinshan-ATLAS)".
    desig = name.split("(")[0].strip() if "(" in name else name.split()[0] if name else line[:12].strip()
    return CometElements(designation=desig, name=name, perihelion_time=t, q_au=q, e=e, arg_peri_deg=peri, node_deg=node, incl_deg=incl, h_mag=h, slope=g)
