#!/usr/bin/env python3
"""
FIRESTORM fire-spread-forecast pipeline — mirrors the open PyreCast (Pyregence
Consortium) ELMFIRE fire-spread forecasts into a slim GeoJSON the FIRESTORM
frontend reads via raw.githubusercontent.com.

WHY THIS IS THE HEADLINE: FIRESTORM today answers "where is the fire NOW"
(FIRMS/VIIRS/GOES detections, NIFC perimeters). This answers the operator's
actual decision question — "where is it GOING?" — by overlaying the MODELED
spread extent for each active fire, with an uncertainty envelope (10th / 50th /
90th percentile of the ensemble). That is the leap from a situational-awareness
COP to a predictive decision-support tool.

WHAT WE PULL: data.pyrecast.org/fire_spread_forecast/<fire>/<run>/pyretec/
landfire/<pct>/isochrones_<fire>_<run>_<pct>.shp  — the modeled fire EXTENT
polygon at that ensemble percentile (UTM, per-fire zone). We take 10/50/90 as
core / likely / outer-cone. (PyreCast also publishes per-hour time-of-arrival
GeoTIFFs, but those need GDAL; the isochrone shapefile gives the spread extent
keylessly and is enough for the headline overlay.)

LICENSING (read carefully — flagged in FIRESTORM memory): PyreCast Terms of Use
(Section VI) EXPLICITLY permit "Public safety applications by emergency
management, fire service organizations, and government agencies" free of charge.
FIRESTORM (DOI) qualifies. We attribute PyreCast/Pyregence on every rendered
feature. Operator chose the agency-use reading for public display; written
redistribution permission is being pursued in parallel.

DEMO-DATA-HONESTY (the hard rule, doubly for a forecast): every polygon is
emitted with model=ELMFIRE, the run timestamp, and the percentile, so the
frontend badges it MODELED / PREDICTED with an uncertainty cone — never ground
truth.

OUTPUT: data/spread.json
Shape: { "generated_at": ISO8601, "source": "PyreCast / Pyregence ELMFIRE",
         "fires": [ { "fire": "ca-saddle", "run": "20260530_222800",
                      "label": "Saddle (CA)",
                      "rings": { "10": [[ [lng,lat],... ]], "50": [...], "90": [...] } }, ... ] }

Requires: pyshp, pyproj  (pure-Python wheels — installable in a GHA runner). No API key.
"""
from __future__ import annotations
import io
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone

import shapefile  # pyshp
import pyproj

ROOT = "https://data.pyrecast.org/fire_spread_forecast/"
PERCENTILES = ["10", "50", "90"]      # core / likely / outer uncertainty cone
OUT_PATH = os.path.join(os.path.dirname(__file__), "data", "spread.json")
UA = {"User-Agent": "firestorm-spread-data/1.0 (+github.com/Deasus; DOI wildfire COP, agency public-safety use)"}
# Coarsen polygons: these are smooth ELMFIRE contours; the frontend doesn't need
# sub-meter fidelity and big payloads hurt the single-file frontend. Cap verts
# per ring AND drop tiny slivers, to keep the whole feed well under ~400KB.
MAX_VERTS = 120        # per ring (a spread polygon reads fine at 120 pts)
MIN_RING_PTS = 8       # drop sub-sliver rings entirely


def _get(url: str) -> bytes:
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25).read()


def _list(url: str) -> list[str]:
    body = _get(url).decode("utf-8", "ignore")
    return [h for h in re.findall(r'href="([^"]+)"', body) if not h.startswith("..")]


def _utm_zone_from_prj(prj: str) -> str | None:
    """Extract EPSG from a WGS84 UTM .prj (e.g. 'UTM_Zone_10N' -> EPSG:32610)."""
    m = re.search(r"UTM[_ ]Zone[_ ](\d+)([NS])", prj, re.I)
    if not m:
        return None
    zone = int(m.group(1))
    hemi = m.group(2).upper()
    return f"EPSG:{(32600 if hemi == 'N' else 32700) + zone}"


def _coarsen(points: list, n: int) -> list:
    if len(points) <= n:
        return points
    step = max(1, len(points) // n)
    out = points[::step]
    if out[-1] != points[-1]:
        out.append(points[-1])
    return out


def _label_from_slug(slug: str) -> str:
    # "ca-saddle" -> "Saddle (CA)" ; "nm-seven-cabins" -> "Seven Cabins (NM)"
    parts = slug.split("-")
    if len(parts) >= 2 and len(parts[0]) == 2:
        st = parts[0].upper()
        name = " ".join(p.capitalize() for p in parts[1:])
        return f"{name} ({st})"
    return slug


def _runs_newest_first(fire: str) -> list[str]:
    runs = [d.rstrip("/") for d in _list(ROOT + fire + "/") if d.endswith("/")]
    runs = [r for r in runs if re.match(r"\d{8}_\d{6}", r)]
    return sorted(runs, reverse=True)


def _read_isochrone(fire: str, run: str, pct: str):
    # PyreCast publishes under two parallel subtrees depending on the fire:
    # <run>/pyretec/landfire/<pct>/  OR  <run>/elmfire/landfire/<pct>/ .
    # Some fires only have one of the two (e.g. ut-cottonwood only publishes
    # elmfire/). Try both so the layer doesn't silently drop those fires.
    stem = f"isochrones_{fire}_{run}_{pct}"
    shp = shx = dbf = prj = None
    last_err = None
    for sub in ("pyretec", "elmfire"):
        base = f"{ROOT}{fire}/{run}/{sub}/landfire/{pct}/"
        try:
            shp = io.BytesIO(_get(base + stem + ".shp"))
            shx = io.BytesIO(_get(base + stem + ".shx"))
            dbf = io.BytesIO(_get(base + stem + ".dbf"))
            prj = _get(base + stem + ".prj").decode("utf-8", "ignore")
            break
        except Exception as e:
            last_err = e
            shp = shx = dbf = prj = None
    if shp is None:
        print(f"  [{fire} {pct}] fetch failed: {last_err}", file=sys.stderr)
        return None
    epsg = _utm_zone_from_prj(prj)
    if not epsg:
        print(f"  [{fire} {pct}] could not parse UTM zone from prj", file=sys.stderr)
        return None
    tr = pyproj.Transformer.from_crs(epsg, "EPSG:4326", always_xy=True)
    r = shapefile.Reader(shp=shp, shx=shx, dbf=dbf)
    rings = []
    for shape in r.shapes():
        pts = shape.points
        parts = list(shape.parts) + [len(pts)]
        for i in range(len(parts) - 1):
            seg = pts[parts[i]:parts[i + 1]]
            if len(seg) < MIN_RING_PTS:
                continue                       # drop sub-sliver rings
            seg = _coarsen(seg, MAX_VERTS)
            ring = []
            for x, y in seg:
                lon, lat = tr.transform(x, y)
                ring.append([round(lon, 5), round(lat, 5)])
            if len(ring) >= 4:
                rings.append(ring)
    if not rings:
        return None
    # Keep only the largest few rings per percentile (by vertex count ≈ extent).
    # The dominant spread polygon(s) are what an operator reads as "predicted
    # extent"; dozens of tiny internal/spot rings just bloat the payload.
    rings.sort(key=len, reverse=True)
    return rings[:3]


def main() -> int:
    now = datetime.now(timezone.utc)
    try:
        fires = [d.rstrip("/") for d in _list(ROOT) if d.endswith("/")]
    except Exception as e:
        print(f"FATAL: cannot list PyreCast root: {e}", file=sys.stderr)
        return 1

    out_fires = []
    for fire in fires:
        try:
            # Newest run sometimes exists but is still computing (empty / 404s).
            # Walk runs newest-first and use the first one that actually has data.
            rings_by_pct, run = {}, None
            for candidate in _runs_newest_first(fire)[:4]:   # don't dig back forever
                rb = {}
                for pct in PERCENTILES:
                    rings = _read_isochrone(fire, candidate, pct)
                    if rings:
                        rb[pct] = rings
                if rb:
                    rings_by_pct, run = rb, candidate
                    break
            if not rings_by_pct or not run:
                continue
            # run dir is YYYYMMDD_HHMMSS UTC
            run_dt = datetime.strptime(run, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
            out_fires.append({
                "fire": fire,
                "label": _label_from_slug(fire),
                "run": run,
                "run_at": run_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "rings": rings_by_pct,
            })
            print(f"[{fire}] run {run} -> percentiles {sorted(rings_by_pct)} "
                  f"({sum(len(v) for v in rings_by_pct.values())} rings)")
        except Exception as e:
            print(f"[{fire}] ERROR: {e}", file=sys.stderr)

    payload = {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "PyreCast / Pyregence Consortium — ELMFIRE fire-spread forecast (MODELED, not observed)",
        "model": "ELMFIRE",
        "percentiles": PERCENTILES,
        "attribution": "Forecast data: PyreCast (Pyregence Consortium). Government public-safety use per PyreCast ToU §VI.",
        "count": len(out_fires),
        "fires": out_fires,
    }
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(payload, f, separators=(",", ":"))
    print(f"wrote {OUT_PATH}: {len(out_fires)} fire forecasts "
          f"({os.path.getsize(OUT_PATH)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
