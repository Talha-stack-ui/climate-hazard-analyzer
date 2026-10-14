# How it works: questions and answers

This page explains, in plain language, how the Climate Hazard Trend Analyzer is built, what each
tool is used for, and why. For setup steps see [README.md](README.md).

---

## The big picture

### What does the app do?
You pick a place (a city, a state, or a box drawn on a map) and a range of years. The app
downloads daily weather for that place, finds the heatwaves (or heavy-rain events) in it, and tells
you whether they have become more common, longer or more intense, and whether that change is
statistically real or just year-to-year noise.

### What happens when I press "Analyse"?
1. The **frontend** (React) sends one request to the **backend**:
   `GET /api/analysis/?lat=28.61&lon=77.21&start_year=1990&end_year=2020`
2. The backend **checks the input** (are the numbers valid and within limits?).
3. It **checks the rate limit** for your IP address.
4. It **looks in the SQLite database** for a finished analysis of the same place and settings. If there is one,
   it returns it straight away (about 10 ms).
5. If not, it looks in the database for the **raw daily weather**. Only the missing years are downloaded
   from Open-Meteo, and then they are stored so they are never downloaded again.
6. It **cleans** the data, **detects events**, computes **yearly numbers** and **trends**, and writes
   a **plain-English summary**.
7. The result is **saved to the database** and sent back as JSON. The frontend draws the charts and tables.

### How is the project organised?
```
backend/            Python API (Django)
  config/           settings, URL root
  api/
    providers.py    talks to the outside APIs (Open-Meteo, Nominatim)
    weather.py      turns a region into grid points, caches daily weather in the database
    cleaning.py     fixes or removes bad data
    hazards.py      heatwave / heavy-rain definitions and event detection
    trends.py       statistics (trend lines, significance tests)
    analysis.py     runs the whole pipeline and writes the summary text
    serializers.py  checks user input
    views.py        the API endpoints and rate limits
    models.py       the database tables (SQLite)
    exceptions.py   error types and clean JSON error responses
    management/commands/prefetch.py   pre-downloads popular cities
    tests/          automated tests
frontend/           React app (Vite)
  src/App.jsx                 page layout, form, calls the API
  src/components/RegionPicker.jsx   place search + coordinate inputs
  src/components/RegionMap.jsx      the Leaflet map
  src/components/Results.jsx        headline, tiles, tables, CSV/PDF export
  src/components/charts.jsx         the Chart.js charts
  src/api/client.js           one helper for calling the API
render.yaml         deployment recipe for Render
```

---

## What is used for what

| Tool | Used for | Why this one |
|---|---|---|
| **Django** | Web framework for the backend | Mature, secure defaults, was already the project skeleton |
| **Django REST Framework (DRF)** | API views, input validation, rate limiting | Validation (serializers) and throttling are built in |
| **django-cors-headers** | Lets the React site call the API from another domain | Restricts which websites may call it |
| **SQLite** (via the **Django ORM**) | Stores weather data, analyses, search results, locks | One file, nothing to install, built into Python |
| **requests** | Calling Open-Meteo and Nominatim | Simple HTTP with timeouts and automatic retries |
| **numpy** | Fast maths on long daily series | 75 years × 365 days is easy for numpy |
| **scipy** | Regression and Sen's slope | Trusted, well-tested statistics |
| **python-dotenv** | Reads settings from `.env` | Keeps secrets and config out of the code |
| **gunicorn** | Runs the backend in production | Standard Python web server |
| **React** | The user interface | Component-based, was already the project skeleton |
| **Vite** | Dev server and production build for React | Fast, simple |
| **Leaflet / react-leaflet** | The interactive map | Free, no API key, OpenStreetMap tiles |
| **Chart.js / react-chartjs-2** | The charts | Interactive tooltips, small and simple |
| **Open-Meteo Historical API** | Daily weather data (ERA5) | Free, no key, covers the whole world from 1940 |
| **Nominatim (OpenStreetMap)** | Turning "Rajasthan" into coordinates and a bounding box | Free, returns boxes for states, not just points |
| **Render** | Free hosting | Free tier for both the API and the static site |

### Why SQLite?
It is a database in a single file: no server to install or pay for, and Django supports it out of
the box. The app has no users, logins or admin pages, so Django's auth, sessions and admin were
removed; only the app's own four tables exist.

The brief asks for a NoSQL database, so this is a deliberate trade-off (stated in the README). The
data is still stored "document-style": each weather row holds a whole year of daily values as JSON,
and each analysis is one JSON blob. Switching to MongoDB later would only touch `models.py` and a
few queries.

### Can SQLite cope with several requests at once?
Yes, for this app. It runs in **WAL mode**, so reading never waits for writing. Only one write
happens at a time; other writers wait up to 20 seconds instead of failing. Writes are rare (only
when new data is downloaded), so this is not a bottleneck on one server.

### What tables are there?
| Table | Holds | Kept for |
|---|---|---|
| `DailyWeather` | One grid cell × one year of daily max temperature and rain | Forever |
| `Analysis` | A finished analysis result (JSON) | Reused for 30 days, then recomputed |
| `GeocodeCache` | Place search results | 30 days |
| `FetchLock` | "Someone is downloading this region right now" | 90 seconds at most |

---

## Data

### Where does the weather data come from?
From the **Open-Meteo Historical Weather API**, which serves **ERA5**. ERA5 is a "reanalysis": a
weather model that is continuously corrected with real observations, giving a complete, gap-free
record for every ~25 km square on Earth. We use daily **maximum temperature** (°C) and daily
**precipitation** (mm), from 1950 to the last complete year.

### Why not weather-station data?
Stations have gaps, move, change instruments, and are unevenly spread. ERA5 is consistent
everywhere, which matters for trends. The trade-off is that it smooths very local extremes (for
example city heat islands), and the app says so in its caveats.

### How is a region turned into data?
- **A point:** snapped to the nearest 0.25° grid cell (the ERA5 grid spacing).
- **A box:** sampled at up to 4 grid cells (2 × 2). Their daily values are averaged into one
  regional series.
- Snapping means nearby clicks share the same cached data.

### How is messy data handled?
| Problem | What the app does |
|---|---|
| Wrong units | Asks for °C and mm explicitly, checks the units in the reply, converts °F, K or inches, rejects anything else |
| Impossible values | Below −90 °C, above 60 °C, negative rain or over 2000 mm/day → treated as missing |
| One-day spikes (glitches) | Removed only if far from normal for that time of year **and** very different from the day before and after. Real extremes last several days, so they are kept. |
| Short gaps (1–2 days) | Temperature is filled in by drawing a straight line between the neighbours. Rain is never filled in (inventing rain is worse than a gap). |
| Long gaps | Left missing. A missing day **ends** an event, so a gap is never assumed to be hot. |
| Years with little data | Years with under 90% valid days are shown but left out of the trend maths |
| Unfinished years | Only complete calendar years are used and cached |

Every count (values removed, gaps filled, years excluded) is shown in the app under "Data".

---

## Climate logic

### What counts as a heatwave?
**3 or more days in a row with a daily maximum temperature above the 95th percentile.** The 95th
percentile is the temperature that only the hottest 5% of days reach. It is calculated for that
location from the **1981–2010** period.

### Why a fixed 1981–2010 period?
So every analysis is measured against the same yardstick. If the threshold were calculated from
the years you selected, choosing a different period would move the threshold and make periods hard
to compare. 1981–2010 is a standard WMO "climate normal" period.

### What counts as heavy rainfall?
A day with rain above the **99th percentile of wet days** (days with at least 1 mm) in 1981–2010.
Back-to-back heavy days are merged into one event.

### Can the definition be changed?
Yes, under "Definition" in the form: the percentile (80–99.9) and the minimum number of days (1–10).

### What is measured each year?
| Number | Meaning |
|---|---|
| Events | How many heatwaves started that year |
| Hazard days | Total days spent in a heatwave |
| Longest event | Length of the longest heatwave, in days |
| Mean excess | On heatwave days, how far above the threshold it was on average (intensity) |
| Hottest day | The year's highest maximum temperature (does not depend on the threshold) |

---

## Trends and the summary

### How does the app decide whether something "increased"?
It uses three methods:
- **Mann-Kendall test:** decides if there is a real upward or downward trend (significant if
  p < 0.05). It is used for the headline because it does not assume a bell-shaped distribution,
  and heatwave counts are usually lopsided (many years with few or none).
- **Linear regression (OLS):** gives "how much per decade" with a 95% confidence range. This is
  the grey line on the charts.
- **Sen's slope:** a robust "how much per decade" that ignores outlier years.

A **5-year rolling average** (orange line) smooths the main chart so the long-term shape is easier
to see.

### How is the headline written?
It compares the average of the **first 10 years** with the **last 10 years** (more stable than
comparing two single years). It only says "increased" or "decreased" when the Mann-Kendall test
is significant. Otherwise it says "no statistically significant trend" and explains that the
difference is within normal year-to-year variation.

### Why be so careful?
A tool that turns noise into "heatwaves fell 46%" would be misleading. For example:
- **New Delhi, 1990–2020:** no significant trend, and the app says so.
- **Madrid, 1970–2024:** a clear, significant rise (about +5 heatwave days per decade), and the app
  says that too.

### What are the caveats shown under "Read with care"?
They are added automatically when they apply: fewer than 30 years of data, excluded years, a box
averaged from several points, ERA5's ~25 km resolution, and the fact that the tests treat each year
as independent.

---

## Speed and caching

### Why is the first analysis of a place slow, and later ones fast?
The first time, decades of daily data must be downloaded (a few seconds). After that:

| Layer | What is stored | How long |
|---|---|---|
| Weather cache (`DailyWeather` table) | Raw daily values, one row per grid cell per year | Forever: past weather doesn't change |
| Analysis cache (`Analysis` table) | The finished result | 30 days |
| Browser/CDN cache | The API response (`Cache-Control`) | 1 hour |

Asking for a different period at a known place downloads only the missing years.

### What if two people ask for the same new place at the same time?
A short **lock** row in the database (`FetchLock` table) lets the first request download. The second
waits and then reads from the cache, so the data is only downloaded once.

### What is `manage.py prefetch` for?
It pre-downloads popular cities so visitors get instant answers. It waits automatically when the
data provider says "slow down".

---

## Security and rate limiting

### What limits are there?
Per visitor IP address:

| Limit | Applies to | Reason |
|---|---|---|
| 120 per minute | every endpoint | general safety ceiling |
| 30 per minute | analysis | the endpoint that does the most work |
| 20 per minute | place search | protects the free geocoding service |
| 10 per hour | **only requests that need new downloads** | stops one person using up the shared Open-Meteo quota; cached places are not counted |

Going over a limit returns **HTTP 429** with a `Retry-After` header saying when to try again.

### Why does the app also limit itself?
- **Open-Meteo** counts big requests as many calls: about one call per 2 weeks of data per
  location, so one 40-year download uses about 1,000 calls, against free limits of 600 per minute
  and 10,000 per day. If Open-Meteo says "too many requests", the app shows "try again later" and
  stops calling it until the wait is over (a "circuit breaker").
- **Nominatim** asks for at most 1 request per second, which the app enforces.

### How is user input kept safe?
- Every parameter is checked before use: numbers must be real and in range, boxes at most 15° per
  side, years between 1950 and last year with at least 10 years, hazard names from a fixed list,
  place names limited to letters, numbers and basic punctuation (max 100 characters).
- All database access goes through the Django ORM, which uses parameterised queries, so SQL
  injection doesn't work.
- Errors come back as short JSON messages, never stack traces.
- Only GET requests from the configured website are allowed (CORS).
- In production: HTTPS only (HSTS) and correct visitor IPs behind Render's proxy.

### Are there any secrets?
Only `DJANGO_SECRET_KEY`. It lives in `.env` (ignored by git)
or in Render's settings, never in code. No weather or map API keys are needed.

---

## The API

| Endpoint | What it returns |
|---|---|
| `GET /api/analysis/?...` | The full analysis (see below) |
| `GET /api/geocode/?q=Rajasthan` | Matching places, each with a suggested point or box |
| `GET /api/meta/` | Hazard list, defaults, year range and limits (the frontend reads these instead of hard-coding them) |
| `GET /api/health/` | `{"status": "ok"}` if the API and database are up |

Analysis parameters: `lat` + `lon`, **or** `south` + `north` + `west` + `east`; `start_year`,
`end_year`; optional `hazard` (`heatwave` default, or `heavy_rain`), `percentile`, `min_duration`.

The analysis response contains: `definition` (including the computed threshold), `data_quality`,
`yearly` (one row per year), `trends` (per metric), `events` (the 15 longest), `insights`
(headline, details, caveats) and `cached` (true if served from the database).

---

## The frontend

### How do I choose a region?
- **Search** a city or state and pick a result. States become a box, cities a point.
- **Point mode:** click the map.
- **Box mode:** tick "Drag on the map to draw a box", then drag.
- Or type exact coordinates.

### What does each chart show?
- **Heatwave days per year:** blue bars = days each year, orange = 5-year average, grey = trend line.
- **Small charts:** events per year, longest event, mean excess (intensity) and hottest day, each
  with its trend line.
- **Tables:** trend statistics for every number, the longest events, and the full yearly data.

### How do exports work?
- **Export CSV:** built in the browser from the data already on screen (yearly table plus a few
  header lines describing the definition and headline). No extra server request.
- **Save as PDF:** opens the browser's print dialog with a print layout that hides the controls;
  choose "Save as PDF".

### Does it work on phones and in dark mode?
Yes. The layout stacks into one column on small screens, wide tables scroll inside their cards,
and colours switch automatically with the system's dark mode.

---

## Testing and running

### How is it tested?
`python manage.py test` runs 25 automated tests with no internet needed (the weather provider is
replaced by fake data, and Django creates a temporary test database). They cover data cleaning, event detection,
statistics, input validation, caching, every rate limit, and the circuit breaker. The app was also
checked by hand against live data (Delhi, Rajasthan, Madrid).

### I get `ModuleNotFoundError: No module named 'scipy'`. Why?
The server was started with the wrong Python. Activate the project's virtual environment first
(`venv\Scripts\activate` inside `backend`) or pick `backend\venv\Scripts\python.exe` as the
interpreter in VS Code.

### How is it deployed?
On Render, using `render.yaml`. The API runs with gunicorn as one process with 8 threads, so the
rate-limit counters (kept in memory) are shared. `migrate` runs on every start to create the
SQLite tables. The frontend is a static site.

Note: Render's free plan has no permanent disk, so the SQLite file is wiped on every deploy or
restart. The app still works, but cached data has to be downloaded again. A paid Render disk
plus `SQLITE_PATH` keeps it.

---

## Limits and next steps

### What are the known limitations?
- ERA5 is smoothed, so local extremes are underestimated.
- A large box is represented by only 4 grid points.
- Calendar years are used, so southern-hemisphere summers are split across two years.
- One yearly threshold means heatwaves are summer events; unusual winter warmth isn't counted.
- Rate-limit counters are in memory, so they reset on restart and aren't shared between servers.
- SQLite allows one writer at a time and lives on one machine; several servers would need
  PostgreSQL or MongoDB.

### What would come next?
Season-aware thresholds, night-time and humid-heat heatwaves, drought as a third hazard, averaging
over every grid cell in a region, better statistics for year-to-year correlation, Redis for shared
rate limits, background downloads with a progress bar, and a check against real station data.
