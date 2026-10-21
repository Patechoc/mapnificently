"""Answer reach, isochrone and route queries for a city. Shared by the API and tests."""

import time as clock
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from mapnificently.isochrone import combine, reach_polygon, to_feature_collection
from mapnificently.models import (
    CityConfig,
    IsochroneRequest,
    Journey,
    JourneyLeg,
    LatLon,
    OriginReach,
    ReachRequest,
    ReachResponse,
    RouteRequest,
    RouteResponse,
    TripRequest,
)
from mapnificently.routing import (
    Itinerary,
    access_stops,
    earliest_arrivals,
    itinerary_to,
    route_mode,
    scan,
)
from mapnificently.timetable import WALK_DETOUR, CityDataStore, StopNetwork, TripInfo


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
            network,
            timetable,
            sources,
            t_start,
            t_end,
            request.walk_speed_mps,
            request.min_change_min * 60,
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
        min_change_min=request.min_change_min,
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
        "min_change_min": request.min_change_min,
        "area_km2": round(area.area / 1e6, 3),
        "computation_ms": round((clock.perf_counter() - started) * 1000, 1),
    }
    return to_feature_collection(network.projection, area, properties)


def _clock(service_date: date, tz: ZoneInfo, seconds: int) -> datetime:
    """Seconds after the service day's midnight as a local datetime."""
    return datetime.combine(service_date, time(), tzinfo=tz) + timedelta(seconds=seconds)


def _journey(
    itinerary: Itinerary,
    origin: LatLon,
    destination: LatLon,
    departure: datetime,
    tz: ZoneInfo,
    t_start: int,
    network: StopNetwork,
    trips: TripInfo,
) -> Journey:
    if itinerary.arrive_s is None:
        return Journey(origin=origin, reachable=False, depart=departure)

    def point(stop: int | None, end: LatLon) -> LatLon:
        if stop is None:
            return end
        return LatLon(lat=float(network.lat[stop]), lon=float(network.lon[stop]))

    def name(stop: int | None, end: str) -> str:
        return end if stop is None else network.names[stop]

    day = departure.date()
    legs = []
    previous_arrival = t_start
    for leg in itinerary.legs:
        start = point(leg.from_stop, origin)
        stop = point(leg.to_stop, destination)
        journey_leg = JourneyLeg(
            mode="walk",
            from_name=name(leg.from_stop, "Start"),
            to_name=name(leg.to_stop, "Destination"),
            depart=_clock(day, tz, leg.depart_s),
            arrive=_clock(day, tz, leg.arrive_s),
            wait_s=max(0, leg.depart_s - previous_arrival),
            path=[start, stop],
        )
        if leg.kind == "walk":
            journey_leg.distance_m = round(leg.distance_m)
        else:
            assert leg.trip is not None
            trip = leg.trip % trips.num_trips  # timetable trips are numbered per day
            journey_leg.mode = route_mode(int(trips.route_type[trip]))
            journey_leg.line = trips.line[trip]
            journey_leg.headsign = trips.headsign[trip]
            journey_leg.num_stops = len(leg.path_stops) - 1
            journey_leg.path = [point(s, origin) for s in leg.path_stops]
        legs.append(journey_leg)
        previous_arrival = leg.arrive_s

    rides = sum(1 for leg in itinerary.legs if leg.kind == "ride")
    return Journey(
        origin=origin,
        reachable=True,
        depart=departure,
        arrive=_clock(day, tz, itinerary.arrive_s),
        duration_s=itinerary.arrive_s - t_start,
        num_changes=max(0, rides - 1),
        legs=legs,
    )


def route(store: CityDataStore, city: CityConfig, request: RouteRequest) -> RouteResponse:
    """Fastest journey from each origin to ``request.destination``, leg by leg."""
    started = clock.perf_counter()
    departure = local_departure(city, request.departure)
    network = store.network(city.id)
    timetable = store.timetable(city.id, departure.date())
    trips = store.trip_info(city.id)
    t_start = departure.hour * 3600 + departure.minute * 60 + departure.second
    t_end = t_start + request.max_minutes * 60

    journeys = []
    for origin in request.origins:
        sources = access_stops(
            network, origin.lat, origin.lon, t_start, request.walk_speed_mps, request.max_walk_m
        )
        result = scan(
            network,
            timetable,
            sources,
            t_start,
            t_end,
            request.walk_speed_mps,
            request.min_change_min * 60,
        )
        itinerary = itinerary_to(
            result,
            network,
            timetable,
            origin,
            request.destination,
            request.walk_speed_mps,
            request.max_walk_m,
        )
        journeys.append(
            _journey(
                itinerary,
                origin,
                request.destination,
                departure,
                city.tz,
                t_start,
                network,
                trips,
            )
        )

    durations = [j.duration_s for j in journeys if j.duration_s is not None]
    return RouteResponse(
        city=city.id,
        departure=departure,
        destination=request.destination,
        min_change_min=request.min_change_min,
        journeys=journeys,
        fastest_s=min(durations) if durations else None,
        everyone_s=max(durations) if len(durations) == len(journeys) else None,
        computation_ms=round((clock.perf_counter() - started) * 1000, 1),
    )
