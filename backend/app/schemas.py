"""Pydantic response models for the public API."""
from __future__ import annotations

from pydantic import BaseModel


class ConditionsOut(BaseModel):
    temperature_f: float
    wind_mph: float
    precipitation_mm: float
    cloud_cover_pct: float
    wave_height_m: float | None = None
    summary: str


class BeachOut(BaseModel):
    id: str
    name: str
    lat: float
    lon: float
    distance_km: float
    score: int
    conditions: ConditionsOut


class BeachesResponse(BaseModel):
    beaches: list[BeachOut]
    count: int
    searched_radius_km: float
    bands_used_km: list[float]
    ceiling_reached: bool


class HealthResponse(BaseModel):
    status: str = "ok"
