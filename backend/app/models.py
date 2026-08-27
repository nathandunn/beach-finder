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
    reduced to just what we need: a stable id, a point, and a name."""

    osm_id: str  # e.g. "way/405053738" -- unique across element types
    lat: float
    lon: float
    name: str | None = None


@dataclass(frozen=True)
class WeatherConditions:
    temperature_f: float
    wind_mph: float
    precipitation_mm: float
    cloud_cover_pct: float
    wave_height_m: float | None = None


@dataclass
class ScoredBeach:
    osm_id: str
    name: str
    lat: float
    lon: float
    distance_km: float
    score: int
    conditions: WeatherConditions
    summary: str


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
