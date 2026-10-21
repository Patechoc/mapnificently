"""Rebuild the fastest journey to a place from a :class:`~mapnificently.routing.csa.ScanResult`.

Going backwards from the destination: the stop we reached it from, the vehicle that brought us
there (and any walk after getting off), the stop where we boarded that vehicle, and so on until
the walk from the start point.
"""

from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from mapnificently.models import LatLon
from mapnificently.models.api import Mode
from mapnificently.routing.csa import NONE, UNREACHED, ScanResult
from mapnificently.timetable import WALK_DETOUR, StopNetwork, Timetable

MIN_WALK_LEG_M = 10.0  # shorter walks (e.g. a stop right at the destination) are not shown


@dataclass
class Leg:
    """A walk or a ride, in seconds after service-day midnight. ``None`` ends are the
    origin (``from_stop``) or the destination (``to_stop``)."""

    kind: Literal["walk", "ride"]
    from_stop: int | None
    to_stop: int | None
    depart_s: int
    arrive_s: int
    trip: int | None = None
    path_stops: list[int] = field(default_factory=list)
    distance_m: float = 0.0


@dataclass
class Itinerary:
    """The fastest way to the destination, or ``arrive_s=None`` if it can't be reached."""

    arrive_s: int | None
    legs: list[Leg]


def route_mode(route_type: int) -> Mode:
    """GTFS route type (basic or extended) to a mode name."""
    basic: dict[int, Mode] = {
        0: "tram", 1: "metro", 2: "rail", 3: "bus", 4: "ferry", 5: "tram",
        6: "cable car", 7: "funicular", 11: "bus", 12: "rail",
    }  # fmt: skip
    if route_type in basic:
        return basic[route_type]
    extended: list[tuple[int, int, Mode]] = [
        (100, 200, "rail"), (200, 300, "coach"), (400, 500, "metro"), (700, 900, "bus"),
        (900, 1000, "tram"), (1000, 1300, "ferry"), (1300, 1400, "cable car"),
        (1400, 1500, "funicular"),
    ]  # fmt: skip
    for low, high, mode in extended:
        if low <= route_type < high:
            return mode
    return "other"


def _distance(network: StopNetwork, a: int, b: int) -> float:
    return float(np.hypot(*(network.xy[a] - network.xy[b])))


def _distance_to(network: StopNetwork, stop: int, point: LatLon) -> float:
    x, y = network.projection.forward(point.lat, point.lon)
    return float(np.hypot(network.xy[stop, 0] - x, network.xy[stop, 1] - y))


def _ride_stops(timetable: Timetable, trip: int, first: int, last: int) -> list[int]:
    """Stops of ``trip`` from connection ``first`` to connection ``last``, in order."""
    window = slice(first, last + 1)
    on_trip = timetable.trip[window] == trip
    stops = timetable.dep_stop[window][on_trip].tolist()
    return [*stops, int(timetable.arr_stop[last])]


def legs_to_stop(
    result: ScanResult,
    network: StopNetwork,
    timetable: Timetable,
    origin: LatLon,
    stop: int,
    walk_speed_mps: float,
) -> list[Leg]:
    """Legs from the origin to ``stop``, which must have been reached."""
    seconds_per_m = WALK_DETOUR / walk_speed_mps
    legs: list[Leg] = []
    use_ready = False  # the last stop is arrived at; earlier ones are boarded from
    while True:
        conn = (result.ready_conn if use_ready else result.arr_conn)[stop]
        if conn == NONE:  # walked here from the start point
            legs.append(
                Leg(
                    "walk", None, stop, result.t_start, result.sources[stop],
                    distance_m=_distance_to(network, stop, origin) * WALK_DETOUR,
                )
            )  # fmt: skip
            break
        walk_from = (result.ready_walk_from if use_ready else result.arr_walk_from)[stop]
        if walk_from != NONE:
            got_off = int(timetable.arr_s[conn])
            dist = _distance(network, walk_from, stop)
            legs.append(
                Leg(
                    "walk", walk_from, stop, got_off, got_off + int(dist * seconds_per_m),
                    distance_m=dist * WALK_DETOUR,
                )
            )  # fmt: skip
        trip = int(timetable.trip[conn])
        boarded = result.board_conn[trip]
        board_stop = int(timetable.dep_stop[boarded])
        legs.append(
            Leg(
                "ride", board_stop, int(timetable.arr_stop[conn]),
                int(timetable.dep_s[boarded]), int(timetable.arr_s[conn]),
                trip=trip, path_stops=_ride_stops(timetable, trip, boarded, conn),
            )
        )  # fmt: skip
        stop = board_stop
        use_ready = True
    legs.reverse()
    return [leg for leg in legs if leg.kind == "ride" or leg.distance_m >= MIN_WALK_LEG_M]


def itinerary_to(
    result: ScanResult,
    network: StopNetwork,
    timetable: Timetable,
    origin: LatLon,
    destination: LatLon,
    walk_speed_mps: float,
    max_walk_m: float,
) -> Itinerary:
    """Fastest journey to a point: walk all the way, or ride to a stop near it and walk."""
    seconds_per_m = WALK_DETOUR / walk_speed_mps
    best_t: int | None = None
    best_stop: int | None = None

    ox, oy = network.projection.forward(origin.lat, origin.lon)
    dx, dy = network.projection.forward(destination.lat, destination.lon)
    direct = float(np.hypot(dx - ox, dy - oy))
    if direct * WALK_DETOUR <= max_walk_m:
        best_t = result.t_start + int(direct * seconds_per_m)

    stops, dists = network.stops_near(destination.lat, destination.lon, max_walk_m / WALK_DETOUR)
    for stop, dist in zip(stops.tolist(), dists.tolist(), strict=True):
        if result.arrival[stop] >= UNREACHED:
            continue
        t = result.arrival[stop] + int(dist * seconds_per_m)
        if best_t is None or t < best_t:
            best_t, best_stop = t, stop

    if best_t is None or best_t > result.t_end:
        return Itinerary(arrive_s=None, legs=[])
    if best_stop is None:
        walk = Leg("walk", None, None, result.t_start, best_t, distance_m=direct * WALK_DETOUR)
        return Itinerary(arrive_s=best_t, legs=[walk])

    legs = legs_to_stop(result, network, timetable, origin, best_stop, walk_speed_mps)
    last = _distance_to(network, best_stop, destination) * WALK_DETOUR
    if last >= MIN_WALK_LEG_M:
        legs.append(
            Leg("walk", best_stop, None, result.arrival[best_stop], best_t, distance_m=last)
        )
    return Itinerary(arrive_s=best_t, legs=legs)
