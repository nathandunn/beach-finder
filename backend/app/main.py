"""FastAPI app: GET /api/beaches, GET /api/health.

Wires the real Overpass + Open-Meteo clients through the caching wrappers
into a single BeachFinderService instance held on app.state, created once
at startup and reused across requests (so the in-memory caches actually
help).
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from .cache import KeyedLock, TTLCache
from .compass import degrees_to_compass
from .config import TILE_CACHE_TTL_SECONDS, WEATHER_CACHE_TTL_SECONDS
from .overpass import CachingOverpassClient, HttpOverpassClient
from .schemas import (
    BeachesResponse,
    BeachOut,
    ConditionsOut,
    HealthResponse,
    HourlyForecastOut,
    ScoresOut,
)
from .service import BeachFinderService
from .weather import CachingWeatherClient, HttpWeatherClient


@asynccontextmanager
async def lifespan(app: FastAPI):
    overpass_http = HttpOverpassClient()
    weather_http = HttpWeatherClient()

    tile_cache = TTLCache(ttl_seconds=TILE_CACHE_TTL_SECONDS)
    weather_cache = TTLCache(ttl_seconds=WEATHER_CACHE_TTL_SECONDS)

    search_client = CachingOverpassClient(overpass_http, tile_cache, KeyedLock())
    weather_client = CachingWeatherClient(weather_http, weather_cache, KeyedLock())

    app.state.service = BeachFinderService(search_client, weather_client)
    app.state.tile_cache = tile_cache
    app.state.weather_cache = weather_cache

    try:
        yield
    finally:
        await overpass_http.aclose()
        await weather_http.aclose()


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


@app.get("/api/beaches", response_model=BeachesResponse)
async def get_beaches(
    lat: float = Query(..., ge=-90, le=90, description="Latitude in decimal degrees"),
    lon: float = Query(..., ge=-180, le=180, description="Longitude in decimal degrees"),
) -> BeachesResponse:
    service: BeachFinderService = app.state.service
    result = await service.find_beaches(lat, lon)

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
