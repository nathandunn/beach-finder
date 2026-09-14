"""Per-place results store (v0.6).

The whole `/api/beaches` answer for a place is kept for RESULTS_TTL_SECONDS
(30 minutes) and served straight back until it is that old -- the same
place is not re-fetched every time somebody taps it. The store is only
touched from the fetch-and-check path: a lookup first expels every entry
that has gone stale, then answers from what is left or lets the caller
fetch and `put` a fresh one. There is no background sweeper and nothing is
written outside that path.

Entries survive a container restart via a small JSON file (RESULTS_PATH);
a missing or unreadable file just means an empty store.

Place key: coordinates rounded to RESULTS_COORD_PRECISION decimals (2 ->
about 1.1 km), so a place searched by name lands on the same key every
time and two taps a few hundred metres apart share one answer.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import RESULTS_COORD_PRECISION, RESULTS_PATH, RESULTS_TTL_SECONDS

WallClock = Callable[[], float]


def place_key(lat: float, lon: float, precision: int = RESULTS_COORD_PRECISION) -> str:
    return f"{round(lat, precision):.{precision}f},{round(lon, precision):.{precision}f}"


@dataclass
class StoredResult:
    fetched_at: float  # wall-clock seconds (time.time())
    payload: dict[str, Any]  # the JSON-able BeachesResponse body
    complete: bool = True  # False while water types are still being resolved


@dataclass
class ResultsStore:
    ttl_seconds: float = RESULTS_TTL_SECONDS
    path: str | None = RESULTS_PATH
    clock: WallClock = time.time
    _entries: dict[str, StoredResult] = field(default_factory=dict)
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    _guard: asyncio.Lock = field(default_factory=asyncio.Lock)

    # --- persistence -------------------------------------------------------

    def load(self) -> int:
        """Read the on-disk file (if any). Returns how many entries loaded."""
        if not self.path or not os.path.exists(self.path):
            return 0
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            return 0
        loaded = 0
        for key, item in (raw.get("entries") or {}).items():
            try:
                self._entries[key] = StoredResult(
                    fetched_at=float(item["fetched_at"]),
                    payload=item["payload"],
                    complete=bool(item.get("complete", True)),
                )
                loaded += 1
            except (KeyError, TypeError, ValueError):
                continue
        return loaded

    def _save(self) -> None:
        if not self.path:
            return
        data = {
            "saved_at": self.clock(),
            "entries": {
                k: {"fetched_at": e.fetched_at, "payload": e.payload, "complete": e.complete}
                for k, e in self._entries.items()
            },
        }
        tmp = self.path + ".tmp"
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, self.path)
        except OSError:
            # Persistence is a convenience; the in-memory store is the truth.
            pass

    # --- the fetch-and-check path -----------------------------------------

    def age_seconds(self, entry: StoredResult) -> float:
        return max(0.0, self.clock() - entry.fetched_at)

    def is_fresh(self, entry: StoredResult) -> bool:
        return self.age_seconds(entry) < self.ttl_seconds

    def expel_stale(self) -> int:
        """Drop every entry older than the TTL. Called on each lookup, so
        the store only ever shrinks as part of a fetch-and-check."""
        stale = [k for k, e in self._entries.items() if not self.is_fresh(e)]
        for k in stale:
            del self._entries[k]
        if stale:
            self._save()
        return len(stale)

    def get(self, key: str) -> StoredResult | None:
        self.expel_stale()
        return self._entries.get(key)

    def put(self, key: str, payload: dict[str, Any], complete: bool = True) -> StoredResult:
        entry = StoredResult(fetched_at=self.clock(), payload=payload, complete=complete)
        self._entries[key] = entry
        self._save()
        return entry

    def patch_water_types(self, key: str, water_types: dict[str, str]) -> bool:
        """Late water-type answers land on the stored entry (which keeps its
        original fetched_at -- the weather is no fresher than it was)."""
        entry = self._entries.get(key)
        if entry is None:
            return False
        for beach in entry.payload.get("beaches", []):
            wt = water_types.get(beach.get("id"))
            if wt and wt != "unknown":
                beach["water_type"] = wt
        entry.complete = True
        self._save()
        return True

    async def lock(self, key: str) -> asyncio.Lock:
        async with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
        return lock

    def __len__(self) -> int:
        return len(self._entries)
