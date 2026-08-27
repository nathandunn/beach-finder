# Beach App — SPEC v0.1 (new app)

Status: agreed 2026-08-25. Separate from the existing Oregon Beach App.

## Purpose
Same idea as Oregon Beach App — rank nearby beaches by how good conditions are
right now — but not tied to Oregon. Ask the browser where you are, find the
beaches around you, score them, list them.

## Relationship to Oregon Beach App
Separate app, separate repo, separate backend. The existing `beach_app` is
Oregon-scoped with a hardcoded beach list and its own Postgres schema.
Entangling the two would make both harder to change. Scoring *approach* is
reused; code is not.

## Data sources
- **Beaches** — OpenStreetMap via the Overpass API, querying `natural=beach`.
  Global coverage, free, no API key.
- **Weather** — Open-Meteo. Global, free, no API key. Temperature, wind,
  precipitation, cloud cover, and where available wave height.

## Search strategy
A single 500-mile Overpass query would time out or return thousands of results.
Instead, expand outward in steps and stop once there are enough beaches:

- Query in increasing radius bands (roughly 5–10 km granularity near the user,
  widening further out)
- Accumulate results as bands complete
- Stop as soon as the target count is reached (default ~25 beaches)
- Hard ceiling at 500 miles, then rank what was found

This keeps the common case (user near a coast) fast, and degrades gracefully
for inland users who need a wide search.

## Caching
Overpass is rate-limited and slow. Backend caches:
- Beach geometry by geographic tile, long TTL — coastlines do not move
- Weather by beach, short TTL (~30 min)

Without caching this app is unusably slow. Caching is not optional.

## Backend
**Required, and does not currently exist.** New FastAPI service:
- `GET /beaches?lat=&lon=` — tiered Overpass search, returns ranked beaches
- Weather fetch and scoring server-side
- Cache layer
- Overpass proxy (avoids browser CORS and centralises rate limiting)

## Frontend
- Browser geolocation prompt on load, with manual location entry as fallback
  (geolocation denial must not be a dead end)
- Ranked list: beach name, distance, score, current conditions
- Graceful empty state for users far from any coast

## Out of scope for v0.1
- Accounts, favourites, persistence of user preferences
- Tide data (no good free global source)
- Photos

---

# Beach Finder — SPEC v0.2 (build + deploy)

Status: agreed 2026-08-27. v0.1's scope is confirmed unchanged — build it.
Additions below are deployment and naming only.

## Confirmed
The v0.1 design stands: browser geolocation ("pulls from the local area, not
just Oregon"), Overpass for beaches, Open-Meteo for weather, tiered-radius
search, mandatory caching, FastAPI backend, manual-location fallback.

## Deployment (per hub-orchestrator/CLAUDE.md)
- Two containers on the hub's `web` network: `beach-finder-backend`
  (FastAPI + cache) and `beach-finder-frontend` (static, nginx). Compose or
  plain Dockerfiles — match the beach app's shape.
- Route: `beaches.apps.precogsoftwareservices.com`, path-split like the
  Oregon app: `/api/*` → backend, rest → frontend. (Caddy edit happens at
  deploy time, not by the implementing agent.)
- Cache lives in-process or on-disk in the backend container — no Postgres
  unless genuinely needed; v0.1's cache needs (tile TTL + 30-min weather)
  fit an in-memory store with optional disk snapshot.
- No API keys anywhere (both upstreams are keyless). Respect Overpass rate
  limits: single flight per tile, backoff on 429/504.

## Scoring
Reuse the Oregon app's *approach* (readable 0–100 score from temperature,
wind, precipitation, cloud; wave height where present), re-derived, not
copied. Document the formula in the README.

## Testing bar
Backend: pytest covering the tiered-search accumulator (stop-at-target,
ceiling), cache TTL behavior, scoring formula, and Overpass/Open-Meteo
response parsing against recorded fixtures (no live network in tests).
Frontend: keep it simple — a static page with fetch; the smoke-bundle check
must pass.
