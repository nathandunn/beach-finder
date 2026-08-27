"""Arrival / +1h / +3h scoring (SPEC v0.3) -- the Oregon Beach App's
signature feature, re-derived for Open-Meteo's hourly-array shape instead
of NWS's period-list shape.

Row selection: Open-Meteo is requested with `forecast_hours=N`
(config.HOURLY_FORECAST_HOURS), which returns N hourly rows starting at
the *current* hour -- so `hourly[0]` already represents "now", with no
separate lookup needed to find "now" inside the array.

A drive time in minutes is converted to an hour offset by rounding to the
nearest whole hour: `round(drive_time_minutes / 60)`. Note this is
Python's banker's rounding (round-half-to-even), so a drive time of
exactly 30 minutes rounds *down* to offset 0 (round(0.5) == 0), while 90
minutes rounds *up* to offset 2 (round(1.5) == 2) -- both are "nearest
hour", just not always in the intuitive direction on an exact .5 boundary.
This is a coarse-on-coarse heuristic (drive time is itself a 45mph
guess), so exact tie-breaking on the half-hour doesn't materially change
the result, but it's worth documenting since it's not simply "round half
up". +1h and +3h are just that offset plus 1 and plus 3.

Clamping: if an offset (arrival, or arrival+3) would index past the end of
the returned hourly array -- a very long drive time, or a beach near the
500-mile search ceiling combined with the +3h lookahead -- it clamps to
the last available row instead of raising. A beach that far out gets a
score based on the furthest-out forecast data available rather than an
error.
"""
from __future__ import annotations

from .models import HourlyPoint, TimeBasedScores, WeatherConditions
from .scoring import compute_score


def select_hourly_index(hourly_len: int, drive_time_minutes: float, hours_after_arrival: int = 0) -> int:
    """Nearest-hour index into an hourly array where index 0 is "now".
    Clamped to [0, hourly_len - 1]; returns 0 if the array is empty (callers
    should check length before indexing with this)."""
    if hourly_len <= 0:
        return 0
    offset_hours = round(drive_time_minutes / 60) + hours_after_arrival
    return max(0, min(offset_hours, hourly_len - 1))


def _score_hourly_row(row: HourlyPoint, wave_height_m: float | None) -> int:
    """Applies the existing 0-100 formula (app/scoring.py, unchanged) to a
    forecast row. Open-Meteo's marine (wave) call is current-only and
    unchanged per spec -- there's no forecasted wave height -- so forecast
    rows borrow the beach's *current* wave reading rather than dropping the
    factor. Wave height changes slowly relative to a 3-hour window, so this
    is a reasonable stand-in rather than a real forecast."""
    synthetic = WeatherConditions(
        temperature_f=row.temperature_f,
        wind_mph=row.wind_mph,
        precipitation_mm=row.precipitation_mm,
        cloud_cover_pct=row.cloud_cover_pct,
        wave_height_m=wave_height_m,
    )
    return compute_score(synthetic)


def compute_time_based_scores(
    hourly: "tuple[HourlyPoint, ...] | list[HourlyPoint]",
    wave_height_m: float | None,
    drive_time_minutes: float,
) -> TimeBasedScores:
    """Computes arrival/+1h/+3h scores from an hourly forecast array. If no
    hourly data is available at all (e.g. a fully-degraded weather fetch),
    falls back to neutral conditions for all three rather than raising --
    weather is best-effort throughout this app, and one beach's forecast
    gap shouldn't break the whole response."""
    if not hourly:
        neutral = WeatherConditions(temperature_f=65.0, wind_mph=5.0, precipitation_mm=0.0, cloud_cover_pct=50.0)
        neutral_score = compute_score(neutral)
        return TimeBasedScores(arrival=neutral_score, plus1h=neutral_score, plus3h=neutral_score)

    n = len(hourly)
    arrival_idx = select_hourly_index(n, drive_time_minutes, 0)
    plus1h_idx = select_hourly_index(n, drive_time_minutes, 1)
    plus3h_idx = select_hourly_index(n, drive_time_minutes, 3)

    return TimeBasedScores(
        arrival=_score_hourly_row(hourly[arrival_idx], wave_height_m),
        plus1h=_score_hourly_row(hourly[plus1h_idx], wave_height_m),
        plus3h=_score_hourly_row(hourly[plus3h_idx], wave_height_m),
    )
