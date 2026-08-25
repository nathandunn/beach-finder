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
