"""Scoring formula sanity checks: warm+calm+dry beats cold+windy+rainy, and
each factor is monotonic in the expected direction."""
import pytest

from app.models import WeatherConditions
from app.scoring import (
    compute_score,
    score_cloud_cover,
    score_precipitation,
    score_temperature,
    score_wave_height,
    score_wind,
)


def make_conditions(**overrides) -> WeatherConditions:
    defaults = dict(
        temperature_f=70.0,
        wind_mph=8.0,
        precipitation_mm=0.0,
        cloud_cover_pct=10.0,
        wave_height_m=0.5,
    )
    defaults.update(overrides)
    return WeatherConditions(**defaults)


class TestOverallSanity:
    def test_warm_calm_dry_beats_cold_windy_rainy(self):
        great = make_conditions(temperature_f=78, wind_mph=3, precipitation_mm=0, cloud_cover_pct=5, wave_height_m=0.2)
        bad = make_conditions(temperature_f=38, wind_mph=28, precipitation_mm=5, cloud_cover_pct=95, wave_height_m=2.5)
        assert compute_score(great) > compute_score(bad)

    def test_score_bounded_0_to_100(self):
        great = make_conditions(temperature_f=90, wind_mph=0, precipitation_mm=0, cloud_cover_pct=0, wave_height_m=0)
        bad = make_conditions(temperature_f=-10, wind_mph=100, precipitation_mm=50, cloud_cover_pct=100, wave_height_m=10)
        assert 0 <= compute_score(great) <= 100
        assert 0 <= compute_score(bad) <= 100
        assert compute_score(bad) == 0
        assert compute_score(great) == 100

    def test_missing_wave_height_still_scores_0_to_100_and_rescales(self):
        with_wave = make_conditions(wave_height_m=0.2)  # near-max wave points
        without_wave = make_conditions(wave_height_m=None)
        # Same other factors; missing wave data shouldn't tank the score --
        # it should rescale the remaining 90 points up to 100.
        assert compute_score(without_wave) >= compute_score(with_wave) - 1

    def test_no_wave_data_does_not_exceed_100(self):
        conditions = make_conditions(
            temperature_f=90, wind_mph=0, precipitation_mm=0, cloud_cover_pct=0, wave_height_m=None
        )
        assert compute_score(conditions) == 100


class TestMonotonicity:
    @pytest.mark.parametrize(
        "cold,warm",
        [(30, 40), (40, 55), (55, 65), (65, 72), (72, 80), (80, 100)],
    )
    def test_temperature_never_decreases_as_it_warms(self, cold, warm):
        assert score_temperature(warm) >= score_temperature(cold)

    @pytest.mark.parametrize(
        "calm,windy",
        [(0, 3), (3, 8), (8, 12), (12, 18), (18, 25), (25, 40)],
    )
    def test_wind_never_increases_as_it_picks_up(self, calm, windy):
        assert score_wind(windy) <= score_wind(calm)

    @pytest.mark.parametrize(
        "dry,wet",
        [(0, 0.1), (0.1, 0.5), (0.5, 2), (2, 5), (5, 20)],
    )
    def test_precipitation_never_increases_as_it_gets_wetter(self, dry, wet):
        assert score_precipitation(wet) <= score_precipitation(dry)

    @pytest.mark.parametrize(
        "clear,cloudy",
        [(0, 20), (20, 50), (50, 70), (70, 90), (90, 100)],
    )
    def test_cloud_cover_never_increases_as_it_clouds_over(self, clear, cloudy):
        assert score_cloud_cover(cloudy) <= score_cloud_cover(clear)

    @pytest.mark.parametrize(
        "calm,rough",
        [(0, 0.4), (0.4, 0.8), (0.8, 1.5), (1.5, 3)],
    )
    def test_wave_height_never_increases_as_surf_grows(self, calm, rough):
        assert score_wave_height(rough) <= score_wave_height(calm)

    def test_score_monotonic_in_temperature_end_to_end(self):
        scores = [compute_score(make_conditions(temperature_f=t)) for t in [30, 45, 55, 65, 72, 80]]
        assert scores == sorted(scores)

    def test_score_monotonic_in_wind_end_to_end(self):
        scores = [compute_score(make_conditions(wind_mph=w)) for w in [0, 5, 10, 15, 20, 30, 50]]
        assert scores == sorted(scores, reverse=True)
