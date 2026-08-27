"""TTL expiry behavior for the generic cache used by both the tile cache and
the weather cache, plus the single-flight keyed lock."""
import asyncio

import pytest

from app.cache import KeyedLock, TTLCache


class FakeClock:
    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestTTLCache:
    def test_get_missing_key_returns_none(self):
        cache = TTLCache(ttl_seconds=60)
        assert cache.get("nope") is None

    def test_set_then_get_within_ttl(self):
        clock = FakeClock()
        cache = TTLCache(ttl_seconds=60, clock=clock)
        cache.set("k", "v")
        clock.advance(59)
        assert cache.get("k") == "v"

    def test_expires_after_ttl(self):
        clock = FakeClock()
        cache = TTLCache(ttl_seconds=60, clock=clock)
        cache.set("k", "v")
        clock.advance(60)  # expires_at == now -> expired (>=)
        assert cache.get("k") is None

    def test_expired_entry_is_evicted_from_underlying_store(self):
        clock = FakeClock()
        cache = TTLCache(ttl_seconds=10, clock=clock)
        cache.set("k", "v")
        clock.advance(11)
        assert cache.get("k") is None
        assert len(cache) == 0

    def test_contains_reflects_expiry(self):
        clock = FakeClock()
        cache = TTLCache(ttl_seconds=10, clock=clock)
        cache.set("k", "v")
        assert "k" in cache
        clock.advance(11)
        assert "k" not in cache

    def test_weather_ttl_is_shorter_than_tile_ttl_in_practice(self):
        # Sanity: a weather-style cache (30 min) expires well before a
        # tile-style cache (30 days) given the same elapsed time.
        clock = FakeClock()
        weather_cache = TTLCache(ttl_seconds=30 * 60, clock=clock)
        tile_cache = TTLCache(ttl_seconds=30 * 24 * 60 * 60, clock=clock)
        weather_cache.set("k", "weather")
        tile_cache.set("k", "tile")

        clock.advance(31 * 60)  # 31 minutes later

        assert weather_cache.get("k") is None
        assert tile_cache.get("k") == "tile"

    def test_independent_keys_do_not_interfere(self):
        clock = FakeClock()
        cache = TTLCache(ttl_seconds=10, clock=clock)
        cache.set("a", 1)
        clock.advance(5)
        cache.set("b", 2)
        clock.advance(6)  # a is 11s old (expired), b is 6s old (fresh)
        assert cache.get("a") is None
        assert cache.get("b") == 2


class TestKeyedLock:
    async def test_same_key_returns_same_lock(self):
        locks = KeyedLock()
        lock1 = await locks.acquire("tile-a")
        lock2 = await locks.acquire("tile-a")
        assert lock1 is lock2

    async def test_different_keys_get_different_locks(self):
        locks = KeyedLock()
        lock1 = await locks.acquire("tile-a")
        lock2 = await locks.acquire("tile-b")
        assert lock1 is not lock2

    async def test_single_flight_serializes_concurrent_fetches(self):
        locks = KeyedLock()
        call_count = 0
        cache = TTLCache(ttl_seconds=60)

        async def fetch_with_single_flight(key: str) -> str:
            nonlocal call_count
            cached = cache.get(key)
            if cached is not None:
                return cached
            lock = await locks.acquire(key)
            async with lock:
                cached = cache.get(key)
                if cached is not None:
                    return cached
                call_count += 1
                await asyncio.sleep(0.01)  # simulate a slow upstream fetch
                value = f"fetched-{call_count}"
                cache.set(key, value)
                return value

        results = await asyncio.gather(*(fetch_with_single_flight("same-tile") for _ in range(10)))

        assert call_count == 1  # only one real fetch happened
        assert all(r == "fetched-1" for r in results)
