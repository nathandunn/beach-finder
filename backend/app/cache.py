"""A tiny in-memory TTL cache plus a per-key single-flight lock manager.

Per the spec, caching is not optional -- a single 500-mile Overpass search
without caching would be unusably slow and would hammer a rate-limited free
API. This module implements the two caches the spec calls for:

- tile cache: Overpass results keyed by (geographic tile, radius band),
  long TTL, because coastlines don't move.
- weather cache: per-beach conditions, short TTL, because weather does
  change.

Both are the same generic TTLCache underneath; only the TTL constant differs.
A monotonic clock is injectable so tests can control expiry without sleeping.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Callable, Generic, Hashable, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")

Clock = Callable[[], float]


@dataclass
class _Entry(Generic[V]):
    value: V
    expires_at: float


class TTLCache(Generic[K, V]):
    """A minimal dict-backed cache with per-entry expiry.

    Not thread-safe across OS threads, but fine for a single asyncio event
    loop (FastAPI's default deployment model here). Expired entries are
    lazily evicted on read; there's no background sweep, which is fine at
    this app's scale.
    """

    def __init__(self, ttl_seconds: float, clock: Clock = time.monotonic):
        self._ttl = ttl_seconds
        self._clock = clock
        self._store: dict[K, _Entry[V]] = {}

    def get(self, key: K) -> V | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        if self._clock() >= entry.expires_at:
            del self._store[key]
            return None
        return entry.value

    def set(self, key: K, value: V) -> None:
        self._store[key] = _Entry(value=value, expires_at=self._clock() + self._ttl)

    def __contains__(self, key: K) -> bool:
        return self.get(key) is not None

    def __len__(self) -> int:
        # Note: does not evict expired entries, just reports raw size.
        return len(self._store)


class KeyedLock:
    """Provides one asyncio.Lock per key, for single-flight behavior.

    Used so that concurrent requests hitting the same uncached tile (or the
    same uncached beach's weather) don't each fire off their own Overpass /
    Open-Meteo request -- only one does the fetch, the rest await it and
    then read the now-populated cache.
    """

    def __init__(self) -> None:
        self._locks: dict[Hashable, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    async def acquire(self, key: Hashable) -> asyncio.Lock:
        async with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
        return lock
