"""Arrival / +1h / +3h scoring (SPEC v0.3): picking the right hourly row for
a given drive time, and applying the existing scoring formula to it.

Row selection: hourly[0] is "now" (Open-Meteo is requested with
`forecast_hours=N`, which starts the array at the current hour -- see
config.HOURLY_FORECAST_HOURS and weather.py). A drive time in minutes maps
to an hour offset via `round(minutes / 60)`; +1h/+3h just add 1/3 to that.
"""
from app.forecast import compute_time_based_scores, select_hourly_index
from app.models import HourlyPoint, WeatherConditions
from app.scoring import compute_score


def make_row(temp: float, wind: float = 8.0, precip: float = 0.0, cloud: float = 10.0) -> HourlyPoint:
    return HourlyPoint(time="t", temperature_f=temp, wind_mph=wind, precipitation_mm=precip, cloud_cover_pct=cloud)


class TestSelectHourlyIndex:
    def test_zero_drive_time_selects_now(self):
        assert select_hourly_index(10, 0) == 0

    def test_rounds_to_nearest_hour(self):
        assert select_hourly_index(10, 40) == 1  # 40/60 = 0.667h -> rounds to 1

    def test_banker_rounding_on_exact_half_hour_boundaries(self):
        # round() is round-half-to-even: 30 min = 0.5h rounds to 0 (even),
        # 90 min = 1.5h rounds to 2 (even). Both are "nearest hour", just
        # not always rounding the half up -- documented in forecast.py.
        assert select_hourly_index(10, 30) == 0
        assert select_hourly_index(10, 90) == 2

    def test_hours_after_arrival_offsets_the_index(self):
        base = select_hourly_index(10, 40)  # 1
        assert select_hourly_index(10, 40, hours_after_arrival=1) == base + 1
        assert select_hourly_index(10, 40, hours_after_arrival=3) == base + 3

    def test_clamps_to_last_row_when_drive_time_exceeds_horizon(self):
        # 10 hours of drive time into a 5-row (5-hour) hourly array.
        assert select_hourly_index(5, 600) == 4

    def test_clamps_plus3h_independently_when_only_it_overflows(self):
        # Arrival fits inside the array; +3h pushes past the end.
        assert select_hourly_index(5, 60, hours_after_arrival=3) == 4  # 1 + 3 = 4, exactly last
        assert select_hourly_index(5, 120, hours_after_arrival=3) == 4  # 2 + 3 = 5 -> clamped

    def test_empty_array_returns_zero_rather_than_raising(self):
        assert select_hourly_index(0, 500) == 0


class TestComputeTimeBasedScores:
    def test_picks_the_right_row_for_arrival_plus1h_plus3h(self):
        hourly = [make_row(t) for t in [50, 60, 70, 80, 90, 95]]
        # drive_time=60min -> arrival index 1 (temp 60), +1h index 2 (70),
        # +3h index 4 (90).
        scores = compute_time_based_scores(hourly, wave_height_m=None, drive_time_minutes=60)

        assert scores.arrival == compute_score(
            WeatherConditions(temperature_f=60, wind_mph=8.0, precipitation_mm=0.0, cloud_cover_pct=10.0)
        )
        assert scores.plus1h == compute_score(
            WeatherConditions(temperature_f=70, wind_mph=8.0, precipitation_mm=0.0, cloud_cover_pct=10.0)
        )
        assert scores.plus3h == compute_score(
            WeatherConditions(temperature_f=90, wind_mph=8.0, precipitation_mm=0.0, cloud_cover_pct=10.0)
        )

    def test_carries_over_current_wave_height_into_every_row(self):
        # Marine (wave) data is current-only/unchanged per spec -- there's
        # no forecasted wave height, so forecast rows reuse the beach's
        # current reading.
        hourly = [make_row(70)]
        with_wave = compute_time_based_scores(hourly, wave_height_m=0.2, drive_time_minutes=0)
        without_wave = compute_time_based_scores(hourly, wave_height_m=None, drive_time_minutes=0)

        assert with_wave.arrival == compute_score(
            WeatherConditions(
                temperature_f=70, wind_mph=8.0, precipitation_mm=0.0, cloud_cover_pct=10.0, wave_height_m=0.2
            )
        )
        assert without_wave.arrival == compute_score(
            WeatherConditions(
                temperature_f=70, wind_mph=8.0, precipitation_mm=0.0, cloud_cover_pct=10.0, wave_height_m=None
            )
        )
        assert with_wave.arrival != without_wave.arrival

    def test_drive_time_longer_than_forecast_horizon_clamps_to_last_row(self):
        # Only 4 hours of hourly data, but a 10-hour drive time (e.g. a
        # beach found near the 500-mile search ceiling). Arrival, +1h, and
        # +3h should all land on the same last row rather than raising.
        hourly = [make_row(t) for t in [50, 55, 60, 65]]
        scores = compute_time_based_scores(hourly, wave_height_m=None, drive_time_minutes=600)

        last_row_score = compute_score(
            WeatherConditions(temperature_f=65, wind_mph=8.0, precipitation_mm=0.0, cloud_cover_pct=10.0)
        )
        assert scores.arrival == last_row_score
        assert scores.plus1h == last_row_score
        assert scores.plus3h == last_row_score

    def test_empty_hourly_falls_back_to_neutral_scores_for_all_three(self):
        scores = compute_time_based_scores([], wave_height_m=None, drive_time_minutes=45)
        assert scores.arrival == scores.plus1h == scores.plus3h
        assert 0 <= scores.arrival <= 100

    def test_scores_stay_within_0_to_100(self):
        hourly = [
            make_row(30, wind=40, precip=20, cloud=100),
            make_row(90, wind=0, precip=0, cloud=0),
            make_row(65, wind=10, precip=0.5, cloud=50),
        ]
        scores = compute_time_based_scores(hourly, wave_height_m=1.0, drive_time_minutes=0)
        for value in (scores.arrival, scores.plus1h, scores.plus3h):
            assert 0 <= value <= 100
