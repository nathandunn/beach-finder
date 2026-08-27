"""Compass-point conversion (SPEC v0.3 item 4): Open-Meteo gives wind
direction in degrees; the card shows it as an 8-point compass abbreviation,
the way the Oregon Beach App renders wind direction."""
import pytest

from app.compass import degrees_to_compass


class TestDegreesToCompass:
    @pytest.mark.parametrize(
        "degrees,expected",
        [
            (0, "N"),
            (45, "NE"),
            (90, "E"),
            (135, "SE"),
            (180, "S"),
            (225, "SW"),
            (270, "W"),
            (315, "NW"),
            (360, "N"),
        ],
    )
    def test_cardinal_and_ordinal_points(self, degrees, expected):
        assert degrees_to_compass(degrees) == expected

    def test_boundary_just_below_northeast_is_still_north(self):
        assert degrees_to_compass(22.4) == "N"

    def test_boundary_at_northeast_start(self):
        assert degrees_to_compass(22.5) == "NE"

    def test_wraps_around_negative_degrees(self):
        # -10 degrees is equivalent to 350, which is still "N".
        assert degrees_to_compass(-10) == "N"

    def test_wraps_around_above_360(self):
        assert degrees_to_compass(405) == degrees_to_compass(45)

    def test_all_eight_points_are_reachable(self):
        seen = {degrees_to_compass(d) for d in range(0, 360, 5)}
        assert seen == {"N", "NE", "E", "SE", "S", "SW", "W", "NW"}
