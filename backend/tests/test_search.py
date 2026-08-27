"""Tiered-search accumulator tests against a FAKE Overpass client -- no
live network. Covers: stop-at-target, ceiling respect, and accumulation
(with dedup) across bands."""
from app.models import BeachElement
from app.search import tiered_search


def beach(n: int, lat: float = 44.6, lon: float = -124.0) -> BeachElement:
    return BeachElement(osm_id=f"way/{n}", lat=lat, lon=lon, name=f"Beach {n}")


class FakeOverpassClient:
    """Returns a fixed list of beaches per radius, from a table the test
    supplies. Records every call for assertions on which bands were tried."""

    def __init__(self, by_radius: dict[float, list[BeachElement]]):
        self._by_radius = by_radius
        self.calls: list[float] = []

    async def search(self, lat: float, lon: float, radius_km: float) -> list[BeachElement]:
        self.calls.append(radius_km)
        return self._by_radius.get(radius_km, [])


class TestAccumulation:
    async def test_accumulates_across_bands(self):
        client = FakeOverpassClient(
            {
                10: [beach(1), beach(2)],
                20: [beach(3)],
                40: [beach(4), beach(5)],
            }
        )
        outcome = await tiered_search(
            client, 44.6, -124.0, target_count=100, bands_km=[10, 20, 40], ceiling_km=40
        )
        assert {b.osm_id for b in outcome.beaches} == {"way/1", "way/2", "way/3", "way/4", "way/5"}
        assert outcome.bands_used_km == [10, 20, 40]

    async def test_dedupes_repeated_ids_across_bands(self):
        # Same beach shows up again in a wider band's full-circle re-query.
        client = FakeOverpassClient(
            {
                10: [beach(1), beach(2)],
                20: [beach(1), beach(2), beach(3)],
            }
        )
        outcome = await tiered_search(
            client, 44.6, -124.0, target_count=100, bands_km=[10, 20], ceiling_km=20
        )
        assert len(outcome.beaches) == 3
        assert {b.osm_id for b in outcome.beaches} == {"way/1", "way/2", "way/3"}


class TestStopAtTarget:
    async def test_stops_as_soon_as_target_reached(self):
        client = FakeOverpassClient(
            {
                10: [beach(i) for i in range(5)],
                20: [beach(i) for i in range(10)],
                40: [beach(i) for i in range(50)],  # should never be queried
            }
        )
        outcome = await tiered_search(
            client, 44.6, -124.0, target_count=8, bands_km=[10, 20, 40], ceiling_km=40
        )
        assert 40 not in client.calls
        assert client.calls == [10, 20]
        assert len(outcome.beaches) == 10  # band of 20 overshoots 8, that's fine
        assert outcome.target_reached is True

    async def test_exact_target_on_first_band_stops_immediately(self):
        client = FakeOverpassClient({10: [beach(i) for i in range(25)]})
        outcome = await tiered_search(
            client, 44.6, -124.0, target_count=25, bands_km=[10, 20, 40], ceiling_km=40
        )
        assert client.calls == [10]
        assert len(outcome.beaches) == 25


class TestCeiling:
    async def test_stops_at_ceiling_even_if_target_never_reached(self):
        client = FakeOverpassClient(
            {
                10: [beach(1)],
                20: [beach(2)],
                40: [beach(3)],
            }
        )
        outcome = await tiered_search(
            client, 44.6, -124.0, target_count=1000, bands_km=[10, 20, 40], ceiling_km=40
        )
        assert client.calls == [10, 20, 40]
        assert outcome.ceiling_reached is True
        assert outcome.target_reached is False
        assert len(outcome.beaches) == 3

    async def test_bands_beyond_ceiling_are_clamped_and_not_exceeded(self):
        client = FakeOverpassClient({10: [beach(1)], 40: [beach(2)]})
        # 40 is the ceiling; a configured 1000km band should be clamped down
        # to 40, not queried at its full value.
        outcome = await tiered_search(
            client, 44.6, -124.0, target_count=1000, bands_km=[10, 1000], ceiling_km=40
        )
        assert client.calls == [10, 40]
        assert max(client.calls) == 40

    async def test_never_queries_past_ceiling_when_bands_list_is_short(self):
        # If the configured bands run out before the ceiling or the target,
        # the accumulator does one final query at the ceiling itself.
        client = FakeOverpassClient({10: [beach(1)], 500: [beach(2)]})
        outcome = await tiered_search(
            client, 44.6, -124.0, target_count=1000, bands_km=[10], ceiling_km=500
        )
        assert client.calls == [10, 500]
        assert outcome.ceiling_reached is True
        assert len(outcome.beaches) == 2


class TestEmptyResults:
    async def test_no_beaches_found_anywhere(self):
        client = FakeOverpassClient({})
        outcome = await tiered_search(
            client, 40.0, -100.0, target_count=25, bands_km=[10, 20, 40], ceiling_km=40
        )
        assert outcome.beaches == []
        assert outcome.ceiling_reached is True
        assert outcome.target_reached is False
