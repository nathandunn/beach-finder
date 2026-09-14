"""Ties the tiered search, weather fetch, and scoring together into one
`find_beaches` call. This is the layer main.py's endpoint calls; it doesn't
know or care whether its clients are real (HTTP) or fake (tests)."""
from __future__ import annotations

import asyncio

from .config import (
    DEFAULT_RADIUS_BANDS_KM,
    DEFAULT_TARGET_COUNT,
    HOURLY_FORECAST_DISPLAY_HOURS,
    MAX_CANDIDATES,
    MAX_RADIUS_KM,
    WATER_TYPE_GRACE_SECONDS,
    WEATHER_FETCH_CONCURRENCY,
)
from .forecast import compute_time_based_scores
from .geo import estimate_drive_time_minutes, haversine_km
from .models import BeachElement, FindBeachesResult, ScoredBeach
from .scoring import compute_score, summarize_conditions
from .search import BeachSearchClient, tiered_search
from .watertype import WaterTypeClient
from .weather import WeatherClient


class _NullWaterTypeClient:
    """Default water-type client when none is supplied: classifies every
    beach as "unknown" without making any request. Keeps
    `BeachFinderService` constructible (e.g. in tests exercising other
    behavior) without forcing every caller to wire up SPEC v0.4's
    classification client -- never guesses, just reports nothing found."""

    async def classify(self, beaches: list[BeachElement]) -> dict[str, str]:
        return {beach.osm_id: "unknown" for beach in beaches}


class BeachFinderService:
    def __init__(
        self,
        search_client: BeachSearchClient,
        weather_client: WeatherClient,
        water_type_client: WaterTypeClient | None = None,
        target_count: int = DEFAULT_TARGET_COUNT,
        bands_km: list[float] | None = None,
        ceiling_km: float = MAX_RADIUS_KM,
        max_candidates: int = MAX_CANDIDATES,
        water_type_grace_seconds: float | None = WATER_TYPE_GRACE_SECONDS,
    ):
        self._search_client = search_client
        self._weather_client = weather_client
        self._water_type_client = water_type_client or _NullWaterTypeClient()
        self._target_count = target_count
        self._max_candidates = max(target_count, max_candidates)
        self._water_type_grace_seconds = water_type_grace_seconds
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

        # SPEC v0.4: classification is a single batched query covering all
        # beaches found this search. Kick it off as its own task so it
        # runs concurrently with the per-beach weather fetches below
        # rather than adding to the critical path -- it's merged onto the
        # scored beaches after both finish. A search-failure here (or an
        # empty beach list) resolves to "unknown" for everyone, per spec,
        # never sinking the whole search.
        # A wide band can hand back hundreds of beaches; only the nearest
        # candidates are worth a weather fetch and a water-type probe.
        candidates = sorted(
            outcome.beaches,
            key=lambda e: haversine_km(lat, lon, e.lat, e.lon),
        )[: self._max_candidates]

        water_types_task = asyncio.ensure_future(self._water_type_client.classify(candidates))

        async def score_one(element: BeachElement) -> ScoredBeach:
            # Bound how many weather fetches are in flight at once -- polite
            # to the free upstream API and avoids the 429s that come from
            # firing dozens of concurrent requests at once.
            async with self._weather_semaphore:
                conditions = await self._weather_client.get_conditions(element.lat, element.lon)
            distance = haversine_km(lat, lon, element.lat, element.lon)
            distance_km = round(distance, 2)
            drive_time_minutes = estimate_drive_time_minutes(distance_km)
            time_based_scores = compute_time_based_scores(
                conditions.hourly, conditions.wave_height_m, drive_time_minutes
            )
            # "Next hours" forecast for the card: the hours *after* now
            # (hourly[0] is "now", already covered by current conditions).
            hourly_forecast = list(conditions.hourly[1 : 1 + HOURLY_FORECAST_DISPLAY_HOURS])
            return ScoredBeach(
                osm_id=element.osm_id,
                name=element.name or "Unnamed Beach",
                city=element.city,
                lat=element.lat,
                lon=element.lon,
                distance_km=distance_km,
                drive_time_minutes=drive_time_minutes,
                score=compute_score(conditions),
                scores=time_based_scores,
                conditions=conditions,
                summary=summarize_conditions(conditions),
                hourly_forecast=hourly_forecast,
            )

        if candidates:
            scored = list(await asyncio.gather(*(score_one(e) for e in candidates)))
        else:
            scored = []

        # Weather is in; give the (Overpass-backed, often slow) water-type
        # query a short grace period, then answer with "unknown" rather
        # than make the user wait on a flaky upstream.
        try:
            if self._water_type_grace_seconds is None:
                water_types = await water_types_task
            else:
                water_types = await asyncio.wait_for(
                    asyncio.shield(water_types_task), self._water_type_grace_seconds
                )
        except asyncio.TimeoutError:
            water_types = {}
            # Let it finish in the background so its result lands in the
            # cache for the next search of this coast.
            water_types_task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        except Exception:
            water_types = {}
        for beach in scored:
            beach.water_type = water_types.get(beach.osm_id, "unknown")

        scored.sort(key=lambda b: (-b.score, b.distance_km))
        trimmed = scored[: self._target_count]

        return FindBeachesResult(
            beaches=trimmed,
            bands_used_km=outcome.bands_used_km,
            ceiling_reached=outcome.ceiling_reached,
            target_reached=outcome.target_reached,
        )
