# Climate Hazard Trend Analyzer

Pick a region (city, state or a lat-lon box) and a period. The app downloads decades of daily
weather, detects **heatwaves** (default) or **heavy-rainfall** events with a percentile-based
definition, and shows whether their frequency, duration and intensity have changed, with
significance tests and the caveats needed to read the result honestly.

```
backend/   Django REST API (Python 3.11): ingestion, cleaning, hazard logic, trends, SQLite
frontend/  React (Vite): Leaflet map, Chart.js charts, CSV / PDF export
render.yaml  one-click deploy of both to Render
```

- How it is built, in plain Q&A form: [HOW_IT_WORKS.md](HOW_IT_WORKS.md)
- Live app: https://climate-web.onrender.com (API: https://climate-api-qw15.onrender.com/api/health/). Free tier: the first request after a quiet period can take ~1 minute while the API wakes up.
- Demo video: _optional_

## Setup

Needs Python 3.11 and Node 20+. The database is a SQLite file (`backend/db.sqlite3`) created by
`migrate`; nothing else to install.

```powershell
cd backend
py -3.11 -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env          # set DJANGO_SECRET_KEY
python manage.py migrate        # creates db.sqlite3
python manage.py test           # 25 tests, no network needed
python manage.py runserver      # http://127.0.0.1:8000/api/health/
python manage.py prefetch       # optional: pre-download popular cities
```

```powershell
cd frontend
npm install
copy .env.example .env          # VITE_API_URL=http://127.0.0.1:8000/api
npm run dev                     # http://localhost:5173
```

| Backend variable | Meaning |
|---|---|
| `DJANGO_SECRET_KEY` | Required |
| `DJANGO_DEBUG` | `True` locally; `False` in production (enables HTTPS redirect and HSTS) |
| `DJANGO_ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS` | Comma-separated |
| `SQLITE_PATH` | Optional location of the SQLite file (default `backend/db.sqlite3`) |
| `GEOCODER_USER_AGENT` | Identifies the app to Nominatim (their policy requires it) |
| `NUM_PROXIES` | Reverse proxies in front of the app, for the real client IP (Render: 1) |

### Deploy (free tiers)

1. On Render: **New > Blueprint** and pick this repo. `render.yaml` creates the API and the static site.
   Fill in `CORS_ALLOWED_ORIGINS` (the site URL), `VITE_API_URL` (API URL + `/api`) and
   `GEOCODER_USER_AGENT`. The API runs `migrate` on every start.
2. The free plan has no persistent disk, so the SQLite cache is empty after each deploy or restart
   (the app still works; first requests per place are just slower). To keep it, attach a Render
   disk and set `SQLITE_PATH` to a file on it.
3. Optional: run `python manage.py prefetch` once from the Render shell so demo cities load instantly.

## Architecture

```
React (Vite) ──GET──▶ Django REST API ──▶ SQLite (api/models.py)
 map, charts,          │ validate (serializers)     DailyWeather  (cell × year, permanent)
 CSV/PDF export        │ rate-limit (per IP)        Analysis      (results, 30 days)
                       │ analysis cached? ──yes──▶  GeocodeCache  (30 days)
                       │   no: missing cell-years?  FetchLock     (single-flight, expiring)
                       │       └─▶ Open-Meteo (one multi-point call, missing years only)
                       └─ clean → regional mean → threshold → events → yearly metrics → trends → insights
```

| Module (`backend/api/`) | Responsibility |
|---|---|
| `providers.py` | Outbound HTTP: timeouts, retries with backoff on 5xx, 429 circuit breaker, unit conversion, payload validation, Nominatim 1 req/s gate |
| `models.py` | SQLite tables (weather cache, analyses, geocoding cache, locks) |
| `weather.py` | Region → grid points, cache per (0.25° cell, year), fetches only missing years, cross-process fetch lock |
| `cleaning.py` | Quality control (see below) |
| `hazards.py` | Hazard definitions, event detection, yearly metrics |
| `trends.py` | OLS, Mann-Kendall, Sen's slope, rolling mean |
| `analysis.py` | Pipeline, result storage, plain-language insights |
| `serializers.py` / `views.py` | Input validation, endpoints, throttles |

The API is read-only for clients: no sessions, auth or admin. SQLite (through the Django ORM) is
the only store. It runs in WAL mode, so reads continue while one thread writes, and writers wait
for the lock (20 s timeout) instead of failing.

### API

| Endpoint | Purpose |
|---|---|
| `GET /api/analysis/?hazard=heatwave&lat=28.61&lon=77.21&start_year=1990&end_year=2020` | Full analysis (or `south,north,west,east` for a box; optional `percentile`, `min_duration`) |
| `GET /api/geocode/?q=Rajasthan` | Place search; each hit has a suggested `region` (states as boxes, cities as points) |
| `GET /api/meta/` | Hazards, defaults, year range and limits, so the frontend hard-codes none of them |
| `GET /api/health/` | API + database check |

The analysis response holds `definition` (with the computed threshold), `data_quality`,
`yearly` (metrics, 5-year means, coverage), `trends` (per metric), `events` (longest 15),
`insights` (headline, details, caveats) and `cached`.

## Data source

**Open-Meteo Historical Weather API**: ECMWF **ERA5** reanalysis, about 25 km grid, daily from
1940; this app uses 1950 onward. It is free, needs no key, and gives one consistent gridded dataset
for any point on Earth. Station records (NOAA GHCN) would be closer to local reality but have
gaps, station moves and uneven coverage. Variables: `temperature_2m_max` (°C) and
`precipitation_sum` (mm), with units requested explicitly and checked in the response.
Geocoding: **Nominatim** (OpenStreetMap).

## Hazard logic

| | Heatwave (default) | Heavy rainfall |
|---|---|---|
| Daily variable | max temperature | precipitation |
| Threshold | 95th percentile of all daily maxima, **1981–2010** | 99th percentile of wet days (≥ 1 mm), 1981–2010 |
| Event | ≥ 3 consecutive days above threshold | ≥ 1 day above threshold (consecutive days merge) |

Percentile and minimum duration are adjustable (80–99.9, 1–10 days). The threshold comes from the
fixed WMO 1981–2010 normal, not the selected period, so different periods are measured against
the same yardstick. Those baseline years are fetched even when the selected period doesn't
include them.

Yearly metrics: event count, hazard days, longest event, mean excess over threshold (intensity),
and the year's maximum (hottest/wettest day). The last one does not depend on the threshold.

**Trends**, per metric:
- **Mann-Kendall** decides significance (5%). It is non-parametric, so it suits zero-heavy,
  skewed counts.
- **OLS** gives the slope per decade with a 95% CI, and is the line drawn on the charts.
- **Sen's slope** is a robust estimate of the size of the trend.
- A **5-year centred rolling mean** smooths the main chart.

**Insights** are generated from these numbers. The headline compares the mean of the first and
last 10 years (more robust than endpoints) and only says "increased/decreased" when Mann-Kendall
is significant. Otherwise it says there is no significant trend, and that the difference is within
year-to-year variability. Caveats are added automatically: short record, excluded years, area
averaging, reanalysis resolution, autocorrelation.

Example: New Delhi 1990–2020 gives "No statistically significant trend in heatwave days
(p = 0.10)", with an OLS slope of −2.9 days/decade. A naive tool would print "heatwaves decreased
46%". The flat or negative pre-monsoon trend in this region matches published work (aerosols,
irrigation), and the app does not overclaim it.

## Data handling (noisy, real-world data)

| Problem | Handling |
|---|---|
| Units | Request °C/mm explicitly, read `daily_units`, convert °F/K/inch, reject anything unknown |
| Impossible values | Outside −90…60 °C or 0…2000 mm/day → missing |
| Outliers | Temperature only: flagged when > 6 robust SD (median/MAD) from the same time of year **and** > 10 °C from both neighbours. Real extremes persist over days and are kept; Delhi's record-cold 19 May 2021 (Cyclone Tauktae) survives, which a z-score alone removed. |
| Gaps | Temperature gaps ≤ 2 days interpolated linearly. Longer gaps stay missing and **break** events (a gap is never assumed hot). Precipitation is never interpolated. |
| Incomplete years | Years with < 90% valid days are shown but excluded from trends |
| Incomplete provider data | Only complete calendar years are cached. The latest year is used from mid-January (ERA5 lags about a week). |
| Regions | Point: nearest 0.25° cell. Box: up to a 2×2 grid of cells; a day counts if at least half the points have data. |

All counts are returned in `data_quality` and shown in the UI.

## Security and performance

**Caching (3 layers).**
1. Raw weather is stored once per (cell, year) and never re-downloaded, because history doesn't
   change. A new period at a known place downloads only the missing years.
2. Finished analyses are stored in SQLite and reused for 30 days. The key comes from the snapped
   grid points, so nearby clicks share results.
3. `Cache-Control: public, max-age=3600` lets browsers and CDNs reuse responses.

A lock row in SQLite makes concurrent requests for the same region wait for one download (no
thundering herd). Tested live: three simultaneous requests for a new place caused one download.

**Rate limiting (DRF throttles, per client IP).**

| Scope | Limit | Why |
|---|---|---|
| global | 120/min | ceiling for any endpoint |
| analysis | 30/min | compute endpoint |
| geocode | 20/min | protects the Nominatim quota (we also keep their 1 req/s rule server-side) |
| upstream | 10/hour | **charged only when a request needs new downloads.** Cached regions are free, but one client cannot drain the shared Open-Meteo quota by sweeping coordinates. |

Throttled responses are `429` with `Retry-After`.

**Upstream limits.** Open-Meteo weights calls by data volume (about 1 call per 2 weeks of data per
location), so a 40-year point costs about 1,000 of the free 600/min, 5,000/hour and 10,000/day.
On a provider `429` the API returns `503 + Retry-After`, and a circuit breaker stops further calls
until the window passes. `manage.py prefetch` pre-warms popular places.

**Input safety.**
- Every parameter is typed and bounded by DRF serializers: lat/lon ranges, finite numbers, box
  ≤ 15° per side, years 1950–last complete year with ≥ 10 years, hazard from a whitelist,
  place-name character whitelist of ≤ 100 chars.
- Database access goes through the Django ORM (parameterised queries), so no SQL injection.
- Errors are JSON with no stack traces.
- CORS allows only GET from configured origins.
- With `DEBUG=False`: HTTPS redirect, HSTS, and a correct client IP behind proxies (`NUM_PROXIES`).

## Assumptions

- **Storage is SQLite, not NoSQL.** The brief asks for a NoSQL store. I chose SQLite for zero setup
  and a single file. The schema is still document-like (each weather row holds a year of daily
  values as JSON; each analysis is one JSON document), so moving to MongoDB or DynamoDB later only
  touches `models.py` and the few queries in `weather.py`, `analysis.py` and `views.py`. SQLite
  allows one writer at a time, which is fine for this read-heavy app on one server.

- ERA5 reanalysis stands in for observations. It smooths local extremes (urban heat islands,
  convective downpours), so absolute counts are conservative; trends are more reliable than levels.
- Years are calendar years, and events count toward the year they start. In the southern
  hemisphere a summer spans two calendar years.
- One annual percentile (the brief's definition) means heatwaves are warm-season events. A
  calendar-day percentile would also catch unusual winter warmth.
- A box is represented by at most 4 grid points, which is a coarse sample for a large state.
- The rainfall threshold needs at least 100 wet baseline days. Very dry places return a clear
  422 instead of a meaningless threshold.
- Trend tests assume independent years. No pre-whitening is applied, so serial correlation can
  overstate significance (stated in the UI).

## What I would improve with more time

- Calendar-day percentile thresholds (15-day window) and the Excess Heat Factor; drought (SPI)
  as a third hazard.
- Tmin / humid-heat (wet-bulb) heatwaves, which matter more for health than Tmax alone.
- Area-weighted means over all grid cells in a box (or admin-boundary polygons) instead of 4 samples.
- Mann-Kendall with pre-whitening (Hamed–Rao), field-significance across metrics, and change-point
  detection.
- PostgreSQL or MongoDB instead of SQLite for several servers; Redis for shared throttle counters; a background job queue for
  cold downloads with progress shown in the UI; a paid or self-hosted Open-Meteo for quota.
- A choropleth map of trends across a region, and server-rendered PDF reports with charts.
- Validation against station data (GHCN-Daily) for a few cities.

## AI tools used

_Edit this section to describe your own use._ Claude Code (Anthropic) was used as a pair
programmer to scaffold the Django/React structure, draft the cleaning, hazard and trend
modules and their tests, and write this README. Every module was reviewed, run against live
data, and adjusted by hand. For example, the outlier rule was changed after a live check showed
it removed a genuine record-cold day in Delhi, and the Open-Meteo quota handling was added after
hitting its per-minute limit in testing.
