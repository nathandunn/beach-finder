"""Response parsing against recorded fixtures -- no live network.

Fixtures were hand-written from real Overpass / Open-Meteo response shapes
(captured manually against the live APIs during development) so they match
the actual API contract, not just what our code expects.
"""
import json
from pathlib import Path

from app.overpass import parse_overpass_response
from app.weather import parse_marine_response, parse_open_meteo_response

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

    def test_missing_current_block_falls_back_to_defaults(self):
        fields = parse_open_meteo_response({})
        assert fields["temperature_f"] == 65.0
        assert fields["wind_mph"] == 5.0
        assert fields["precipitation_mm"] == 0.0
        assert fields["cloud_cover_pct"] == 50.0


class TestMarineParsing:
    def test_parses_wave_height(self):
        payload = load_fixture("open_meteo_marine_sample.json")
        assert parse_marine_response(payload) == 1.08

    def test_missing_wave_height_returns_none(self):
        assert parse_marine_response({"current": {}}) is None

    def test_missing_current_block_returns_none(self):
        assert parse_marine_response({}) is None
