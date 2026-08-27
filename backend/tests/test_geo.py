"""Drive-time math (SPEC v0.3): the Oregon Beach App's heuristic ported
unchanged -- minutes = distance_miles / 45 * 60, truncated (not rounded)."""
from app.geo import estimate_drive_time_minutes


class TestEstimateDriveTimeMinutes:
    def test_zero_distance_is_zero_minutes(self):
        assert estimate_drive_time_minutes(0.0) == 0

    def test_matches_oregon_formula_for_a_known_distance(self):
        # 45 miles at an assumed 45mph should be ~60 minutes.
        distance_km = 45 / 0.621371  # 45 miles, expressed in km
        assert estimate_drive_time_minutes(distance_km) == 60

    def test_truncates_rather_than_rounds(self):
        # 46 miles / 45mph * 60 = 61.33... minutes -- Oregon's int() truncates
        # to 61, it does not round to the nearest minute.
        distance_km = 46 / 0.621371
        assert estimate_drive_time_minutes(distance_km) == 61

    def test_500_mile_ceiling_is_about_11_hours(self):
        minutes = estimate_drive_time_minutes(804.7)
        assert 660 <= minutes <= 670

    def test_monotonic_in_distance(self):
        minutes = [estimate_drive_time_minutes(km) for km in [1, 10, 50, 100, 400, 804.7]]
        assert minutes == sorted(minutes)

    def test_never_negative(self):
        assert estimate_drive_time_minutes(0.4) >= 0
