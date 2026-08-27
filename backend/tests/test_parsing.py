"""Response parsing against recorded fixtures -- no live network.

Fixtures were hand-written from real Overpass / Open-Meteo response shapes
(captured manually against the live APIs during development) so they match
the actual API contract, not just what our code expects.
"""
import json
from pathlib import Path

from app.overpass import extract_city, parse_overpass_response
from app.weather import parse_hourly_block, parse_marine_response, parse_open_meteo_response

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


class TestOverpassParsing:
    def test_parses_nodes_ways_and_relations(self):
        payload = load_fixture("overpass_sample.json")
        beaches = parse_overpass_response(payload)

        ids = {b.osm_id for b in beaches}
        assert "way/405053738" in ids
        assert "node/9876543210" in ids
        assert "relation/112233" in ids

    def test_node_uses_lat_lon_directly(self):
        payload = load_fixture("overpass_sample.json")
        beaches = parse_overpass_response(payload)
        nye = next(b for b in beaches if b.osm_id == "node/9876543210")
        assert nye.lat == 44.6301
        assert nye.lon == -124.0532
        assert nye.name == "Nye Beach"

    def test_way_uses_center(self):
        payload = load_fixture("overpass_sample.json")
        beaches = parse_overpass_response(payload)
        agate = next(b for b in beaches if b.osm_id == "way/405053738")
        assert agate.lat == 44.6244105
        assert agate.lon == -124.057761
        assert agate.name == "Agate Beach"

    def test_unnamed_beach_has_none_name(self):
        payload = load_fixture("overpass_sample.json")
        beaches = parse_overpass_response(payload)
        unnamed = next(b for b in beaches if b.osm_id == "way/553624856")
        assert unnamed.name is None

    def test_city_from_addr_city_tag(self):
        payload = load_fixture("overpass_sample.json")
        beaches = parse_overpass_response(payload)
        agate = next(b for b in beaches if b.osm_id == "way/405053738")
        assert agate.city == "Newport"

    def test_city_falls_back_to_first_segment_of_is_in(self):
        payload = load_fixture("overpass_sample.json")
        beaches = parse_overpass_response(payload)
        unnamed = next(b for b in beaches if b.osm_id == "way/553624856")
        assert unnamed.city == "Lincoln County"

    def test_city_omitted_when_no_usable_tags(self):
        payload = load_fixture("overpass_sample.json")
        beaches = parse_overpass_response(payload)
        nye = next(b for b in beaches if b.osm_id == "node/9876543210")
        assert nye.city is None


class TestExtractCity:
    def test_prefers_addr_city_over_is_in(self):
        tags = {"addr:city": "Newport", "is_in": "Some Other Place, Oregon"}
        assert extract_city(tags) == "Newport"

    def test_is_in_city_before_addr_town(self):
        tags = {"is_in:city": "Yachats", "addr:town": "Somewhere Else"}
        assert extract_city(tags) == "Yachats"

    def test_addr_town_and_hamlet_supported(self):
        assert extract_city({"addr:town": "Depoe Bay"}) == "Depoe Bay"
        assert extract_city({"addr:hamlet": "Otter Rock"}) == "Otter Rock"

    def test_is_in_takes_first_comma_segment(self):
        tags = {"is_in": "  Newport , Lincoln County, Oregon, USA"}
        assert extract_city(tags) == "Newport"

    def test_no_tags_returns_none(self):
        assert extract_city({}) is None

    def test_blank_is_in_returns_none(self):
        assert extract_city({"is_in": ""}) is None

    def test_element_without_coordinates_is_skipped(self):
        payload = load_fixture("overpass_sample.json")
        beaches = parse_overpass_response(payload)
        ids = {b.osm_id for b in beaches}
        assert "way/998877" not in ids

    def test_empty_elements_list(self):
        assert parse_overpass_response({"elements": []}) == []

    def test_missing_elements_key(self):
        assert parse_overpass_response({}) == []


class TestOpenMeteoParsing:
    def test_parses_current_weather_fields(self):
        payload = load_fixture("open_meteo_sample.json")
        fields = parse_open_meteo_response(payload)

        assert fields["temperature_f"] == 58.3
        assert fields["wind_mph"] == 7.0
        assert fields["precipitation_mm"] == 0.2
        assert fields["cloud_cover_pct"] == 100

    def test_parses_wind_direction_and_humidity(self):
        payload = load_fixture("open_meteo_sample.json")
        fields = parse_open_meteo_response(payload)

        assert fields["wind_direction_deg"] == 292.0
        assert fields["humidity_pct"] == 84

    def test_missing_current_block_falls_back_to_defaults(self):
        fields = parse_open_meteo_response({})
        assert fields["temperature_f"] == 65.0
        assert fields["wind_mph"] == 5.0
        assert fields["wind_direction_deg"] == 0.0
        assert fields["humidity_pct"] == 70.0
        assert fields["precipitation_mm"] == 0.0
        assert fields["cloud_cover_pct"] == 50.0

    def test_parses_hourly_block_alongside_current(self):
        payload = load_fixture("open_meteo_sample.json")
        fields = parse_open_meteo_response(payload)

        hourly = fields["hourly"]
        assert len(hourly) == 6
        assert hourly[0].time == "2026-08-27T06:00"
        assert hourly[0].temperature_f == 58.3
        # Index 0 is "now" -- matches the current block's own reading for
        # this fixture (a real response's first hourly row and its
        # `current` block usually agree closely since both represent "now").
        assert hourly[-1].temperature_f == 68.2
        assert hourly[-1].cloud_cover_pct == 30

    def test_missing_hourly_block_returns_empty_list(self):
        fields = parse_open_meteo_response({"current": {"temperature_2m": 60.0}})
        assert fields["hourly"] == []


class TestHourlyBlockParsing:
    def test_parses_parallel_arrays_into_points(self):
        hourly = {
            "time": ["2026-08-27T06:00", "2026-08-27T07:00"],
            "temperature_2m": [58.3, 59.1],
            "wind_speed_10m": [7.0, 7.5],
            "precipitation": [0.2, 0.1],
            "cloud_cover": [100, 95],
        }
        points = parse_hourly_block(hourly)
        assert len(points) == 2
        assert points[0].time == "2026-08-27T06:00"
        assert points[0].temperature_f == 58.3
        assert points[1].wind_mph == 7.5
        assert points[1].cloud_cover_pct == 95

    def test_empty_block_returns_empty_list(self):
        assert parse_hourly_block({}) == []

    def test_zips_to_shortest_array_rather_than_raising(self):
        # A malformed/partial response shouldn't crash the whole beach.
        hourly = {
            "time": ["2026-08-27T06:00", "2026-08-27T07:00", "2026-08-27T08:00"],
            "temperature_2m": [58.3, 59.1],  # one short
            "wind_speed_10m": [7.0, 7.5, 8.0],
            "precipitation": [0.0, 0.0, 0.0],
            "cloud_cover": [10, 20, 30],
        }
        points = parse_hourly_block(hourly)
        assert len(points) == 2

    def test_null_values_fall_back_to_defaults(self):
        hourly = {
            "time": ["2026-08-27T06:00"],
            "temperature_2m": [None],
            "wind_speed_10m": [None],
            "precipitation": [None],
            "cloud_cover": [None],
        }
        points = parse_hourly_block(hourly)
        assert points[0].temperature_f == 65.0
        assert points[0].wind_mph == 5.0
        assert points[0].precipitation_mm == 0.0
        assert points[0].cloud_cover_pct == 50.0


class TestMarineParsing:
    def test_parses_wave_height(self):
        payload = load_fixture("open_meteo_marine_sample.json")
        assert parse_marine_response(payload) == 1.08

    def test_missing_wave_height_returns_none(self):
        assert parse_marine_response({"current": {}}) is None

    def test_missing_current_block_returns_none(self):
        assert parse_marine_response({}) is None
