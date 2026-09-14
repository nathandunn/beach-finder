"""Water-type classification: ocean / lake / river / unknown (SPEC v0.4).

OSM beaches rarely tag their own water type, but the water feature next to
them usually does -- verified live during spec development: Twin Lakes
Beach shows `natural=coastline` within 400m alongside named creeks. This
module:

- builds ONE batched Overpass query per search, covering every beach found
  by the tiered geometry search with `around:400m` clauses for the four
  tag patterns the spec cares about (see `build_water_type_query`)
- classifies each beach independently against whatever features come back
  (see "Attribution" below)
- applies the spec's precedence -- coastline (ocean) beats lake beats
  river; a stream only counts as river if nothing stronger is present;
  nothing found -> "unknown", never a guess (see `_combine`)

Caching (per-beach, long TTL) and single-flight live in
`CachingWaterTypeClient` below, mirroring `weather.py`'s
`CachingWeatherClient` shape.

## Attribution: independent per-beach check, not nearest-neighbor

The batched query returns one flat list of water features; something has
to decide which beach(es) each feature applies to. Two approaches were
possible:

1. Assign each feature to only its single *nearest* beach (a partition).
2. Check each beach independently: does *this* feature fall within this
   beach's own probe radius, regardless of what's nearest to it.

This module does (2). Two named beaches can legitimately sit along the
same stretch of coastline, both well within 400m of the same
`natural=coastline` way. Under a nearest-only partition, whichever beach
lost the tie-break would incorrectly fail to see that coastline at all --
even though it is plainly, independently, within its own probe radius.
Per spec ("ocean -- natural=coastline within the probe radius"), that beach
is next to the ocean; who's nearest to a particular OSM way is irrelevant.
Approach (2) costs O(beaches x features), which at this app's scale (~25
beaches, at most a few hundred features per search) is trivial.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from .config import (
    OVERPASS_TIMEOUT_SECONDS,
    WATER_TYPE_PROBE_RADIUS_M,
    WEATHER_CACHE_COORD_PRECISION,
)
from .geo import haversine_km
from .models import BeachElement
from .overpass import post_overpass_query

# --- Classification ---------------------------------------------------------

_LAKE_VALUES = {"lake", "reservoir", "pond"}
_RIVER_WATERWAY_VALUES = {"river", "riverbank"}

# Precedence, strongest first. "stream" is deliberately last/weakest: per
# spec it only counts as a river when nothing stronger is present.
_TIER_PRECEDENCE = ("ocean", "lake", "river", "stream")


@dataclass(frozen=True)
class WaterFeature:
    """A single water-related OSM element near some beach(es): just a
    point (node lat/lon, or way/relation center) and its tags -- enough to
    classify it and measure distance."""

    lat: float
    lon: float
    # Full geometry vertices (from `out geom`), when the element is a way or
    # relation. A long coastline way's CENTER can be miles from a beach the
    # way itself passes right next to, so membership tests must use these
    # when present; lat/lon alone is only trustworthy for nodes.
    tags: dict[str, Any]
    points: tuple[tuple[float, float], ...] | None = None


def classify_element(tags: dict[str, Any]) -> str | None:
    """Which tier (if any) a single water feature's tags belong to.

    Order matters and matches SPEC v0.4 precedence: an element could in
    principle carry more than one of these tags (real-world OSM data is
    messy), so coastline is checked first, then lake, then river/riverbank
    or water=river, then stream. Returns None for a feature that matches
    none of the patterns -- defensive; the Overpass query should only ever
    return matching elements, but a classification helper shouldn't crash
    on a surprise tag combination.
    """
    if tags.get("natural") == "coastline":
        return "ocean"
    if tags.get("water") in _LAKE_VALUES:
        return "lake"
    if tags.get("waterway") in _RIVER_WATERWAY_VALUES or tags.get("water") == "river":
        return "river"
    if tags.get("waterway") == "stream":
        return "stream"
    return None


def _combine(tiers: set[str]) -> str:
    """Collapse every tier seen near one beach into the single strongest
    classification, per SPEC v0.4 precedence (coastline > lake > river;
    stream only counts as river if nothing stronger showed up). Empty
    input means nothing was found nearby at all -> "unknown", never a
    guess."""
    for tier in _TIER_PRECEDENCE:
        if tier in tiers:
            return "river" if tier == "stream" else tier
    return "unknown"


def classify_beaches(
    beaches: list[BeachElement],
    features: list[WaterFeature],
    radius_km: float = WATER_TYPE_PROBE_RADIUS_M / 1000.0,
) -> dict[str, str]:
    """Classify every beach independently against the shared feature list
    (see module docstring's "Attribution" section for why this isn't a
    nearest-neighbor partition). Returns osm_id -> "ocean"/"lake"/"river"/
    "unknown" for every beach passed in, even ones with no nearby features
    at all."""
    result: dict[str, str] = {}
    for beach in beaches:
        tiers: set[str] = set()
        for feature in features:
            if not feature_within(beach.lat, beach.lon, feature, radius_km):
                continue
            tier = classify_element(feature.tags)
            if tier is not None:
                tiers.add(tier)
        result[beach.osm_id] = _combine(tiers)
    return result


def feature_within(lat: float, lon: float, feature: WaterFeature, radius_km: float) -> bool:
    """Is (lat, lon) within radius_km of the feature -- any vertex, or (for
    ways) any *segment* between consecutive vertices. Coastline ways drawn
    along a straight shore can have vertices a kilometre apart, so a beach
    sitting squarely on the line between two of them must still count."""
    pts = feature.points or ((feature.lat, feature.lon),)
    for la, lo in pts:
        if haversine_km(lat, lon, la, lo) <= radius_km:
            return True
    if len(pts) < 2:
        return False
    # Local equirectangular projection (km) for the segment test; fine at
    # the few-hundred-metre scale this is used at.
    cos_lat = math.cos(math.radians(lat))
    px, py = 0.0, 0.0
    prev = None
    for la, lo in pts:
        x = (lo - lon) * 111.0 * cos_lat
        y = (la - lat) * 111.0
        if prev is not None:
            ax, ay = prev
            dx, dy = x - ax, y - ay
            seg = dx * dx + dy * dy
            if seg > 0:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg))
                cx, cy = ax + t * dx, ay + t * dy
                if math.hypot(cx - px, cy - py) <= radius_km:
                    return True
        prev = (x, y)
    return False


def build_coastline_query(
    beaches: list[BeachElement],
    margin_m: int = WATER_TYPE_PROBE_RADIUS_M,
    timeout_s: int = int(OVERPASS_TIMEOUT_SECONDS),
) -> str:
    """One bounding-box query for every natural=coastline way near the
    candidate beaches (their extent plus the probe radius). Measured live
    2026-09-14: 1-6 s and 0.2-2 MB for a 64 km box, where the per-beach
    `around:` union took 9-15 s -- and this is the clause that decides
    "ocean", which is what the default filter shows."""
    if not beaches:
        return ""
    lats = [b.lat for b in beaches]
    lons = [b.lon for b in beaches]
    dlat = margin_m / 111000.0
    mid = (max(lats) + min(lats)) / 2.0
    dlon = margin_m / (111000.0 * max(math.cos(math.radians(mid)), 0.01))
    south, north = max(min(lats) - dlat, -90.0), min(max(lats) + dlat, 90.0)
    west, east = max(min(lons) - dlon, -180.0), min(max(lons) + dlon, 180.0)
    return (
        f"[out:json][timeout:{timeout_s}][bbox:{south:.5f},{west:.5f},{north:.5f},{east:.5f}];\n"
        'way["natural"="coastline"];\nout geom;'
    )


# --- Batched query construction ---------------------------------------------


def build_water_type_query(
    beaches: list[BeachElement],
    radius_m: int = WATER_TYPE_PROBE_RADIUS_M,
    timeout_s: int = int(OVERPASS_TIMEOUT_SECONDS),
    include_coastline: bool = True,
) -> str:
    """ONE batched Overpass query covering every beach found this search:
    a union of `around:{radius_m}` clauses, four tag patterns per beach
    (SPEC v0.4's "~25 beaches x 4 patterns is fine for Overpass"):

    - `natural=coastline` (ocean)
    - `water` in {lake, reservoir, pond} (lake)
    - `waterway` in {river, riverbank, stream} -- both tiers requested in
      one clause; `classify_element` tells river and stream apart
      afterwards from the actual tag value on each returned element, so
      this stays at 4 query patterns rather than 5
    - `water=river` (river, the other way rivers get tagged)

    `nwr` (the combined node/way/relation selector) is used throughout so
    each pattern is one clause regardless of which element type OSM
    happens to use for that feature (coastlines and rivers are usually
    ways, lakes are often relations, tiny ponds are sometimes nodes).
    `out center;` so ways/relations report a point to measure distance
    from, exactly like the beach-geometry query in overpass.py.

    Returns an empty string for an empty beach list -- callers should
    treat that as "nothing to query" rather than sending a query with an
    empty union (which Overpass would reject).
    """
    if not beaches:
        return ""

    clauses: list[str] = []
    for beach in beaches:
        lat, lon = beach.lat, beach.lon
        if include_coastline:
            clauses.append(f'  nwr["natural"="coastline"](around:{radius_m},{lat},{lon});')
        clauses.append(f'  nwr["water"~"^(lake|reservoir|pond)$"](around:{radius_m},{lat},{lon});')
        clauses.append(f'  nwr["waterway"~"^(river|riverbank|stream)$"](around:{radius_m},{lat},{lon});')
        clauses.append(f'  nwr["water"="river"](around:{radius_m},{lat},{lon});')

    body = "\n".join(clauses)
    # `out geom` (not `out center`): the around-filter matches on a way's true
    # geometry, so the membership test must measure against that geometry too.
    # A coastline way's center can sit far outside the probe radius even when
    # the way runs along the beach - with `out center` those features were
    # dropped and ocean beaches classified as river (found live, 2026-08-27).
    return f"[out:json][timeout:{timeout_s}];\n(\n{body}\n);\nout geom;"


def parse_water_features(payload: dict[str, Any]) -> list[WaterFeature]:
    """Turn a raw Overpass JSON payload (from `build_water_type_query`)
    into WaterFeatures. Mirrors `overpass.parse_overpass_response`'s
    node-vs-center handling; elements with neither are skipped rather than
    raising, same rationale -- a partial/odd response shouldn't blow up
    classification for every other beach."""
    features: list[WaterFeature] = []
    for element in payload.get("elements", []):
        el_type = element.get("type")
        points: list[tuple[float, float]] = []
        if el_type == "node":
            lat = element.get("lat")
            lon = element.get("lon")
        else:
            # `out geom`: ways carry a `geometry` vertex list; relations carry
            # it per member. Fall back to `center` for older cached payloads.
            for pt in element.get("geometry") or []:
                if pt and pt.get("lat") is not None and pt.get("lon") is not None:
                    points.append((float(pt["lat"]), float(pt["lon"])))
            for member in element.get("members") or []:
                for pt in member.get("geometry") or []:
                    if pt and pt.get("lat") is not None and pt.get("lon") is not None:
                        points.append((float(pt["lat"]), float(pt["lon"])))
            if points:
                lat, lon = points[0]
            else:
                center = element.get("center") or {}
                lat = center.get("lat")
                lon = center.get("lon")

        if lat is None or lon is None:
            continue

        # Cap vertices per element so a monster relation can't bloat memory;
        # an even stride keeps endpoints-ish coverage along the geometry.
        MAX_PTS = 500
        if len(points) > MAX_PTS:
            stride = len(points) // MAX_PTS + 1
            points = points[::stride]

        features.append(
            WaterFeature(
                lat=float(lat), lon=float(lon),
                points=tuple(points) if points else None,
                tags=element.get("tags") or {},
            )
        )
    return features


# --- HTTP client + caching wrapper ------------------------------------------


class WaterTypeClient(Protocol):
    async def classify(self, beaches: list[BeachElement]) -> dict[str, str]:
        ...


class HttpWaterTypeClient:
    """Talks to the real Overpass API for the water-features batch query.

    Reuses `overpass.post_overpass_query` for retry/backoff and the shared
    User-Agent -- same politeness as the beach-geometry search, not a
    second implementation of it. If the query fails entirely (retries
    exhausted, bad response), returns an empty feature list; the caller
    (`CachingWaterTypeClient`) turns that into "unknown" for every beach
    rather than failing the search (per spec: a filter failure must never
    sink the whole beach search).
    """

    def __init__(self, http_client: httpx.AsyncClient | None = None):
        self._client = http_client
        self._owns_client = http_client is None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=OVERPASS_TIMEOUT_SECONDS)
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def fetch_features(self, beaches: list[BeachElement]) -> list[WaterFeature] | None:
        """Two steps: a cheap bounding-box query for coastline (decides
        "ocean"), then the per-beach lake/river probe only for beaches the
        coastline did not already claim. Returns None when *both* queries
        failed outright, so the caller can leave those beaches uncached
        rather than remembering "unknown" for a month."""
        if not beaches:
            return []
        client = await self._get_client()
        features: list[WaterFeature] = []
        failed = 0

        coast_payload = await post_overpass_query(client, build_coastline_query(beaches))
        if coast_payload is None:
            failed += 1
        else:
            features.extend(parse_water_features(coast_payload))

        radius_km = WATER_TYPE_PROBE_RADIUS_M / 1000.0
        inland = [
            b for b in beaches
            if not any(feature_within(b.lat, b.lon, f, radius_km) for f in features)
        ]
        if inland:
            query = build_water_type_query(inland, include_coastline=coast_payload is None)
            payload = await post_overpass_query(client, query)
            if payload is None:
                failed += 1
            else:
                features.extend(parse_water_features(payload))
        elif coast_payload is None:
            return None

        if failed and not features:
            return None
        return features


def water_type_cache_key(lat: float, lon: float, precision: int) -> tuple[float, float]:
    return (round(lat, precision), round(lon, precision))


class CachingWaterTypeClient:
    """Wraps an `HttpWaterTypeClient`-shaped inner client with a per-beach
    cache (long geometry TTL -- water bodies don't move, SPEC v0.4) and a
    single-flight lock, mirroring `weather.CachingWeatherClient`'s shape:
    same cache-key-by-rounded-coordinate approach, same
    check-cache/acquire-lock/re-check-cache/fetch/populate flow.

    The one real difference from the weather cache: a weather fetch is
    naturally one-request-per-beach, but SPEC v0.4 requires ONE batched
    query per search covering every beach still uncached -- not one query
    per beach. So `classify()` takes the *whole* beach list for a search,
    splits it into already-cached vs. missing, and if anything is missing
    issues exactly one Overpass query (via the inner client) covering just
    those missing beaches. Beaches already cached never appear in that
    query at all.

    Single-flight here is keyed on the sorted set of currently-missing
    beach ids, not on a single beach -- concurrent calls to `classify()`
    with the *same* missing set share one fetch, same idea as the tile and
    weather caches' single-flight. Two calls with a different but
    overlapping missing set would each fire their own query rather than
    coalescing further; that's an accepted simplification at this app's
    request volume, not a correctness problem (each still gets a correct
    answer, just possibly redundant Overpass calls in a rare race).
    """

    def __init__(
        self,
        inner: Any,
        cache: Any,
        locks: Any = None,
        coord_precision: int = WEATHER_CACHE_COORD_PRECISION,
    ):
        from .cache import KeyedLock

        self._inner = inner
        self._cache = cache
        self._locks = locks or KeyedLock()
        self._precision = coord_precision

    def _key(self, beach: BeachElement) -> tuple[float, float]:
        return water_type_cache_key(beach.lat, beach.lon, self._precision)

    async def classify(self, beaches: list[BeachElement]) -> dict[str, str]:
        result: dict[str, str] = {}
        missing: list[BeachElement] = []
        for beach in beaches:
            cached = self._cache.get(self._key(beach))
            if cached is not None:
                result[beach.osm_id] = cached
            else:
                missing.append(beach)

        if not missing:
            return result

        lock_key = tuple(sorted(b.osm_id for b in missing))
        lock = await self._locks.acquire(lock_key)
        async with lock:
            # Re-check: another request may have populated some or all of
            # these while we were waiting for the lock (single-flight).
            still_missing: list[BeachElement] = []
            for beach in missing:
                cached = self._cache.get(self._key(beach))
                if cached is not None:
                    result[beach.osm_id] = cached
                else:
                    still_missing.append(beach)

            if not still_missing:
                return result

            features = await self._inner.fetch_features(still_missing)
            if features is None:
                # Every query failed: answer "unknown" for now but cache
                # nothing, so the next search asks again instead of
                # remembering a failure for a month.
                for beach in still_missing:
                    result[beach.osm_id] = "unknown"
                return result
            # An empty feature list (genuinely nothing nearby) classifies
            # every still-missing beach as "unknown" -- classify_beaches
            # never raises.
            classified = classify_beaches(still_missing, features)

            for beach in still_missing:
                water_type = classified.get(beach.osm_id, "unknown")
                self._cache.set(self._key(beach), water_type)
                result[beach.osm_id] = water_type

        return result
