"""Tunable constants for the beach-finder backend.

Keeping these in one place makes the tiered-search, caching, and scoring
behavior easy to see (and to override in tests) without hunting through
the rest of the codebase.
"""
from __future__ import annotations

# --- Search behavior -------------------------------------------------------

# Radius bands to search outward, in kilometers. Overpass is queried with a
# growing radius for each band (not an annulus -- Overpass has no clean way
# to query "between R1 and R2", so we re-query the full circle and dedupe by
# OSM id). Bands are deliberately fine-grained near the user and coarser
# further out, matching the spec's "5-10km near the user, widening further
# out" guidance.
DEFAULT_RADIUS_BANDS_KM: list[float] = [8, 16, 32, 64, 128, 256, 400, 804.7]

# Stop expanding once we've accumulated this many distinct beaches.
DEFAULT_TARGET_COUNT = 25

# Hard ceiling: 500 miles in kilometers. We never search further than this,
# and rank whatever was found once it's hit.
MAX_RADIUS_KM = 804.7  # 500 miles

# --- Caching -----------------------------------------------------------

# Beach geometry barely changes -- coastlines don't move -- so tile results
# are cached for a long time.
TILE_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days

# Weather changes minute to minute; cache briefly per the spec (~30 min).
WEATHER_CACHE_TTL_SECONDS = 30 * 60  # 30 minutes

# Geographic tile size in degrees used as the cache key granularity for
# Overpass results. ~0.25 degrees is roughly 25-28km at mid-latitudes --
# coarse enough that nearby users share cache entries, fine enough that the
# tile's radius bands stay meaningful.
TILE_SIZE_DEG = 0.25

# --- Overpass client -----------------------------------------------------

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_USER_AGENT = "beach-finder/0.1 (contact: ndunnme@gmail.com)"
OVERPASS_TIMEOUT_SECONDS = 25.0
OVERPASS_MAX_RETRIES = 3
OVERPASS_BACKOFF_BASE_SECONDS = 1.5
OVERPASS_RETRY_STATUS_CODES = (429, 504)

# --- Open-Meteo client -----------------------------------------------------

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
OPEN_METEO_TIMEOUT_SECONDS = 10.0
OPEN_METEO_MAX_RETRIES = 3
OPEN_METEO_BACKOFF_BASE_SECONDS = 1.0
OPEN_METEO_RETRY_STATUS_CODES = (429, 504)

# A single /api/beaches request may need weather for ~25 distinct beaches.
# Fetching them all at once is not polite to a free, keyless API and (in
# practice) gets a meaningful fraction of requests 429'd. Cap how many
# beaches are fetched concurrently; the rest queue behind the semaphore.
WEATHER_FETCH_CONCURRENCY = 6

# Weather-cache key rounding: beach coordinates are rounded to this many
# decimal places (~110m at 3dp) before being used as a cache key, so beaches
# that share a coordinate (or nearly do) share a weather fetch.
WEATHER_CACHE_COORD_PRECISION = 3

# --- Drive time (SPEC v0.3, borrowed from the Oregon Beach App) ------------

# Oregon's heuristic, unchanged: assume a flat average speed, no traffic or
# routing. minutes = distance_miles / ASSUMED_DRIVE_SPEED_MPH * 60.
ASSUMED_DRIVE_SPEED_MPH = 45.0

KM_TO_MILES = 0.621371

# --- Time-based scores / hourly forecast (SPEC v0.3) -----------------------

# How many hours of hourly forecast to request from Open-Meteo per beach
# (in the same call as `current`), starting at the current hour (index 0).
# Needs to comfortably cover the worst case this app can produce: the
# 500-mile search ceiling is ~11h of drive time at 45mph, plus the +3h
# lookahead on top of arrival is ~14h. 24h leaves headroom for that with a
# single day of hourly data. Drive times that land beyond this horizon
# clamp to the last available hourly row rather than erroring -- see
# app/forecast.py.
HOURLY_FORECAST_HOURS = 24

# How many hours of "next N hours" forecast to surface in the API response
# and render on the card -- distinct from HOURLY_FORECAST_HOURS above,
# which is how much we *request*.
HOURLY_FORECAST_DISPLAY_HOURS = 3
