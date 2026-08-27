"""Water-type classification tests (SPEC v0.4) -- all offline.

Covers: precedence (coastline beats stream; stream-only -> river; lake vs
river; nothing -> unknown), batched-query construction, per-beach
attribution against a realistic fixture, and the caching/single-flight
wrapper (TTL + batched-query-covers-only-missing-beaches + failure path).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.cache import KeyedLock, TTLCache
from app.models import BeachElement
from app.watertype import (
    CachingWaterTypeClient,
    WaterFeature,
    build_water_type_query,
    classify_beaches,
    classify_element,
    parse_water_features,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def beach(osm_id: str, lat: float, lon: float) -> BeachElement:
    return BeachElement(osm_id=osm_id, lat=lat, lon=lon, name=osm_id)


def feature(lat: float, lon: float, **tags) -> WaterFeature:
    return WaterFeature(lat=lat, lon=lon, tags=tags)


class TestClassifyElement:
    def test_coastline(self):
        assert classify_element({"natural": "coastline"}) == "ocean"

    def test_lake_reservoir_pond(self):
        assert classify_element({"water": "lake"}) == "lake"
        assert classify_element({"water": "reservoir"}) == "lake"
        assert classify_element({"water": "pond"}) == "lake"

    def test_river_waterway_or_water_river(self):
        assert classify_element({"waterway": "river"}) == "river"
        assert classify_element({"waterway": "riverbank"}) == "river"
        assert classify_element({"water": "river"}) == "river"

    def test_stream(self):
        assert classify_element({"waterway": "stream"}) == "stream"

    def test_coastline_takes_precedence_over_other_tags_on_same_element(self):
        # Real OSM data is messy -- an element could carry more than one of
        # these tags. Coastline still wins.
        assert classify_element({"natural": "coastline", "waterway": "stream"}) == "ocean"

    def test_unrelated_tags_return_none(self):
        assert classify_element({"highway": "residential"}) is None
        assert classify_element({}) is None


class TestClassifyBeaches:
    """Precedence at the per-beach level: coastline beats lake beats river;
    stream only counts when nothing stronger is present near that beach;
    no qualifying feature at all -> unknown."""

    def test_coastline_beats_stream(self):
        b = [beach("way/1", 44.6, -124.0)]
        features = [
            feature(44.6001, -124.0001, natural="coastline"),
            feature(44.6002, -124.0002, waterway="stream"),
        ]
        result = classify_beaches(b, features)
        assert result["way/1"] == "ocean"

    def test_stream_only_classifies_as_river(self):
        b = [beach("way/1", 44.6, -124.0)]
        features = [feature(44.6001, -124.0001, waterway="stream")]
        result = classify_beaches(b, features)
        assert result["way/1"] == "river"

    def test_lake_beats_river_and_stream(self):
        b = [beach("way/1", 44.6, -124.0)]
        features = [
            feature(44.6001, -124.0001, water="lake"),
            feature(44.6002, -124.0002, waterway="river"),
            feature(44.6003, -124.0003, waterway="stream"),
        ]
        result = classify_beaches(b, features)
        assert result["way/1"] == "lake"

    def test_river_beats_stream(self):
        b = [beach("way/1", 44.6, -124.0)]
        features = [
            feature(44.6001, -124.0001, waterway="river"),
            feature(44.6002, -124.0002, waterway="stream"),
        ]
        result = classify_beaches(b, features)
        assert result["way/1"] == "river"

    def test_water_equals_river_tag_counts_as_river(self):
        b = [beach("way/1", 44.6, -124.0)]
        features = [feature(44.6001, -124.0001, water="river")]
        result = classify_beaches(b, features)
        assert result["way/1"] == "river"

    def test_no_features_at_all_is_unknown(self):
        b = [beach("way/1", 44.6, -124.0)]
        result = classify_beaches(b, [])
        assert result["way/1"] == "unknown"

    def test_features_present_but_outside_radius_is_unknown(self):
        b = [beach("way/1", 44.6, -124.0)]
        # ~1.1 degrees of latitude away -- roughly 120km, well outside 400m.
        features = [feature(45.7, -124.0, natural="coastline")]
        result = classify_beaches(b, features, radius_km=0.4)
        assert result["way/1"] == "unknown"

    def test_every_input_beach_gets_a_result_even_with_no_features(self):
        beaches = [beach("way/1", 44.6, -124.0), beach("way/2", 45.0, -123.0)]
        result = classify_beaches(beaches, [])
        assert result == {"way/1": "unknown", "way/2": "unknown"}

    def test_independent_attribution_not_nearest_only(self):
        # Two beaches close together, both legitimately within 400m of the
        # SAME coastline way. Both must classify "ocean" -- a
        # nearest-neighbor partition would only credit whichever beach is
        # closer to the feature and miss the other.
        close_pair = [
            beach("way/near", 44.60000, -124.00000),
            beach("way/far", 44.60250, -124.00000),  # ~278m away from near, still both <400m from the feature below
        ]
        # Coastline point ~40m from "near" and ~240m from "far" -- both
        # within the 400m probe radius independently.
        features = [feature(44.60036, -124.00000, natural="coastline")]
        result = classify_beaches(close_pair, features, radius_km=0.4)
        assert result["way/near"] == "ocean"
        assert result["way/far"] == "ocean"


class TestBuildWaterTypeQuery:
    def test_empty_beach_list_returns_empty_string(self):
        assert build_water_type_query([]) == ""

    def test_one_beach_has_four_clauses(self):
        query = build_water_type_query([beach("way/1", 44.6, -124.0)])
        assert query.count("around:400,44.6,-124.0") == 4
        assert 'nwr["natural"="coastline"]' in query
        assert 'nwr["water"~"^(lake|reservoir|pond)$"]' in query
        assert 'nwr["waterway"~"^(river|riverbank|stream)$"]' in query
        assert 'nwr["water"="river"]' in query
        assert "out center;" in query

    def test_multiple_beaches_all_present_in_one_union(self):
        beaches = [beach(f"way/{i}", 44.0 + i * 0.1, -124.0) for i in range(5)]
        query = build_water_type_query(beaches)
        for b in beaches:
            assert f"{b.lat},{b.lon}" in query
        # One query, one union -- not five separate requests.
        assert query.count("[out:json]") == 1
        # 5 beaches x 4 patterns.
        assert query.count("around:400") == 20

    def test_custom_radius_is_honored(self):
        query = build_water_type_query([beach("way/1", 1.0, 2.0)], radius_m=250)
        assert "around:250,1.0,2.0" in query


class TestParseWaterFeaturesFixture:
    """Against a realistic recorded-shape fixture: a coastline way, named
    creeks (waterway=stream), a lake relation, and a tagged river way --
    modeled on overpass_sample.json's node/way/relation mix."""

    def test_parses_all_element_types(self):
        payload = load_fixture("overpass_water_sample.json")
        features = parse_water_features(payload)
        assert len(features) == 7

    def test_way_and_relation_use_center_node_uses_lat_lon(self):
        payload = load_fixture("overpass_water_sample.json")
        features = parse_water_features(payload)
        coastline = next(f for f in features if f.tags.get("natural") == "coastline")
        assert coastline.lat == 44.6246
        assert coastline.lon == -124.058
        lake = next(f for f in features if f.tags.get("water") == "lake")
        assert lake.lat == 44.9301
        node_stream = next(f for f in features if f.tags.get("name") == "Little Feeder")
        assert node_stream.lat == 45.5005
        assert node_stream.lon == -123.1005

    def test_end_to_end_classification_against_fixture(self):
        """Four beaches, each set up to land near a different combination
        of features in the fixture, exercising the full precedence chain
        through real parsed data rather than hand-built WaterFeatures."""
        payload = load_fixture("overpass_water_sample.json")
        features = parse_water_features(payload)

        beaches = [
            # Near the coastline AND a stream -- ocean wins.
            beach("way/ocean", 44.6244, -124.0577),
            # Near the lake AND a feeder stream -- lake wins.
            beach("way/lake", 44.9300, -122.9300),
            # Near a tagged river AND a stream node -- river wins.
            beach("way/river", 45.5000, -123.1000),
            # Near only an isolated stream -- counts as river.
            beach("way/creek-only", 46.0000, -121.0000),
            # Nowhere near anything in the fixture.
            beach("way/nowhere", 0.0, 0.0),
        ]
        result = classify_beaches(beaches, features)
        assert result["way/ocean"] == "ocean"
        assert result["way/lake"] == "lake"
        assert result["way/river"] == "river"
        assert result["way/creek-only"] == "river"
        assert result["way/nowhere"] == "unknown"


# --- Caching / single-flight wrapper ---------------------------------------


class FakeClock:
    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeWaterTypeHttpClient:
    """Records every batch it was asked to fetch, and returns whatever the
    test wired up via `responses` keyed on the sorted tuple of osm_ids
    requested (defaulting to no features -> every beach unknown)."""

    def __init__(self, responses: dict | None = None, fail: bool = False):
        self._responses = responses or {}
        self.fail = fail
        self.calls: list[list[str]] = []

    async def fetch_features(self, beaches: list[BeachElement]) -> list[WaterFeature]:
        ids = [b.osm_id for b in beaches]
        self.calls.append(ids)
        if self.fail:
            return []  # mirrors HttpWaterTypeClient's failure contract
        key = tuple(sorted(ids))
        return self._responses.get(key, [])


class TestCachingWaterTypeClient:
    async def test_classifies_and_caches_per_beach(self):
        b1 = beach("way/1", 44.6, -124.0)
        features = {("way/1",): [feature(44.6001, -124.0001, natural="coastline")]}
        inner = FakeWaterTypeHttpClient(features)
        cache = TTLCache(ttl_seconds=1000)
        client = CachingWaterTypeClient(inner, cache, KeyedLock())

        result = await client.classify([b1])
        assert result == {"way/1": "ocean"}
        assert len(inner.calls) == 1

    async def test_cached_beach_is_not_refetched(self):
        b1 = beach("way/1", 44.6, -124.0)
        clock = FakeClock()
        cache = TTLCache(ttl_seconds=1000, clock=clock)
        inner = FakeWaterTypeHttpClient({("way/1",): [feature(44.6001, -124.0001, water="lake")]})
        client = CachingWaterTypeClient(inner, cache, KeyedLock())

        first = await client.classify([b1])
        second = await client.classify([b1])
        assert first == second == {"way/1": "lake"}
        assert len(inner.calls) == 1  # second call served entirely from cache

    async def test_ttl_expiry_triggers_refetch(self):
        b1 = beach("way/1", 44.6, -124.0)
        clock = FakeClock()
        cache = TTLCache(ttl_seconds=100, clock=clock)
        inner = FakeWaterTypeHttpClient({("way/1",): [feature(44.6001, -124.0001, water="lake")]})
        client = CachingWaterTypeClient(inner, cache, KeyedLock())

        await client.classify([b1])
        clock.advance(101)
        await client.classify([b1])
        assert len(inner.calls) == 2

    async def test_long_geometry_ttl_used_by_wiring(self):
        # Sanity: this cache is meant to share the beach-geometry TTL
        # (config.WATER_TYPE_CACHE_TTL_SECONDS == TILE_CACHE_TTL_SECONDS),
        # not the short weather TTL -- confirm the values still agree so a
        # future edit to one doesn't silently drift from the other.
        from app.config import TILE_CACHE_TTL_SECONDS, WATER_TYPE_CACHE_TTL_SECONDS

        assert WATER_TYPE_CACHE_TTL_SECONDS == TILE_CACHE_TTL_SECONDS

    async def test_only_uncached_beaches_are_included_in_the_batch_fetch(self):
        b1 = beach("way/1", 44.6, -124.0)
        b2 = beach("way/2", 45.0, -123.0)
        cache = TTLCache(ttl_seconds=1000)
        cache.set((44.6, -124.0), "ocean")  # pre-populate b1 as already cached
        inner = FakeWaterTypeHttpClient({("way/2",): [feature(45.0001, -123.0001, waterway="stream")]})
        client = CachingWaterTypeClient(inner, cache, KeyedLock())

        result = await client.classify([b1, b2])
        assert result == {"way/1": "ocean", "way/2": "river"}
        # Only the uncached beach appeared in the one batched fetch --
        # ONE query per search, covering the beaches that still need it.
        assert inner.calls == [["way/2"]]

    async def test_all_beaches_cached_makes_no_fetch_at_all(self):
        b1 = beach("way/1", 44.6, -124.0)
        cache = TTLCache(ttl_seconds=1000)
        cache.set((44.6, -124.0), "lake")
        inner = FakeWaterTypeHttpClient()
        client = CachingWaterTypeClient(inner, cache, KeyedLock())

        result = await client.classify([b1])
        assert result == {"way/1": "lake"}
        assert inner.calls == []

    async def test_empty_beach_list_makes_no_fetch(self):
        cache = TTLCache(ttl_seconds=1000)
        inner = FakeWaterTypeHttpClient()
        client = CachingWaterTypeClient(inner, cache, KeyedLock())
        assert await client.classify([]) == {}
        assert inner.calls == []

    async def test_single_flight_serializes_concurrent_identical_batches(self):
        b1 = beach("way/1", 44.6, -124.0)
        b2 = beach("way/2", 45.0, -123.0)
        cache = TTLCache(ttl_seconds=1000)

        class SlowFakeClient(FakeWaterTypeHttpClient):
            async def fetch_features(self, beaches):
                await asyncio.sleep(0.01)
                return await super().fetch_features(beaches)

        inner = SlowFakeClient({("way/1", "way/2"): [feature(44.6001, -124.0001, natural="coastline")]})
        client = CachingWaterTypeClient(inner, cache, KeyedLock())

        results = await asyncio.gather(*(client.classify([b1, b2]) for _ in range(5)))
        assert all(r == {"way/1": "ocean", "way/2": "unknown"} for r in results)
        assert len(inner.calls) == 1  # single-flight: only one real fetch happened

    async def test_query_failure_classifies_every_beach_unknown(self):
        # SPEC v0.4: if the classification query fails entirely, all
        # beaches get "unknown" -- the search itself must still succeed.
        beaches = [beach("way/1", 44.6, -124.0), beach("way/2", 45.0, -123.0)]
        cache = TTLCache(ttl_seconds=1000)
        inner = FakeWaterTypeHttpClient(fail=True)
        client = CachingWaterTypeClient(inner, cache, KeyedLock())

        result = await client.classify(beaches)
        assert result == {"way/1": "unknown", "way/2": "unknown"}

    async def test_failed_result_is_still_cached_as_unknown(self):
        # A failed lookup shouldn't be retried on every single request --
        # "unknown" gets cached like any other classification, honoring
        # the same TTL.
        b1 = beach("way/1", 44.6, -124.0)
        cache = TTLCache(ttl_seconds=1000)
        inner = FakeWaterTypeHttpClient(fail=True)
        client = CachingWaterTypeClient(inner, cache, KeyedLock())

        await client.classify([b1])
        await client.classify([b1])
        assert len(inner.calls) == 1
