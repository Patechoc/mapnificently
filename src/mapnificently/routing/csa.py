"""Earliest-arrival Connection Scan Algorithm (Dibbelt et al., 2013).

Connections are scanned once in departure order. A connection can be used when its trip was
already boarded, or when we are at its departure stop in time. Each improved arrival is
spread to nearby stops on foot.
"""

import numpy as np

from mapnificently.timetable import WALK_DETOUR, StopNetwork, Timetable

UNREACHED = 1 << 30


def earliest_arrivals(
    network: StopNetwork,
    timetable: Timetable,
    sources: dict[int, int],
    t_start: int,
    t_end: int,
    walk_speed_mps: float,
) -> dict[int, int]:
    """Earliest arrival (seconds after midnight) at every stop reachable by ``t_end``.

    ``sources`` maps stop index to the time we can be there (after walking from the origin).
    """
    arrival = [UNREACHED] * network.num_stops
    for stop, t in sources.items():
        arrival[stop] = min(arrival[stop], t)

    lo = int(np.searchsorted(timetable.dep_s, t_start, side="left"))
    hi = int(np.searchsorted(timetable.dep_s, t_end, side="right"))
    dep_stop = timetable.dep_stop[lo:hi].tolist()
    arr_stop = timetable.arr_stop[lo:hi].tolist()
    dep_s = timetable.dep_s[lo:hi].tolist()
    arr_s = timetable.arr_s[lo:hi].tolist()
    trips = timetable.trip[lo:hi].tolist()
    board = timetable.board[lo:hi].tolist()
    alight = timetable.alight[lo:hi].tolist()
    footpaths = network.footpaths()
    seconds_per_m = WALK_DETOUR / walk_speed_mps
    on_trip = bytearray(timetable.num_trips)

    for i in range(hi - lo):
        trip = trips[i]
        if not on_trip[trip]:
            if not board[i] or arrival[dep_stop[i]] > dep_s[i]:
                continue
            on_trip[trip] = 1
        if not alight[i]:
            continue
        t = arr_s[i]
        stop = arr_stop[i]
        if t < arrival[stop]:
            arrival[stop] = t
            for other, dist in footpaths[stop]:
                t_walk = t + int(dist * seconds_per_m)
                if t_walk < arrival[other]:  # noqa: PLR1730 - faster than min() here
                    arrival[other] = t_walk

    return {stop: t for stop, t in enumerate(arrival) if t <= t_end}


def access_stops(
    network: StopNetwork,
    lat: float,
    lon: float,
    t_start: int,
    walk_speed_mps: float,
    max_walk_m: float,
) -> dict[int, int]:
    """Stops within walking distance of a point, with the time we get there."""
    idx, dist = network.stops_near(lat, lon, max_walk_m / WALK_DETOUR)
    seconds = dist * WALK_DETOUR / walk_speed_mps
    return {int(s): t_start + int(w) for s, w in zip(idx, seconds, strict=True)}
