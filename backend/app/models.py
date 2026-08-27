"""Internal dataclasses shared across the search/weather/scoring pipeline.

These are deliberately plain dataclasses (not pydantic) because they're
produced and consumed entirely server-side; the pydantic schemas in
schemas.py are the API-facing shapes.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class BeachElement:
    """A single beach as returned by an Overpass-like client, already
    reduced to just what we need: a stable id, a point, a name, and
    (SPEC v0.3) a best-effort city/locality from OSM tags."""

    osm_id: str  # e.g. "way/405053738" -- unique across element types
    lat: float
    lon: float
    name: str | None = None
    city: str | None = None


@dataclass(frozen=True)
class HourlyPoint:
    """One hourly forecast row from Open-Meteo's `hourly` block, already
    unit-converted like the `current` fields. Index 0 of a beach's hourly
    list is always the current hour (see config.HOURLY_FORECAST_HOURS and
    weather.py) -- there's no separate "time zero" marker, the array
    position *is* the marker."""

    time: str
    temperature_f: float
    wind_mph: float
    precipitation_mm: float
    cloud_cover_pct: float


@dataclass(frozen=True)
class TimeBasedScores:
    """The Oregon Beach App's signature feature: score conditions at
    arrival (now + drive time), and at arrival +1h / +3h. See
    app/forecast.py for how the hourly row is picked."""

    arrival: int
    plus1h: int
    plus3h: int


@dataclass(frozen=True)
class WeatherConditions:
    temperature_f: float
    wind_mph: float
    precipitation_mm: float
    cloud_cover_pct: float
    wind_direction_deg: float = 0.0
    humidity_pct: float = 70.0
    wave_height_m: float | None = None
    # Tuple, not list, so this dataclass can stay frozen/hashable-friendly
    # without needing a default_factory.
    hourly: tuple[HourlyPoint, ...] = ()


@dataclass
class ScoredBeach:
    osm_id: str
    name: str
    city: str | None
    lat: float
    lon: float
    distance_km: float
    drive_time_minutes: int
    score: int
    scores: TimeBasedScores
    conditions: WeatherConditions
    summary: str
    hourly_forecast: list[HourlyPoint]


@dataclass
class SearchOutcome:
    """Result of the tiered-radius accumulator, before weather/scoring."""

    beaches: list[BeachElement] = field(default_factory=list)
    bands_used_km: list[float] = field(default_factory=list)
    ceiling_reached: bool = False
    target_reached: bool = False


@dataclass
class FindBeachesResult:
    """Final result of a full /beaches lookup: scored, ranked, and trimmed."""

    beaches: list[ScoredBeach] = field(default_factory=list)
    bands_used_km: list[float] = field(default_factory=list)
    ceiling_reached: bool = False
    target_reached: bool = False
