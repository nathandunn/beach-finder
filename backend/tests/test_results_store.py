"""v0.6 results store: serve a place's answer until it is 30 minutes old,
expel stale entries on the fetch-and-check path, persist across restarts,
and let a late water-type answer patch the stored copy."""
from __future__ import annotations

import json
import os

import pytest

from app.results_store import ResultsStore, place_key


class FakeClock:
    def __init__(self, t: float = 1_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def payload(*ids: str) -> dict:
    return {"beaches": [{"id": i, "water_type": "unknown"} for i in ids], "count": len(ids)}


class TestPlaceKey:
    def test_rounds_to_two_decimals(self):
        assert place_key(44.6368, -124.0535) == "44.64,-124.05"

    def test_nearby_taps_share_a_key(self):
        assert place_key(44.6368, -124.0535) == place_key(44.641, -124.049)


class TestFetchAndCheck:
    def test_fresh_entry_is_served(self):
        clock = FakeClock()
        store = ResultsStore(ttl_seconds=1800, path=None, clock=clock)
        store.put("k", payload("a"))
        clock.t += 1799
        entry = store.get("k")
        assert entry is not None and entry.payload["count"] == 1
        assert store.age_seconds(entry) == 1799

    def test_stale_entry_is_expelled_on_lookup(self):
        clock = FakeClock()
        store = ResultsStore(ttl_seconds=1800, path=None, clock=clock)
        store.put("k", payload("a"))
        clock.t += 1800
        assert store.get("k") is None
        assert len(store) == 0

    def test_lookup_expels_every_stale_entry_not_just_the_asked_one(self):
        clock = FakeClock()
        store = ResultsStore(ttl_seconds=1800, path=None, clock=clock)
        store.put("old1", payload("a"))
        store.put("old2", payload("b"))
        clock.t += 1000
        store.put("newer", payload("c"))
        clock.t += 900  # old1/old2 are 1900 s old, newer is 900 s old
        assert store.get("nothing") is None
        assert len(store) == 1 and store.get("newer") is not None

    def test_patch_water_types_keeps_fetched_at(self):
        clock = FakeClock()
        store = ResultsStore(ttl_seconds=1800, path=None, clock=clock)
        entry = store.put("k", payload("a", "b"), complete=False)
        clock.t += 60
        assert store.patch_water_types("k", {"a": "ocean", "b": "unknown"})
        entry = store.get("k")
        assert entry.complete
        assert entry.fetched_at == 1_000_000.0
        assert [b["water_type"] for b in entry.payload["beaches"]] == ["ocean", "unknown"]

    def test_patch_missing_key_is_a_noop(self):
        store = ResultsStore(ttl_seconds=1800, path=None)
        assert not store.patch_water_types("nope", {"a": "ocean"})


class TestPersistence:
    def test_round_trip_through_file(self, tmp_path):
        path = str(tmp_path / "results.json")
        clock = FakeClock()
        store = ResultsStore(ttl_seconds=1800, path=path, clock=clock)
        store.put("k", payload("a"), complete=False)
        assert os.path.exists(path)

        again = ResultsStore(ttl_seconds=1800, path=path, clock=clock)
        assert again.load() == 1
        entry = again.get("k")
        assert entry is not None and not entry.complete and entry.payload["count"] == 1

    def test_expulsion_is_written_back(self, tmp_path):
        path = str(tmp_path / "results.json")
        clock = FakeClock()
        store = ResultsStore(ttl_seconds=1800, path=path, clock=clock)
        store.put("k", payload("a"))
        clock.t += 5000
        store.get("k")
        with open(path) as fh:
            assert json.load(fh)["entries"] == {}

    def test_missing_or_corrupt_file_is_empty(self, tmp_path):
        path = str(tmp_path / "results.json")
        assert ResultsStore(path=path).load() == 0
        with open(path, "w") as fh:
            fh.write("{not json")
        assert ResultsStore(path=path).load() == 0

    @pytest.mark.asyncio
    async def test_lock_is_per_key(self):
        store = ResultsStore(path=None)
        a = await store.lock("a")
        b = await store.lock("b")
        assert a is not b and a is await store.lock("a")
