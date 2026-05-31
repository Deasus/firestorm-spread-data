# firestorm-spread-data

**Predicted fire spread** for [FIRESTORM](https://github.com/Deasus/Firestorm) — the leap from
"where is the fire **now**" to "where is it **going**." Mirrors the open **PyreCast (Pyregence
Consortium) ELMFIRE** fire-spread forecasts into a slim GeoJSON the single-file frontend reads.

## What it gives FIRESTORM

For each active fire PyreCast models, a **predicted spread extent** with an uncertainty envelope —
the **10th / 50th / 90th** ensemble percentiles (core / likely / outer cone). Rendered as a MODELED
overlay so an operator can see the projected footprint, not just current heat.

## Source + licensing

`data.pyrecast.org/fire_spread_forecast/<fire>/<run>/pyretec/landfire/<pct>/isochrones_*.shp`
(UTM per-fire zone; reprojected to WGS84 here).

**Licensing — important:** PyreCast Terms §VI explicitly permit *"Public safety applications by
emergency management, fire service organizations, and government agencies"* free of charge. FIRESTORM
(DOI) qualifies. Every rendered feature is attributed to PyreCast/Pyregence. Operator authorized the
agency-use reading for display; written redistribution permission is being pursued in parallel.

**Demo-data honesty:** every polygon carries `model=ELMFIRE` + run timestamp + percentile so the
frontend badges it **MODELED / PREDICTED** with an uncertainty cone — never ground truth. A forecast is
decision-support; accuracy is bounded by inputs (wind-dominant).

## Output — `data/spread.json`
```jsonc
{ "generated_at":"...", "model":"ELMFIRE", "percentiles":["10","50","90"],
  "fires":[ {"fire":"ca-saddle","label":"Saddle (CA)","run_at":"...","rings":{"10":[[ [lng,lat],... ]],"50":[...],"90":[...]}} ] }
```
Frontend reads `raw.githubusercontent.com/Deasus/firestorm-spread-data/main/data/spread.json`.

## Run locally
```bash
pip install pyshp pyproj      # pure-Python, no GDAL
python fetch_spread.py
```
Walks runs newest-first per fire (the very newest is sometimes still computing/empty → falls back).
Keeps the largest rings per percentile, coarsened, to keep the payload ~200KB. 20-min GHA cron.
