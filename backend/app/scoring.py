"""The 0-100 beach condition score.

Reuses the *approach* from the Oregon Beach App (readable point buckets per
factor, summed to a 0-100 total) but is re-derived here, not copied: units,
thresholds, weights, and the wave-height factor are all new.

Five factors, each worth a fixed maximum number of points:

    temperature     0-30   warmer is better, up to a comfortable cap
    wind speed       0-25   calmer is better
    precipitation    0-20   drier is better
    cloud cover      0-15   clearer is better
    wave height      0-10   calmer surf is better (omitted if unavailable)

That's 100 points when wave height is available. Open-Meteo's marine API
only covers ocean/sea points, so plenty of valid beaches (lakes, some
coastlines) won't have a wave reading -- when it's missing, the other four
factors (which sum to 90) are rescaled up to a 0-100 range so a beach is
never penalized just for lacking wave data.

Every factor is monotonic in the "obviously good" direction across its
whole domain (e.g. score never drops as it gets warmer, never rises as wind
gets stronger) -- there's no hump shape to worry about, which keeps the
formula easy to reason about and to test.
"""
from __future__ import annotations

from .models import WeatherConditions

TEMPERATURE_MAX_POINTS = 30
WIND_MAX_POINTS = 25
PRECIPITATION_MAX_POINTS = 20
CLOUD_MAX_POINTS = 15
WAVE_MAX_POINTS = 10

_NO_WAVE_TOTAL = TEMPERATURE_MAX_POINTS + WIND_MAX_POINTS + PRECIPITATION_MAX_POINTS + CLOUD_MAX_POINTS  # 90
_RESCALE_WHEN_NO_WAVE = 100 / _NO_WAVE_TOTAL


def score_temperature(temperature_f: float) -> float:
    """Warmer is better up to a comfortable plateau; never decreases as it
    gets warmer (no penalty for a hot day at the beach)."""
    if temperature_f >= 75:
        return 30
    if temperature_f >= 68:
        return 26
    if temperature_f >= 60:
        return 20
    if temperature_f >= 50:
        return 12
    if temperature_f >= 40:
        return 5
    return 0


def score_wind(wind_mph: float) -> float:
    """Calmer is better; never increases as wind picks up."""
    if wind_mph <= 5:
        return 25
    if wind_mph <= 10:
        return 22
    if wind_mph <= 15:
        return 17
    if wind_mph <= 20:
        return 10
    if wind_mph <= 30:
        return 4
    return 0


def score_precipitation(precipitation_mm: float) -> float:
    """Drier is better; never increases as precipitation increases."""
    if precipitation_mm <= 0:
        return 20
    if precipitation_mm <= 0.2:
        return 16
    if precipitation_mm <= 1:
        return 10
    if precipitation_mm <= 4:
        return 4
    return 0


def score_cloud_cover(cloud_cover_pct: float) -> float:
    """Clearer skies are better; never increases as cloud cover increases."""
    if cloud_cover_pct <= 10:
        return 15
    if cloud_cover_pct <= 30:
        return 12
    if cloud_cover_pct <= 60:
        return 8
    if cloud_cover_pct <= 85:
        return 4
    return 0


def score_wave_height(wave_height_m: float) -> float:
    """Calmer surf is better; never increases as waves get bigger."""
    if wave_height_m <= 0.3:
        return 10
    if wave_height_m <= 0.6:
        return 8
    if wave_height_m <= 1.2:
        return 5
    if wave_height_m <= 2.0:
        return 2
    return 0


def compute_score(conditions: WeatherConditions) -> int:
    total = (
        score_temperature(conditions.temperature_f)
        + score_wind(conditions.wind_mph)
        + score_precipitation(conditions.precipitation_mm)
        + score_cloud_cover(conditions.cloud_cover_pct)
    )

    if conditions.wave_height_m is not None:
        total += score_wave_height(conditions.wave_height_m)
    else:
        total *= _RESCALE_WHEN_NO_WAVE

    return round(max(0, min(100, total)))


def summarize_conditions(conditions: WeatherConditions) -> str:
    """A short human-readable line, e.g. '72°F, 8 mph wind, mostly clear'."""
    if conditions.cloud_cover_pct <= 10:
        sky = "clear skies"
    elif conditions.cloud_cover_pct <= 30:
        sky = "mostly clear"
    elif conditions.cloud_cover_pct <= 60:
        sky = "partly cloudy"
    elif conditions.cloud_cover_pct <= 85:
        sky = "mostly cloudy"
    else:
        sky = "overcast"

    parts = [f"{conditions.temperature_f:.0f}°F", f"{conditions.wind_mph:.0f} mph wind", sky]

    if conditions.precipitation_mm > 0:
        parts.append("rain")

    if conditions.wave_height_m is not None:
        parts.append(f"{conditions.wave_height_m:.1f}m waves")

    return ", ".join(parts)
