"""Ties the tiered search, weather fetch, and scoring together into one
`find_beaches` call. This is the layer main.py's endpoint calls; it doesn't
know or care whether its clients are real (HTTP) or fake (tests)."""
from __future__ import annotations

import asyncio

from .config import (
    DEFAULT_RADIUS_BANDS_KM,
    DEFAULT_TARGET_COUNT,
    MAX_RADIUS_KM,
    WEATHER_FETCH_CONCURRENCY,
)
from .geo import haversine_km
from .models import BeachElement, FindBeachesResult, ScoredBeach
from .scoring import compute_score, summarize_conditions
from .search import BeachSearchClient, tiered_search
from .weather import WeatherClient


class BeachFinderService:
    def __init__(
        self,
        search_client: BeachSearchClient,
        weather_client: WeatherClient,
        target_count: int = DEFAULT_TARGET_COUNT,
        bands_km: list[float] | None = None,
        ceiling_km: float = MAX_RADIUS_KM,
    ):
        self._search_client = search_client
        self._weather_client = weather_client
        self._target_count = target_count
        self._bands_km = bands_km
        self._ceiling_km = ceiling_km
        self._weather_semaphore = asyncio.Semaphore(WEATHER_FETCH_CONCURRENCY)

    async def find_beaches(self, lat: float, lon: float) -> FindBeachesResult:
        outcome = await tiered_search(
            self._search_client,
            lat,
            lon,
            target_count=self._target_count,
            bands_km=self._bands_km,
            ceiling_km=self._ceiling_km,
        )

        async def score_one(element: BeachElement) -> ScoredBeach:
            # Bound how many weather fetches are in flight at once -- polite
            # to the free upstream API and avoids the 429s that come from
            # firing dozens of concurrent requests at once.
            async with self._weather_semaphore:
                conditions = await self._weather_client.get_conditions(element.lat, element.lon)
            distance = haversine_km(lat, lon, element.lat, element.lon)
            return ScoredBeach(
                osm_id=element.osm_id,
                name=element.name or "Unnamed Beach",
                lat=element.lat,
                lon=element.lon,
                distance_km=round(distance, 2),
                score=compute_score(conditions),
                conditions=conditions,
                summary=summarize_conditions(conditions),
            )

        if outcome.beaches:
            scored = list(await asyncio.gather(*(score_one(e) for e in outcome.beaches)))
        else:
            scored = []

        scored.sort(key=lambda b: (-b.score, b.distance_km))
        trimmed = scored[: self._target_count]

        return FindBeachesResult(
            beaches=trimmed,
            bands_used_km=outcome.bands_used_km,
            ceiling_reached=outcome.ceiling_reached,
            target_reached=outcome.target_reached,
        )
