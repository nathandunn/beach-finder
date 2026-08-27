# Beach Finder

Rank the beaches near you by how good conditions are *right now* — anywhere
in the world, not just Oregon. Grant location access, get a scored,
ranked list. Deny it, and drop in coordinates (or pick an example city)
instead.

Beach locations come from OpenStreetMap (Overpass API); weather and wave
data come from Open-Meteo. Both are free and keyless. This is a separate
app from the Oregon Beach App (`apps/beach`) — same idea, no shared code,
global instead of Oregon-scoped, no database.

## How it works

1. The browser asks for your location (or you type one in).
2. The backend searches OpenStreetMap for `natural=beach` points outward in
   growing radius bands (8 km → 16 → 32 → 64 → 128 → 256 → 400 → 500 mi),
   accumulating results and stopping as soon as ~25 beaches are found, or
   at the 500-mile ceiling — whichever comes first.
3. For each beach found, the backend fetches current weather (and wave
   height, where available) from Open-Meteo and computes a 0–100 score.
4. Beaches are ranked by score and returned as JSON; the frontend renders
   them as a list with a visual score meter.

## Scoring formula

Five factors, each worth a fixed number of points, summed to 0–100:

| Factor          | Max points | Better direction        |
|-----------------|-----------:|--------------------------|
| Temperature     | 30         | warmer, up to a plateau  |
| Wind speed      | 25         | calmer                   |
| Precipitation   | 20         | drier                    |
| Cloud cover     | 15         | clearer                  |
| Wave height     | 10         | calmer surf              |

That totals 100 when wave data is available. Open-Meteo's marine API only
covers ocean/sea points, so plenty of valid beaches (lake beaches, some
coastlines) come back with no wave reading. When that happens, the other
four factors (which sum to 90) are rescaled to 0–100 so a beach is never
penalized just for lacking wave data.

Each factor is implemented as a small set of point buckets (see
`backend/app/scoring.py`) rather than a continuous curve — easy to read,
easy to test, and deliberately monotonic across its whole domain (score
never drops as it gets warmer, never rises as the wind picks up, etc). This
reuses the *approach* of the Oregon Beach App's weather score (readable
point buckets summed to 100) but every threshold, weight, and the wave
factor are re-derived from scratch for this app, not copied.

Example: 78°F, 3 mph wind, no rain, clear skies, 0.2 m surf scores 100.
38°F, 28 mph wind, heavy rain, overcast, 2.5 m surf scores 0.

## SPEC v0.3 — borrowed from the Oregon Beach App

v0.3 ports the Oregon Beach App's feature set (`apps/beach`) into this
global app: behavior borrowed, not code — every threshold, formula, and
unit below is re-derived for beach-finder's shape (km instead of miles
internally, Open-Meteo instead of NWS, no database). What follows maps
each borrowed feature to its Oregon original and documents where this app
adapted it.

- **Drive time** (`app/geo.py: estimate_drive_time_minutes`) — Oregon's
  heuristic, unchanged: assume a flat 45mph average, no traffic or
  routing, `minutes = distance_miles / 45 * 60`. Oregon's original
  truncates via Python's `int()` rather than rounding
  (`int(distance / 45 * 60)`); this keeps that exact behavior, just
  converting this app's `distance_km` to miles first. Returned as
  `drive_time_minutes` on each beach.

- **Time-based scores** (`app/forecast.py: compute_time_based_scores`) —
  Oregon's signature feature: score conditions at arrival (now + drive
  time), and at arrival +1h / +3h, using the same 0–100 formula as
  "current". Response carries `scores: {arrival, plus1h, plus3h}`
  alongside the existing top-level `score` (current conditions,
  unchanged).

  **Row selection.** The single per-beach Open-Meteo call now requests
  `hourly=` fields (`forecast_hours=24`, see `config.HOURLY_FORECAST_HOURS`)
  in addition to `current=`, in the same request — no second HTTP call.
  Open-Meteo's `forecast_hours` parameter starts the returned array at the
  *current* hour, so `hourly[0]` is always "now"; there's no separate
  lookup to find "now" inside the array.

  A drive time in minutes converts to an hour offset via
  `round(minutes / 60)`. This is Python's round-half-to-even: a drive time
  of exactly 30 minutes rounds *down* to offset 0 (`round(0.5) == 0`),
  while 90 minutes rounds *up* to offset 2 (`round(1.5) == 2`) — both are
  "nearest hour", just not always rounding the exact half up. Since drive
  time is itself a 45mph guess, this doesn't materially change the result,
  but it's a real, tested behavior (`tests/test_forecast.py`), not an
  accident. `+1h`/`+3h` simply add 1 and 3 to that offset.

  **Clamping.** Oregon's `calculate_time_based_scores` degrades gracefully
  when NWS's hourly list runs short (falls back to a 2-hour read, or to
  the current score); this app's equivalent is: if an offset would index
  past the end of the returned hourly array — a long drive time, or a
  beach near the 500-mile search ceiling combined with the +3h lookahead —
  it **clamps to the last available hourly row** rather than raising.
  24 hours of requested forecast comfortably covers the worst case (500mi
  ≈ 11h drive + 3h lookahead ≈ 14h), so clamping is the exception, not the
  common case; it's exercised directly in
  `tests/test_forecast.py::TestComputeTimeBasedScores::test_drive_time_longer_than_forecast_horizon_clamps_to_last_row`.

  **Wave height in forecast rows.** Open-Meteo's marine (wave) call stays
  current-only and unchanged per spec — there's no forecasted wave height.
  Arrival/+1h/+3h rows reuse the beach's *current* wave reading rather than
  dropping the factor, since wave height changes slowly relative to a
  3-hour window; this is a deliberate stand-in, not a real forecast.

- **Hourly forecast on the card** — the next 3 hours (time, temp, wind,
  precip, cloud) ride along in `hourly_forecast`, taken as
  `hourly[1:4]` (the hours *after* now — `hourly[0]` is already covered by
  "current conditions"). Oregon's card showed a 2-hour slice of temp/wind/
  precip only; this app's spec explicitly asks for cloud cover too, so the
  card's hourly rows show one more field than Oregon's did.

- **Wind direction + humidity** — added to current conditions via
  Open-Meteo's `wind_direction_10m` and `relative_humidity_2m` (fetched in
  the same call as everything else). `app/compass.py` converts degrees to
  an 8-point compass abbreviation (N/NE/E/SE/S/SW/W/NW) server-side; the
  frontend spells it out into a full word for display (`Northwest`, etc.),
  the same split Oregon used (NWS gave it a letter code; BeachCard spelled
  it out for the label).

- **Sort control** — `distance` or `arrival` (score), client-side over the
  already-fetched list, no re-fetch. Oregon's third option — sort by
  latitude, north-to-south — is Oregon-geography-specific and doesn't
  translate globally, so it's dropped rather than replaced; the frontend's
  dropdown reads "🚗 Distance (Closest)" / "☀️ Weather (Best)", mirroring
  Oregon's emoji-labeled options minus the dropped one.

- **Max-distance filter** — a slider over the already-fetched list
  (client-side, like Oregon's), labeled "Max Distance: N km" / "All",
  scaled to the search radius actually used for that lookup rather than a
  fixed 500-mile range (this app's search ceiling is reported per-request
  as `searched_radius_km`, not a constant).

- **Score color classes** — the existing 4-tier meter classes
  (`score-great/good/fair/poor`) are unchanged for the current-conditions
  meter. The new Arrival/+1h/+3h chips use Oregon's own 3-tier thresholds
  (`>=80` excellent, `>=60` good, else poor), mapped onto this app's
  existing color tokens (`--score-great`, `--score-fair`, `--score-poor`)
  rather than introducing new colors, so the palette stays coherent
  between the meter and the chips.

- **City/locality** (`app/overpass.py: extract_city`) — no new geocoding
  dependency, stays keyless. Priority order: `addr:city` / `addr:town` /
  `addr:hamlet` (direct, structured tags) → `is_in:city` (an older
  structured tag some elements carry) → `is_in` (free-text locality
  hierarchy, e.g. `"Newport, Lincoln County, Oregon, USA"` — takes the
  first, most specific segment). `None`/omitted when nothing usable is
  present, rather than guessing.

- **Card interaction** — the frontend's card is now click-to-expand, like
  Oregon's `BeachCard`: collapsed shows name/city, a
  `📍 distance • 🚗 drive time` line, an expand hint, and the three score
  chips; expanded adds emoji-labeled weather rows (🌡️/💨/🌧️/☁️/💧) and the
  hourly list. Units stay this app's own — precipitation is real
  millimeters from Open-Meteo, not the 0–1 "percent chance"-shaped value
  Oregon displayed (Oregon's NWS-derived precipitation field was itself an
  estimate parsed out of forecast text, not a probability; this app's is a
  real Open-Meteo reading, so it's labeled "mm" rather than "%").

## Caching

Overpass is rate-limited and slow, and a global search would otherwise be
unusably slow — caching is load-bearing, not an optimization:

- **Tile cache** (`app/overpass.py: CachingOverpassClient`) — Overpass
  results are cached by `(geographic tile, radius band)` with a 30-day TTL,
  since coastlines don't move. Tiles are a coarse 0.25°×0.25° grid so
  nearby users share cache entries.
- **Weather cache** (`app/weather.py: CachingWeatherClient`) — per-beach
  conditions are cached (coordinates rounded to 3 decimal places) with a
  30-minute TTL, since weather actually changes.
- **Single-flight** — both caches sit behind a per-key `asyncio.Lock`
  (`app/cache.py: KeyedLock`), so concurrent requests for the same
  uncached tile or beach share one upstream fetch instead of stampeding
  Overpass / Open-Meteo.
- **Backoff** — the Overpass client retries on `429`/`504` with exponential
  backoff (bounded retries; on exhaustion it returns an empty list for that
  band rather than failing the whole search, so a slow/overloaded Overpass
  degrades to "found fewer beaches" instead of an error page). The
  Open-Meteo client does the same, and beach weather fetches are also
  capped at a handful in flight at once (`WEATHER_FETCH_CONCURRENCY` in
  `app/config.py`) — verified against live Open-Meteo that firing all ~25
  beaches' weather requests at once gets a real fraction of them 429'd;
  capping concurrency plus retrying fixed that.

Both caches are in-process dicts (see `app/cache.py: TTLCache`) — no
Postgres, per the spec; this app's cache needs are small enough that an
in-memory store is enough, and it resets on redeploy (acceptable: the tile
cache just refills from Overpass on demand).

## Running locally

Backend (Python 3.11+ to actually run the server — see note below):

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt   # includes pytest
uvicorn app.main:app --reload --port 8000
```

> **Python version note:** the app modules use `X | None` union syntax
> under `from __future__ import annotations`, which is fine on 3.9 for
> plain dataclasses (annotations are never evaluated) but **not** for
> Pydantic's `BaseModel`s in `app/schemas.py` (and therefore `app/main.py`,
> which imports them) — Pydantic evaluates annotations at class-definition
> time regardless of the `__future__` import, so importing `app.main` (or
> running uvicorn) on Python 3.9 raises `TypeError: unsupported operand
> type(s) for |: 'type' and 'NoneType'`. This is pre-existing (not
> introduced by v0.3) and only affects `app.main`/`app.schemas` — the test
> suite never imports either, so `pytest` runs fine on 3.9 too. Use the
> `python:3.11-slim` image (or any 3.11+ interpreter) to actually run the
> server.

- `GET http://localhost:8000/api/health` → `{"status": "ok"}`
- `GET http://localhost:8000/api/beaches?lat=44.62&lon=-124.05` → ranked
  beaches near Newport, OR.

Run the test suite (fully offline — fake Overpass client, hand-written
Overpass/Open-Meteo fixtures, no live network):

```bash
pytest
```

Frontend: no build step. Point any static file server at `frontend/` and
open `index.html` — but note it calls `/api/*` on the same origin, so for a
real local run either serve both behind one proxy or temporarily edit
`API_BASE` in `app.js` to point at `http://localhost:8000/api`.

## Two-container deploy layout

Matches the Oregon Beach App's shape, adapted for a static (no-build)
frontend:

- **`beach-finder-backend`** — `backend/Dockerfile`, `python:3.11-slim` +
  uvicorn on port 8000. Cache lives in-process (no volume needed).
- **`beach-finder-frontend`** — `frontend/Dockerfile`, `nginx:alpine`
  serving the static files directly (no Node/build step — it's plain
  HTML/CSS/JS).

Both attach to the hub's `web` network. Caddy path-splits
`beaches.apps.precogsoftwareservices.com`: `/api/*` → backend, everything
else → frontend. No API keys anywhere (Overpass and Open-Meteo are both
keyless); the backend sets a descriptive `User-Agent` on Overpass requests
per their usage policy.

## Testing bar (what's covered)

- `tests/test_search.py` — the tiered-radius accumulator against a fake
  Overpass client: stops at target count, accumulates (and dedupes) across
  bands, respects the 500-mile ceiling, clamps oversized bands down to it.
- `tests/test_cache.py` — TTL expiry for the generic cache (used for both
  tiles and weather) via an injectable fake clock, plus single-flight
  behavior of the keyed lock under concurrent access.
- `tests/test_scoring.py` — warm+calm+dry beats cold+windy+rainy, score
  stays within 0–100, missing wave data rescales rather than penalizing,
  and each factor is monotonic in its expected direction.
- `tests/test_parsing.py` — hand-written fixture JSON (recorded from the
  real Overpass and Open-Meteo response shapes) parsed into the internal
  data model: nodes vs. ways/relations (`center`), missing names, missing
  coordinates skipped, missing wave height; (v0.3) the `hourly` block
  parsed alongside `current`, `wind_direction_10m`/`relative_humidity_2m`
  parsing, and city/locality extraction from OSM tags (`addr:city`,
  `is_in`, priority order, and the omitted-when-absent case).
- `tests/test_geo.py` (v0.3) — drive-time math: matches Oregon's formula
  for a known distance, truncates rather than rounds, monotonic in
  distance.
- `tests/test_compass.py` (v0.3) — degrees-to-compass-point conversion:
  all 8 cardinal/ordinal points, boundary behavior at each 45° edge,
  wraparound above 360° and below 0°.
- `tests/test_forecast.py` (v0.3) — arrival-row selection: nearest-hour
  rounding (including the round-half-to-even edge case), `+1h`/`+3h`
  offsetting, and the clamp-to-last-row behavior when drive time (or
  drive time + 3h) exceeds the returned hourly horizon; scoring-on-
  forecast-rows sanity (right row picked, current wave height carried into
  every row, scores stay within 0–100, neutral fallback when hourly data
  is entirely missing).

All 110 tests run offline (`pytest.ini` sets `asyncio_mode = auto` for the
async tests; no network calls in the suite) — 62 from v0.1/v0.2 plus 48
new for v0.3.

## Out of scope (v0.1, per spec)

Accounts, favorites, tide data (no good free global source), photos.
