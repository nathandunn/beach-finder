"""FastAPI app: GET /api/beaches, GET /api/health.

Wires the real Overpass + Open-Meteo clients through the caching wrappers
into a single BeachFinderService instance held on app.state, created once
at startup and reused across requests (so the in-memory caches actually
help).
"""
from __future__ import annotations

import asyncio
import datetime as _dt
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from .cache import KeyedLock, TTLCache
from .compass import degrees_to_compass
from .config import TILE_CACHE_TTL_SECONDS, WATER_TYPE_CACHE_TTL_SECONDS, WEATHER_CACHE_TTL_SECONDS
from .overpass import CachingOverpassClient, HttpOverpassClient
from .schemas import (
    BeachesResponse,
    BeachOut,
    ConditionsOut,
    HealthResponse,
    HourlyForecastOut,
    ScoresOut,
)
from .results_store import ResultsStore, place_key
from .service import BeachFinderService
from .watertype import CachingWaterTypeClient, HttpWaterTypeClient
from .weather import CachingWeatherClient, HttpWeatherClient


@asynccontextmanager
async def lifespan(app: FastAPI):
    overpass_http = HttpOverpassClient()
    weather_http = HttpWeatherClient()
    water_type_http = HttpWaterTypeClient()

    tile_cache = TTLCache(ttl_seconds=TILE_CACHE_TTL_SECONDS)
    weather_cache = TTLCache(ttl_seconds=WEATHER_CACHE_TTL_SECONDS)
    water_type_cache = TTLCache(ttl_seconds=WATER_TYPE_CACHE_TTL_SECONDS)

    search_client = CachingOverpassClient(overpass_http, tile_cache, KeyedLock())
    weather_client = CachingWeatherClient(weather_http, weather_cache, KeyedLock())
    water_type_client = CachingWaterTypeClient(water_type_http, water_type_cache, KeyedLock())

    app.state.service = BeachFinderService(search_client, weather_client, water_type_client)
    # v0.6: whole answers per place, served until 30 minutes old.
    results_store = ResultsStore()
    results_store.load()
    app.state.results_store = results_store
    app.state.background = set()
    app.state.tile_cache = tile_cache
    app.state.weather_cache = weather_cache
    app.state.water_type_cache = water_type_cache

    try:
        yield
    finally:
        await overpass_http.aclose()
        await weather_http.aclose()
        await water_type_http.aclose()


app = FastAPI(title="Beach Finder API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()


def _iso(ts: float) -> str:
    return _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc).isoformat(timespec="seconds")


def _with_freshness(payload: dict, entry, store: ResultsStore, cached: bool) -> dict:
    out = dict(payload)
    out["cached"] = cached
    out["fetched_at"] = _iso(entry.fetched_at)
    out["age_seconds"] = int(store.age_seconds(entry))
    out["stale_after_seconds"] = int(store.ttl_seconds)
    out["water_types_pending"] = not entry.complete
    return out


@app.get("/api/beaches", response_model=BeachesResponse)
async def get_beaches(
    lat: float = Query(..., ge=-90, le=90, description="Latitude in decimal degrees"),
    lon: float = Query(..., ge=-180, le=180, description="Longitude in decimal degrees"),
) -> dict:
    """Fetch-and-check: expel stale answers, serve a fresh stored one, or
    fetch, store and serve. Only this path ever touches the store."""
    service: BeachFinderService = app.state.service
    store: ResultsStore = app.state.results_store
    key = place_key(lat, lon)

    entry = store.get(key)  # expels every stale entry first
    if entry is not None:
        return _with_freshness(entry.payload, entry, store, cached=True)

    lock = await store.lock(key)
    async with lock:
        entry = store.get(key)
        if entry is not None:
            return _with_freshness(entry.payload, entry, store, cached=True)

        result = await service.find_beaches(lat, lon)
        payload = _build_response(result).model_dump()
        pending = result.pending_water_types
        entry = store.put(key, payload, complete=pending is None)

    if pending is not None:
        _patch_later(key, pending)

    return _with_freshness(entry.payload, entry, store, cached=False)


def _patch_later(key: str, pending) -> None:
    """When the late water-type answer arrives, write it onto the stored
    entry (its fetched_at is untouched -- the weather is no fresher)."""
    store: ResultsStore = app.state.results_store

    async def waiter() -> None:
        try:
            water_types = await pending
        except Exception:
            return
        if water_types:
            store.patch_water_types(key, water_types)

    task = asyncio.ensure_future(waiter())
    app.state.background.add(task)
    task.add_done_callback(app.state.background.discard)


def _build_response(result) -> BeachesResponse:
    beaches_out = [
        BeachOut(
            id=b.osm_id,
            name=b.name,
            city=b.city,
            lat=b.lat,
            lon=b.lon,
            distance_km=b.distance_km,
            drive_time_minutes=b.drive_time_minutes,
            score=b.score,
            water_type=b.water_type,
            scores=ScoresOut(
                arrival=b.scores.arrival,
                plus1h=b.scores.plus1h,
                plus3h=b.scores.plus3h,
            ),
            conditions=ConditionsOut(
                temperature_f=b.conditions.temperature_f,
                wind_mph=b.conditions.wind_mph,
                wind_direction_deg=b.conditions.wind_direction_deg,
                wind_compass=degrees_to_compass(b.conditions.wind_direction_deg),
                humidity_pct=b.conditions.humidity_pct,
                precipitation_mm=b.conditions.precipitation_mm,
                cloud_cover_pct=b.conditions.cloud_cover_pct,
                wave_height_m=b.conditions.wave_height_m,
                summary=b.summary,
            ),
            hourly_forecast=[
                HourlyForecastOut(
                    time=h.time,
                    temperature_f=h.temperature_f,
                    wind_mph=h.wind_mph,
                    precipitation_mm=h.precipitation_mm,
                    cloud_cover_pct=h.cloud_cover_pct,
                )
                for h in b.hourly_forecast
            ],
        )
        for b in result.beaches
    ]

    searched_radius_km = result.bands_used_km[-1] if result.bands_used_km else 0.0

    return BeachesResponse(
        beaches=beaches_out,
        count=len(beaches_out),
        searched_radius_km=searched_radius_km,
        bands_used_km=result.bands_used_km,
        ceiling_reached=result.ceiling_reached,
    )
