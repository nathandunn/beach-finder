"""Wind direction: degrees -> an 8-point compass abbreviation, the way the
Oregon Beach App renders wind direction on the card. Oregon got letter
codes directly from NWS ("NW"); Open-Meteo gives degrees instead
(`wind_direction_10m`), so this converts. The frontend spells the
abbreviation out into a full word for display (mirroring Oregon's
BeachCard getWindDirection), the same split between "backend carries the
short code, frontend prettifies it for prose" as the original.
"""
from __future__ import annotations

_POINTS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def degrees_to_compass(degrees: float) -> str:
    """0/360 = N, 90 = E, 180 = S, 270 = W. Eight 45-degree buckets
    centered on each point (e.g. 337.5-22.5 = N, 22.5-67.5 = NE)."""
    normalized = degrees % 360
    index = int((normalized + 22.5) // 45) % 8
    return _POINTS[index]
