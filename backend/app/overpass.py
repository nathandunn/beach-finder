"""Overpass API client: the real HTTP client, response parsing, and a
caching wrapper that adds the tile cache + single-flight behavior the spec
requires.
"""
from __future__ import annotations

import asyncio
import math
from typing import Any

import httpx

from .cache import KeyedLock, TTLCache
from .config import (
    OVERPASS_BACKOFF_BASE_SECONDS,
    OVERPASS_MAX_RETRIES,
    OVERPASS_RETRY_STATUS_CODES,
    OVERPASS_TIMEOUT_SECONDS,
    OVERPASS_URL,
    OVERPASS_USER_AGENT,
    TILE_SIZE_DEG,
)
from .models import BeachElement


def build_overpass_query(lat: float, lon: float, radius_km: float) -> str:
    radius_m = int(radius_km * 1000)
    return (
        f"[out:json][timeout:{int(OVERPASS_TIMEOUT_SECONDS)}];\n"
        "(\n"
        f'  node["natural"="beach"](around:{radius_m},{lat},{lon});\n'
        f'  way["natural"="beach"](around:{radius_m},{lat},{lon});\n'
        f'  relation["natural"="beach"](around:{radius_m},{lat},{lon});\n'
        ");\n"
        "out center;"
    )


def parse_overpass_response(payload: dict[str, Any]) -> list[BeachElement]:
    """Turn a raw Overpass JSON payload into BeachElements.

    Nodes carry lat/lon directly; ways and relations carry a "center" (we
    always request `out center;`). Elements with neither are skipped rather
    than raising -- a partial/odd response shouldn't blow up the whole
    search.
    """
    beaches: list[BeachElement] = []
    for element in payload.get("elements", []):
        el_type = element.get("type")
        el_id = element.get("id")
        if el_type is None or el_id is None:
            continue

        if el_type == "node":
            lat = element.get("lat")
            lon = element.get("lon")
        else:
            center = element.get("center") or {}
            lat = center.get("lat")
            lon = center.get("lon")

        if lat is None or lon is None:
            continue

        tags = element.get("tags") or {}
        name = tags.get("name")

        beaches.append(
            BeachElement(
                osm_id=f"{el_type}/{el_id}",
                lat=float(lat),
                lon=float(lon),
                name=name,
            )
        )
    return beaches


class HttpOverpassClient:
    """Talks to the real Overpass API, with retry/backoff on 429/504.

    If every retry is exhausted, returns an empty list rather than raising --
    per the spec, an Overpass timeout should mean "return what accumulated
    so far", not a hard failure of the whole request.
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

    async def search(self, lat: float, lon: float, radius_km: float) -> list[BeachElement]:
        query = build_overpass_query(lat, lon, radius_km)
        client = await self._get_client()
        headers = {"User-Agent": OVERPASS_USER_AGENT}

        for attempt in range(OVERPASS_MAX_RETRIES):
            try:
                response = await client.post(
                    OVERPASS_URL, data={"data": query}, headers=headers
                )
            except httpx.TimeoutException:
                if attempt == OVERPASS_MAX_RETRIES - 1:
                    return []
                await asyncio.sleep(OVERPASS_BACKOFF_BASE_SECONDS * (2**attempt))
                continue

            if response.status_code in OVERPASS_RETRY_STATUS_CODES:
                if attempt == OVERPASS_MAX_RETRIES - 1:
                    return []
                await asyncio.sleep(OVERPASS_BACKOFF_BASE_SECONDS * (2**attempt))
                continue

            if response.status_code >= 400:
                # Non-retryable error (bad query, etc) -- don't fail the
                # whole beach search, just report nothing found this band.
                return []

            try:
                payload = response.json()
            except ValueError:
                return []
            return parse_overpass_response(payload)

        return []


def tile_key(lat: float, lon: float, tile_size_deg: float = TILE_SIZE_DEG) -> tuple[int, int]:
    """Map a coordinate to a coarse grid cell so nearby users share cache
    entries for the "same" patch of coastline."""
    return (
        math.floor(lat / tile_size_deg),
        math.floor(lon / tile_size_deg),
    )


class CachingOverpassClient:
    """Wraps a BeachSearchClient with the tile cache + single-flight lock.

    Cache key is (tile, radius_km) so repeated searches at the same radius
    band near the same location are served from cache. Single-flight is per
    cache key: concurrent requests for the same tile+band await one fetch
    instead of stampeding Overpass.
    """

    def __init__(
        self,
        inner: Any,
        tile_cache: TTLCache[tuple[tuple[int, int], float], list[BeachElement]],
        locks: KeyedLock | None = None,
    ):
        self._inner = inner
        self._cache = tile_cache
        self._locks = locks or KeyedLock()

    async def search(self, lat: float, lon: float, radius_km: float) -> list[BeachElement]:
        key = (tile_key(lat, lon), radius_km)

        cached = self._cache.get(key)
        if cached is not None:
            return cached

        lock = await self._locks.acquire(key)
        async with lock:
            # Re-check: another request may have populated the cache while
            # we were waiting for the lock (single-flight).
            cached = self._cache.get(key)
            if cached is not None:
                return cached

            result = await self._inner.search(lat, lon, radius_km)
            self._cache.set(key, result)
            return result
