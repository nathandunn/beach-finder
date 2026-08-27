"""Open-Meteo client: current weather + optional marine (wave height) data,
response parsing, and a caching wrapper for the per-beach weather cache.
"""
from __future__ import annotations

import asyncio
from typing import Any, Protocol

import httpx

from .cache import TTLCache
from .config import (
    OPEN_METEO_BACKOFF_BASE_SECONDS,
    OPEN_METEO_MARINE_URL,
    OPEN_METEO_MAX_RETRIES,
    OPEN_METEO_RETRY_STATUS_CODES,
    OPEN_METEO_TIMEOUT_SECONDS,
    OPEN_METEO_URL,
    WEATHER_CACHE_COORD_PRECISION,
)
from .models import WeatherConditions


def parse_open_meteo_response(payload: dict[str, Any]) -> dict[str, float]:
    """Extract the fields we care about from an Open-Meteo `current` block.

    Raises KeyError/TypeError-free: missing fields just fall back to
    reasonable defaults, since a partial response shouldn't sink the whole
    beach's score.
    """
    current = payload.get("current", {})
    return {
        "temperature_f": float(current.get("temperature_2m", 65.0)),
        "wind_mph": float(current.get("wind_speed_10m", 5.0)),
        "precipitation_mm": float(current.get("precipitation", 0.0) or 0.0),
        "cloud_cover_pct": float(current.get("cloud_cover", 50.0)),
    }


def parse_marine_response(payload: dict[str, Any]) -> float | None:
    """Extract wave height in meters, or None if unavailable (marine data
    only covers ocean/sea points -- inland or some coastal points return
    null or omit the field entirely)."""
    current = payload.get("current", {})
    wave = current.get("wave_height")
    if wave is None:
        return None
    try:
        return float(wave)
    except (TypeError, ValueError):
        return None


class WeatherClient(Protocol):
    async def get_conditions(self, lat: float, lon: float) -> WeatherConditions:
        ...


class HttpWeatherClient:
    """Fetches current weather and, best-effort, marine wave height."""

    def __init__(self, http_client: httpx.AsyncClient | None = None):
        self._client = http_client

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=OPEN_METEO_TIMEOUT_SECONDS)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def _get_with_retry(self, url: str, params: dict) -> httpx.Response | None:
        """GET with retry/backoff on 429/504, mirroring the Overpass
        client's politeness -- Open-Meteo is free and keyless too, and a
        single /api/beaches request can fan out to dozens of these calls."""
        client = await self._get_client()

        for attempt in range(OPEN_METEO_MAX_RETRIES):
            try:
                response = await client.get(url, params=params)
            except httpx.TimeoutException:
                if attempt == OPEN_METEO_MAX_RETRIES - 1:
                    return None
                await asyncio.sleep(OPEN_METEO_BACKOFF_BASE_SECONDS * (2**attempt))
                continue

            if response.status_code in OPEN_METEO_RETRY_STATUS_CODES:
                if attempt == OPEN_METEO_MAX_RETRIES - 1:
                    return response
                await asyncio.sleep(OPEN_METEO_BACKOFF_BASE_SECONDS * (2**attempt))
                continue

            return response

        return None

    async def get_conditions(self, lat: float, lon: float) -> WeatherConditions:
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,wind_speed_10m,precipitation,cloud_cover",
            "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph",
        }
        try:
            response = await self._get_with_retry(OPEN_METEO_URL, params)
            if response is None:
                raise httpx.HTTPError("no response after retries")
            response.raise_for_status()
            fields = parse_open_meteo_response(response.json())
        except (httpx.HTTPError, ValueError):
            # Weather is best-effort per beach; fall back to neutral values
            # rather than failing the whole beach search over one bad call.
            fields = {
                "temperature_f": 65.0,
                "wind_mph": 5.0,
                "precipitation_mm": 0.0,
                "cloud_cover_pct": 50.0,
            }

        wave_height_m: float | None = None
        try:
            marine_response = await self._get_with_retry(
                OPEN_METEO_MARINE_URL, {"latitude": lat, "longitude": lon, "current": "wave_height"}
            )
            if marine_response is not None and marine_response.status_code == 200:
                wave_height_m = parse_marine_response(marine_response.json())
        except httpx.HTTPError:
            wave_height_m = None

        return WeatherConditions(wave_height_m=wave_height_m, **fields)


def weather_cache_key(lat: float, lon: float) -> tuple[float, float]:
    precision = WEATHER_CACHE_COORD_PRECISION
    return (round(lat, precision), round(lon, precision))


class CachingWeatherClient:
    """Wraps a WeatherClient with the ~30-min per-beach weather cache and a
    single-flight lock so concurrent requests for the same coordinate don't
    each hit Open-Meteo."""

    def __init__(self, inner: WeatherClient, cache: TTLCache[tuple[float, float], WeatherConditions], locks=None):
        from .cache import KeyedLock

        self._inner = inner
        self._cache = cache
        self._locks = locks or KeyedLock()

    async def get_conditions(self, lat: float, lon: float) -> WeatherConditions:
        key = weather_cache_key(lat, lon)

        cached = self._cache.get(key)
        if cached is not None:
            return cached

        lock = await self._locks.acquire(key)
        async with lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached

            result = await self._inner.get_conditions(lat, lon)
            self._cache.set(key, result)
            return result
