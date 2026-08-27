"""The tiered-radius search accumulator.

Per the spec: query in increasing radius bands, accumulate results as bands
complete, stop as soon as a target beach count is reached, and never search
past a hard ceiling. This module contains only that algorithm -- it knows
nothing about Overpass, HTTP, or caching, which is what makes it testable
with a fake client and no network.
"""
from __future__ import annotations

from typing import Protocol

from .config import DEFAULT_RADIUS_BANDS_KM, DEFAULT_TARGET_COUNT, MAX_RADIUS_KM
from .models import BeachElement, SearchOutcome


class BeachSearchClient(Protocol):
    """Anything that can answer "beaches within radius_km of (lat, lon)".

    The real implementation (overpass.py) talks to the Overpass API and may
    itself be wrapped in a caching layer. Tests substitute a fake that
    returns canned lists per radius, with no network involved.
    """

    async def search(self, lat: float, lon: float, radius_km: float) -> list[BeachElement]:
        ...


async def tiered_search(
    client: BeachSearchClient,
    lat: float,
    lon: float,
    target_count: int = DEFAULT_TARGET_COUNT,
    bands_km: list[float] | None = None,
    ceiling_km: float = MAX_RADIUS_KM,
) -> SearchOutcome:
    """Expand the search radius band by band until enough beaches are found
    or the ceiling is hit, accumulating (deduped) results along the way.

    Each band re-queries the *full* circle up to that radius (Overpass has
    no clean "annulus" query), so results are deduped by osm_id as they
    accumulate rather than concatenated.
    """
    bands = list(bands_km if bands_km is not None else DEFAULT_RADIUS_BANDS_KM)

    accumulated: dict[str, BeachElement] = {}
    bands_used: list[float] = []
    ceiling_reached = False
    target_reached = False

    for band in bands:
        # Clamp any band beyond the ceiling down to the ceiling itself, and
        # make sure the ceiling is always tried at least once even if it
        # falls between two configured bands.
        effective_band = min(band, ceiling_km)

        results = await client.search(lat, lon, effective_band)
        for element in results:
            accumulated[element.osm_id] = element

        bands_used.append(effective_band)

        if len(accumulated) >= target_count:
            target_reached = True
            break

        if effective_band >= ceiling_km:
            ceiling_reached = True
            break
    else:
        # Loop finished without hitting target or an explicit ceiling band.
        # If the last band tried was still short of the ceiling, do one
        # final search at the ceiling itself before giving up.
        if bands_used and bands_used[-1] < ceiling_km:
            results = await client.search(lat, lon, ceiling_km)
            for element in results:
                accumulated[element.osm_id] = element
            bands_used.append(ceiling_km)
        ceiling_reached = True

    return SearchOutcome(
        beaches=list(accumulated.values()),
        bands_used_km=bands_used,
        ceiling_reached=ceiling_reached,
        target_reached=target_reached,
    )
