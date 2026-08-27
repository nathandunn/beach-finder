"""Pydantic response models for the public API."""
from __future__ import annotations

from pydantic import BaseModel


class HourlyForecastOut(BaseModel):
    """One hour of forecast, for the "next hours" list on the card."""

    time: str
    temperature_f: float
    wind_mph: float
    precipitation_mm: float
    cloud_cover_pct: float


class ScoresOut(BaseModel):
    """Time-based scores (SPEC v0.3): conditions at arrival (now + drive
    time), and at arrival +1h / +3h. The current-conditions score is
    unchanged and stays at the top level as `BeachOut.score`."""

    arrival: int
    plus1h: int
    plus3h: int


class ConditionsOut(BaseModel):
    temperature_f: float
    wind_mph: float
    wind_direction_deg: float
    wind_compass: str
    humidity_pct: float
    precipitation_mm: float
    cloud_cover_pct: float
    wave_height_m: float | None = None
    summary: str


class BeachOut(BaseModel):
    id: str
    name: str
    city: str | None = None
    lat: float
    lon: float
    distance_km: float
    drive_time_minutes: int
    score: int
    # SPEC v0.4: "ocean" | "lake" | "river" | "unknown", from nearby OSM
    # water features -- never guessed, "unknown" when nothing conclusive
    # was found nearby (see app/watertype.py).
    water_type: str = "unknown"
    scores: ScoresOut
    conditions: ConditionsOut
    hourly_forecast: list[HourlyForecastOut]


class BeachesResponse(BaseModel):
    beaches: list[BeachOut]
    count: int
    searched_radius_km: float
    bands_used_km: list[float]
    ceiling_reached: bool


class HealthResponse(BaseModel):
    status: str = "ok"
