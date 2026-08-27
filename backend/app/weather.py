"""Open-Meteo client: current weather + optional marine (wave height) data,
response parsing, and a caching wrapper for the per-beach weather cache.
"""
from __future__ import annotations

import asyncio
from typing import Any, Protocol

import httpx

from .cache import TTLCache
from .config import (
    HOURLY_FORECAST_HOURS,
    OPEN_METEO_BACKOFF_BASE_SECONDS,
    OPEN_METEO_MARINE_URL,
    OPEN_METEO_MAX_RETRIES,
    OPEN_METEO_RETRY_STATUS_CODES,
    OPEN_METEO_TIMEOUT_SECONDS,
    OPEN_METEO_URL,
    WEATHER_CACHE_COORD_PRECISION,
)
from .models import HourlyPoint, WeatherConditions


def parse_hourly_block(hourly: dict[str, Any]) -> list[HourlyPoint]:
    """Parse an Open-Meteo `hourly` block (parallel arrays keyed by field
    name) into a list of HourlyPoint, one per hour, in the order returned.

    Requested with `forecast_hours=N` (see get_conditions below), so index
    0 is the current hour -- that's what makes the arrival/+1h/+3h row
    selection in app/forecast.py just an index into this list.

    Zips to the shortest array rather than assuming they're all the same
    length, and defaults any missing individual value the same way
    parse_open_meteo_response does for `current` -- a partial hourly
    response shouldn't blow up the whole beach's forecast.
    """
    times = hourly.get("time") or []
    temps = hourly.get("temperature_2m") or []
    winds = hourly.get("wind_speed_10m") or []
    precip = hourly.get("precipitation") or []
    clouds = hourly.get("cloud_cover") or []

    points: list[HourlyPoint] = []
    for t, temp, wind, rain, cloud in zip(times, temps, winds, precip, clouds):
        points.append(
            HourlyPoint(
                time=str(t),
                temperature_f=float(temp) if temp is not None else 65.0,
                wind_mph=float(wind) if wind is not None else 5.0,
                precipitation_mm=float(rain) if rain is not None else 0.0,
                cloud_cover_pct=float(cloud) if cloud is not None else 50.0,
            )
        )
    return points


def parse_open_meteo_response(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract the fields we care about from an Open-Meteo response: the
    `current` block (current conditions) and the `hourly` block (used for
    arrival/+1h/+3h scoring and the "next hours" forecast on the card).

    Raises KeyError/TypeError-free: missing fields just fall back to
    reasonable defaults, since a partial response shouldn't sink the whole
    beach's score.
    """
    current = payload.get("current", {})
    wind_direction = current.get("wind_direction_10m")
    humidity = current.get("relative_humidity_2m")
    return {
        "temperature_f": float(current.get("temperature_2m", 65.0)),
        "wind_mph": float(current.get("wind_speed_10m", 5.0)),
        "wind_direction_deg": float(wind_direction) if wind_direction is not None else 0.0,
        "humidity_pct": float(humidity) if humidity is not None else 70.0,
        "precipitation_mm": float(current.get("precipitation", 0.0) or 0.0),
        "cloud_cover_pct": float(current.get("cloud_cover", 50.0)),
        "hourly": parse_hourly_block(payload.get("hourly") or {}),
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
        # SPEC v0.3 constraint: current + hourly in the SAME request (one
        # Open-Meteo call per beach, unchanged) -- `current` and `hourly`
        # are just two more query params on the one /forecast call.
        # `forecast_hours` caps how many hourly rows come back (see
        # config.HOURLY_FORECAST_HOURS) so the response stays small and the
        # array's length is predictable for the arrival/+1h/+3h clamp.
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,wind_speed_10m,wind_direction_10m,relative_humidity_2m,precipitation,cloud_cover",
            "hourly": "temperature_2m,wind_speed_10m,wind_direction_10m,relative_humidity_2m,precipitation,cloud_cover",
            "forecast_hours": HOURLY_FORECAST_HOURS,
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
                "wind_direction_deg": 0.0,
                "humidity_pct": 70.0,
                "precipitation_mm": 0.0,
                "cloud_cover_pct": 50.0,
                "hourly": [],
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

        hourly = tuple(fields.pop("hourly"))
        return WeatherConditions(wave_height_m=wave_height_m, hourly=hourly, **fields)


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
