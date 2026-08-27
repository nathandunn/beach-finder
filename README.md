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

Backend (Python 3.11+):

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt   # includes pytest
uvicorn app.main:app --reload --port 8000
```

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
  coordinates skipped, missing wave height.

All 62 tests run offline (`pytest.ini` sets `asyncio_mode = auto` for the
async tests; no network calls in the suite).

## Out of scope (v0.1, per spec)

Accounts, favorites, tide data (no good free global source), photos.
