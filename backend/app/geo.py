"""Small geo helpers: haversine distance, used both for sorting results and
for reporting distance_km to the client; and (SPEC v0.3) the Oregon Beach
App's drive-time heuristic."""
from __future__ import annotations

import math

from .config import ASSUMED_DRIVE_SPEED_MPH, KM_TO_MILES

EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1_rad, lat2_rad = math.radians(lat1), math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_KM * c


def estimate_drive_time_minutes(distance_km: float) -> int:
    """The Oregon Beach App's drive-time heuristic, unchanged: assume a
    flat 45mph average (no traffic, no routing), `minutes = distance_miles
    / 45 * 60`. Oregon's original truncates via Python's `int()` rather
    than rounding (`int(distance / 45 * 60)`); this keeps that exact
    behavior rather than rounding to the nearest minute, so a straight port
    of the formula produces identical numbers for the same distance."""
    distance_miles = distance_km * KM_TO_MILES
    return int(distance_miles / ASSUMED_DRIVE_SPEED_MPH * 60)
