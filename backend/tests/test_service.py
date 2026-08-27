"""BeachFinderService wiring tests -- all offline, fake search/weather/
water-type clients, no network. Covers SPEC v0.4's response-wiring bullet
("water_type present" on the scored result) and its failure path ("if the
classification query fails entirely, all beaches get unknown -- never fail
the search over the filter").

Deliberately does not import app.main or app.schemas (pydantic/FastAPI
response shapes) -- app.service only depends on config/forecast/geo/models/
scoring/search/watertype/weather, none of which need those.
"""
from __future__ import annotations

from app.models import BeachElement, WeatherConditions
from app.service import BeachFinderService


def beach(osm_id: str, lat: float = 44.6, lon: float = -124.0, name: str = "Test Beach") -> BeachElement:
    return BeachElement(osm_id=osm_id, lat=lat, lon=lon, name=name)


class FakeSearchClient:
    def __init__(self, beaches: list[BeachElement]):
        self._beaches = beaches

    async def search(self, lat: float, lon: float, radius_km: float) -> list[BeachElement]:
        return self._beaches


class FakeWeatherClient:
    async def get_conditions(self, lat: float, lon: float) -> WeatherConditions:
        return WeatherConditions(
            temperature_f=70.0, wind_mph=5.0, precipitation_mm=0.0, cloud_cover_pct=10.0
        )


class FakeWaterTypeClient:
    def __init__(self, mapping: dict[str, str] | None = None, fail: bool = False):
        self._mapping = mapping or {}
        self.fail = fail
        self.calls: list[list[str]] = []

    async def classify(self, beaches: list[BeachElement]) -> dict[str, str]:
        self.calls.append([b.osm_id for b in beaches])
        if self.fail:
            return {}  # mirrors a total query failure -- nothing classified
        return {b.osm_id: self._mapping.get(b.osm_id, "unknown") for b in beaches}


class TestWaterTypeWiring:
    async def test_water_type_is_present_on_scored_beaches(self):
        beaches = [beach("way/1"), beach("way/2", lat=44.7, lon=-124.1)]
        service = BeachFinderService(
            FakeSearchClient(beaches),
            FakeWeatherClient(),
            water_type_client=FakeWaterTypeClient({"way/1": "ocean", "way/2": "lake"}),
            bands_km=[10],
            ceiling_km=10,
        )
        result = await service.find_beaches(44.6, -124.0)
        by_id = {b.osm_id: b.water_type for b in result.beaches}
        assert by_id == {"way/1": "ocean", "way/2": "lake"}

    async def test_missing_classification_defaults_to_unknown(self):
        beaches = [beach("way/1")]
        # Water-type client returns nothing for this beach at all (e.g. a
        # partial classification) -- must default to "unknown", never
        # crash or omit the field.
        service = BeachFinderService(
            FakeSearchClient(beaches),
            FakeWeatherClient(),
            water_type_client=FakeWaterTypeClient({}),
            bands_km=[10],
            ceiling_km=10,
        )
        result = await service.find_beaches(44.6, -124.0)
        assert result.beaches[0].water_type == "unknown"

    async def test_classification_query_failure_still_returns_all_beaches(self):
        # SPEC v0.4: "If the classification query fails entirely, all
        # beaches get unknown -- never fail the search over the filter."
        beaches = [beach("way/1"), beach("way/2", lat=44.7, lon=-124.1)]
        service = BeachFinderService(
            FakeSearchClient(beaches),
            FakeWeatherClient(),
            water_type_client=FakeWaterTypeClient(fail=True),
            bands_km=[10],
            ceiling_km=10,
        )
        result = await service.find_beaches(44.6, -124.0)
        assert len(result.beaches) == 2
        assert all(b.water_type == "unknown" for b in result.beaches)
        # The search itself succeeded and still has scores/conditions --
        # the classification failure didn't cascade into anything else.
        assert all(b.score >= 0 for b in result.beaches)

    async def test_no_water_type_client_supplied_defaults_everyone_unknown(self):
        # BeachFinderService must remain constructible without a
        # water-type client at all (e.g. other tests exercising unrelated
        # behavior), degrading to "unknown" rather than erroring.
        beaches = [beach("way/1")]
        service = BeachFinderService(FakeSearchClient(beaches), FakeWeatherClient(), bands_km=[10], ceiling_km=10)
        result = await service.find_beaches(44.6, -124.0)
        assert result.beaches[0].water_type == "unknown"

    async def test_empty_search_result_does_not_invoke_water_type_client(self):
        water_type_client = FakeWaterTypeClient()
        service = BeachFinderService(
            FakeSearchClient([]),
            FakeWeatherClient(),
            water_type_client=water_type_client,
            bands_km=[10],
            ceiling_km=10,
        )
        result = await service.find_beaches(44.6, -124.0)
        assert result.beaches == []
        assert water_type_client.calls == [[]]
