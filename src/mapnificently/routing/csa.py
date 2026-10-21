"""Earliest-arrival Connection Scan Algorithm (Dibbelt et al., 2013).

Connections are scanned once in departure order. A connection can be used when its trip was
already boarded, or when we are ready to board at its departure stop in time. Each improved
arrival is spread to nearby stops on foot.

Every stop has two times:

- ``arrival``: when we first get there. This is what the map shows.
- ``ready``: when we can board a vehicle there. Getting off one vehicle and onto another takes
  at least ``min_change_s``, or the walk to another stop if that is longer. Staying on the same
  vehicle, and boarding after walking from the start point, need no change time.

The scan also records how each time was obtained, so :mod:`mapnificently.routing.journey` can
rebuild the fastest journey to any stop.
"""

from dataclasses import dataclass, field

import numpy as np

from mapnificently.timetable import WALK_DETOUR, StopNetwork, Timetable

UNREACHED = 1 << 30
NONE = -1


@dataclass
class ScanResult:
    """Times per stop, plus how they were reached.

    For both ``arrival`` and ``ready``, ``*_conn`` is the connection (index into the timetable)
    whose vehicle got us there, or ``NONE`` when we walked from the start point; ``*_walk_from``
    is the stop where we got off before walking here, or ``NONE`` when we got off right here.
    """

    t_start: int
    t_end: int
    sources: dict[int, int]
    arrival: list[int]
    arr_conn: list[int]
    arr_walk_from: list[int]
    ready: list[int]
    ready_conn: list[int]
    ready_walk_from: list[int]
    board_conn: dict[int, int] = field(default_factory=dict)  # trip -> connection boarded

    def arrivals(self) -> dict[int, int]:
        """Stop index → earliest arrival, for stops reached by ``t_end``."""
        return {s: t for s, t in enumerate(self.arrival) if t <= self.t_end}


def scan(  # noqa: PLR0915 - one tight loop; helpers would slow it down
    network: StopNetwork,
    timetable: Timetable,
    sources: dict[int, int],
    t_start: int,
    t_end: int,
    walk_speed_mps: float,
    min_change_s: int = 0,
) -> ScanResult:
    """Run the scan from ``sources`` (stop index → time we get there on foot)."""
    n = network.num_stops
    arrival = [UNREACHED] * n
    arr_conn = [NONE] * n
    arr_walk_from = [NONE] * n
    for stop, t in sources.items():
        arrival[stop] = min(arrival[stop], t)
    ready = list(arrival)
    ready_conn = [NONE] * n
    ready_walk_from = [NONE] * n
    board_conn: dict[int, int] = {}

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
            if not board[i] or ready[dep_stop[i]] > dep_s[i]:
                continue
            on_trip[trip] = 1
            board_conn[trip] = lo + i
        if not alight[i]:
            continue
        t = arr_s[i]
        stop = arr_stop[i]
        if t >= arrival[stop]:
            continue
        conn = lo + i
        arrival[stop] = t
        arr_conn[stop] = conn
        arr_walk_from[stop] = NONE
        t_ready = t + min_change_s
        if t_ready < ready[stop]:
            ready[stop] = t_ready
            ready_conn[stop] = conn
            ready_walk_from[stop] = NONE
        for other, dist in footpaths[stop]:
            walk = int(dist * seconds_per_m)
            t_walk = t + walk
            if t_walk < arrival[other]:
                arrival[other] = t_walk
                arr_conn[other] = conn
                arr_walk_from[other] = stop
            t_ready = t + (walk if walk > min_change_s else min_change_s)
            if t_ready < ready[other]:
                ready[other] = t_ready
                ready_conn[other] = conn
                ready_walk_from[other] = stop

    return ScanResult(
        t_start=t_start,
        t_end=t_end,
        sources=sources,
        arrival=arrival,
        arr_conn=arr_conn,
        arr_walk_from=arr_walk_from,
        ready=ready,
        ready_conn=ready_conn,
        ready_walk_from=ready_walk_from,
        board_conn=board_conn,
    )


def earliest_arrivals(
    network: StopNetwork,
    timetable: Timetable,
    sources: dict[int, int],
    t_start: int,
    t_end: int,
    walk_speed_mps: float,
    min_change_s: int = 0,
) -> dict[int, int]:
    """Earliest arrival (seconds after midnight) at every stop reachable by ``t_end``."""
    return scan(
        network, timetable, sources, t_start, t_end, walk_speed_mps, min_change_s
    ).arrivals()


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
