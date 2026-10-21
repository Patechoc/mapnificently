"""Answer reach and isochrone queries for a city. Shared by the API and tests."""

import time as clock
from datetime import datetime
from typing import Any

from mapnificently.isochrone import combine, reach_polygon, to_feature_collection
from mapnificently.models import (
    CityConfig,
    IsochroneRequest,
    OriginReach,
    ReachRequest,
    ReachResponse,
    TripRequest,
)
from mapnificently.routing import access_stops, earliest_arrivals
from mapnificently.timetable import WALK_DETOUR, CityDataStore


def local_departure(city: CityConfig, departure: datetime | None) -> datetime:
    """The departure in the city's timezone; naive times are read as local, ``None`` is now."""
    if departure is None:
        return datetime.now(city.tz).replace(microsecond=0)
    if departure.tzinfo is None:
        return departure.replace(tzinfo=city.tz)
    return departure.astimezone(city.tz)


def _origin_reaches(
    store: CityDataStore, city: CityConfig, request: TripRequest, max_minutes: int
) -> tuple[datetime, list[OriginReach]]:
    departure = local_departure(city, request.departure)
    network = store.network(city.id)
    timetable = store.timetable(city.id, departure.date())
    t_start = departure.hour * 3600 + departure.minute * 60 + departure.second
    t_end = t_start + max_minutes * 60

    reaches = []
    for origin in request.origins:
        sources = access_stops(
            network, origin.lat, origin.lon, t_start, request.walk_speed_mps, request.max_walk_m
        )
        arrivals = earliest_arrivals(
            network, timetable, sources, t_start, t_end, request.walk_speed_mps
        )
        ordered = sorted(arrivals.items(), key=lambda item: item[1])
        reaches.append(
            OriginReach(
                origin=origin,
                stop_idx=[stop for stop, _ in ordered],
                arrival_s=[t - t_start for _, t in ordered],
            )
        )
    return departure, reaches


def reach(store: CityDataStore, city: CityConfig, request: ReachRequest) -> ReachResponse:
    """Earliest arrival at every stop reachable within ``request.max_minutes``."""
    started = clock.perf_counter()
    departure, reaches = _origin_reaches(store, city, request, request.max_minutes)
    return ReachResponse(
        city=city.id,
        departure=departure,
        service_date=departure.date(),
        max_minutes=request.max_minutes,
        walk_speed_mps=request.walk_speed_mps,
        max_walk_m=request.max_walk_m,
        walk_detour=WALK_DETOUR,
        origins=reaches,
        computation_ms=round((clock.perf_counter() - started) * 1000, 1),
    )


def isochrone(store: CityDataStore, city: CityConfig, request: IsochroneRequest) -> dict[str, Any]:
    """GeoJSON FeatureCollection of the area reachable within ``request.minutes``."""
    started = clock.perf_counter()
    departure, reaches = _origin_reaches(store, city, request, request.minutes)
    network = store.network(city.id)
    areas = [
        reach_polygon(
            network.projection,
            r.origin,
            network.lat[r.stop_idx].tolist(),
            network.lon[r.stop_idx].tolist(),
            r.arrival_s,
            request.minutes,
            request.walk_speed_mps,
            request.max_walk_m,
        )
        for r in reaches
    ]
    area = combine(areas, request.mode)
    properties = {
        "city": city.id,
        "departure": departure.isoformat(),
        "minutes": request.minutes,
        "mode": request.mode,
        "origins": [o.model_dump() for o in request.origins],
        "walk_speed_mps": request.walk_speed_mps,
        "max_walk_m": request.max_walk_m,
        "area_km2": round(area.area / 1e6, 3),
        "computation_ms": round((clock.perf_counter() - started) * 1000, 1),
    }
    return to_feature_collection(network.projection, area, properties)
