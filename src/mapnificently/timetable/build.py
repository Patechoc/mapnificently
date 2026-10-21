"""Build routing structures from a prepared city.

- :class:`StopNetwork`: stops, their projected coordinates and walking links between them.
  It doesn't depend on the date.
- :class:`Timetable`: every vehicle hop (connection) of one service day, sorted by departure.
  Trips of the previous day still running after midnight, and early trips of the next day,
  are included with their times shifted, so a query near midnight works.
"""

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import polars as pl
from scipy.spatial import cKDTree

from mapnificently.geo import LocalProjection
from mapnificently.gtfs import active_services

IntArray = npt.NDArray[np.int32]
BoolArray = npt.NDArray[np.bool_]
FloatArray = npt.NDArray[np.float64]

DAY_S = 86_400
FOOTPATH_RADIUS_M = 400.0  # straight-line distance for stop-to-stop walking links
WALK_DETOUR = 1.2  # streets are longer than straight lines
NEXT_DAY_HORIZON_S = 6 * 3600  # keep next-day trips departing before 06:00


@dataclass
class StopNetwork:
    """Stops of a city plus walking links (CSR arrays, distances in metres)."""

    ids: list[str]
    names: list[str]
    lat: FloatArray
    lon: FloatArray
    projection: LocalProjection
    xy: FloatArray
    tree: Any  # scipy.spatial.cKDTree
    fp_ptr: IntArray
    fp_to: IntArray
    fp_dist: FloatArray
    _fp_lists: list[list[tuple[int, float]]] | None = field(default=None, repr=False)

    @property
    def num_stops(self) -> int:
        """Number of stops."""
        return len(self.ids)

    def footpaths(self) -> list[list[tuple[int, float]]]:
        """Walking links as Python lists, built once, for the routing hot loop."""
        if self._fp_lists is None:
            ptr = self.fp_ptr.tolist()
            to = self.fp_to.tolist()
            dist = self.fp_dist.tolist()
            self._fp_lists = [
                list(zip(to[ptr[i] : ptr[i + 1]], dist[ptr[i] : ptr[i + 1]], strict=True))
                for i in range(self.num_stops)
            ]
        return self._fp_lists

    def stops_near(self, lat: float, lon: float, radius_m: float) -> tuple[IntArray, FloatArray]:
        """Stops within ``radius_m`` (straight line) of a point, with their distances."""
        x, y = self.projection.forward(lat, lon)
        point = np.array([float(x), float(y)])
        idx = np.sort(np.asarray(self.tree.query_ball_point(point, radius_m), dtype=np.int32))
        if idx.size == 0:
            return idx, np.zeros(0)
        dist = np.hypot(self.xy[idx, 0] - point[0], self.xy[idx, 1] - point[1])
        return idx, dist


@dataclass
class Timetable:
    """Connections of one service day, sorted by departure time (seconds after midnight)."""

    service_date: date
    dep_stop: IntArray
    arr_stop: IntArray
    dep_s: IntArray
    arr_s: IntArray
    trip: IntArray  # unique per (trip, day), indexes the "boarded" flags during routing
    board: BoolArray
    alight: BoolArray
    num_trips: int

    @property
    def num_connections(self) -> int:
        """Number of connections."""
        return len(self.dep_s)


@dataclass
class TripInfo:
    """What riders see of each trip, indexed by trip index."""

    route_type: IntArray
    line: list[str]
    headsign: list[str | None]

    @property
    def num_trips(self) -> int:
        """Number of trips."""
        return len(self.line)


def load_trip_info(prepared_dir: Path) -> TripInfo:
    """Line names, directions and route types of all trips."""
    trips = pl.read_parquet(prepared_dir / "trips.parquet").sort("trip_idx")
    if "line" not in trips.columns:  # prepared before line names were kept
        trips = trips.with_columns(
            pl.col("route_id").str.split(":").list.last().alias("line"),
            pl.lit(None, dtype=pl.String).alias("headsign"),
        )
    return TripInfo(
        route_type=trips["route_type"].to_numpy().astype(np.int32),
        line=trips["line"].to_list(),
        headsign=trips["headsign"].to_list(),
    )


def read_meta(prepared_dir: Path) -> dict[str, Any]:
    """The ``meta.json`` written by :func:`mapnificently.gtfs.prepare_city`."""
    data: dict[str, Any] = json.loads((prepared_dir / "meta.json").read_text())
    return data


def build_network(prepared_dir: Path, center_lat: float, center_lon: float) -> StopNetwork:
    """Load stops and link every pair closer than :data:`FOOTPATH_RADIUS_M`."""
    stops = pl.read_parquet(prepared_dir / "stops.parquet").sort("stop_idx")
    lat = stops["lat"].to_numpy().astype(np.float64)
    lon = stops["lon"].to_numpy().astype(np.float64)
    projection = LocalProjection(center_lat, center_lon)
    x, y = projection.forward(lat, lon)
    xy = np.column_stack([x, y]) if len(lat) else np.zeros((0, 2))
    tree = cKDTree(xy)

    pairs = tree.query_pairs(FOOTPATH_RADIUS_M, output_type="ndarray")
    if len(pairs):
        src = np.concatenate([pairs[:, 0], pairs[:, 1]])
        dst = np.concatenate([pairs[:, 1], pairs[:, 0]])
    else:
        src = dst = np.zeros(0, dtype=np.int64)
    order = np.lexsort((dst, src))
    src, dst = src[order], dst[order]
    dist = np.hypot(xy[src, 0] - xy[dst, 0], xy[src, 1] - xy[dst, 1]) if len(src) else np.zeros(0)
    counts = np.bincount(src, minlength=len(lat))
    fp_ptr = np.concatenate([[0], np.cumsum(counts)]).astype(np.int32)

    return StopNetwork(
        ids=stops["stop_id"].to_list(),
        names=stops["name"].to_list(),
        lat=lat,
        lon=lon,
        projection=projection,
        xy=xy,
        tree=tree,
        fp_ptr=fp_ptr,
        fp_to=dst.astype(np.int32),
        fp_dist=dist.astype(np.float64),
    )


def _connections_for_day(
    stop_times: pl.DataFrame, trips: pl.DataFrame, services: set[str]
) -> pl.DataFrame:
    """Consecutive stop pairs of the trips running under ``services``."""
    trip_idx = trips.filter(pl.col("service_id").is_in(list(services)))["trip_idx"]
    st = stop_times.filter(pl.col("trip_idx").is_in(trip_idx.implode()))
    nxt = st.shift(-1)
    return (
        pl.DataFrame(
            {
                "trip": st["trip_idx"],
                "dep_stop": st["stop_idx"],
                "arr_stop": nxt["stop_idx"],
                "dep_s": st["dep_s"],
                "arr_s": nxt["arr_s"],
                "board": st["board"],
                "alight": nxt["alight"],
                "same_trip": st["trip_idx"] == nxt["trip_idx"],
            }
        )
        .filter(pl.col("same_trip").fill_null(value=False))
        .drop("same_trip")
    )


def build_timetable(prepared_dir: Path, service_date: date) -> Timetable:
    """Connections usable on ``service_date``, cached as ``tt-<date>.npz``."""
    cache = prepared_dir / f"tt-{service_date.isoformat()}.npz"
    if cache.is_file():
        return _load_timetable(cache, service_date)

    trips = pl.read_parquet(prepared_dir / "trips.parquet", columns=["trip_idx", "service_id"])
    stop_times = pl.read_parquet(prepared_dir / "stop_times.parquet")
    calendar = pl.read_parquet(prepared_dir / "calendar.parquet")
    calendar_dates = pl.read_parquet(prepared_dir / "calendar_dates.parquet")
    num_trips = trips.height

    parts = []
    for day_index, offset in enumerate((-DAY_S, 0, DAY_S)):
        day = service_date + timedelta(days=day_index - 1)
        services = active_services(calendar, calendar_dates, day)
        if not services:
            continue
        conns = _connections_for_day(stop_times, trips, services).with_columns(
            pl.col("dep_s") + offset,
            pl.col("arr_s") + offset,
            pl.col("trip") + day_index * num_trips,
        )
        if offset < 0:
            conns = conns.filter(pl.col("arr_s") >= 0)
        elif offset > 0:
            conns = conns.filter(pl.col("dep_s") < DAY_S + NEXT_DAY_HORIZON_S)
        parts.append(conns)

    conns = pl.concat(parts) if parts else _connections_for_day(stop_times, trips, set())
    conns = conns.filter(pl.col("arr_s") >= pl.col("dep_s")).sort("dep_s", "arr_s")
    timetable = Timetable(
        service_date=service_date,
        dep_stop=conns["dep_stop"].to_numpy().astype(np.int32),
        arr_stop=conns["arr_stop"].to_numpy().astype(np.int32),
        dep_s=conns["dep_s"].to_numpy().astype(np.int32),
        arr_s=conns["arr_s"].to_numpy().astype(np.int32),
        trip=conns["trip"].to_numpy().astype(np.int32),
        board=conns["board"].to_numpy().astype(np.bool_),
        alight=conns["alight"].to_numpy().astype(np.bool_),
        num_trips=3 * num_trips,
    )
    np.savez(
        cache,
        dep_stop=timetable.dep_stop,
        arr_stop=timetable.arr_stop,
        dep_s=timetable.dep_s,
        arr_s=timetable.arr_s,
        trip=timetable.trip,
        board=timetable.board,
        alight=timetable.alight,
        num_trips=np.array([timetable.num_trips]),
    )
    return timetable


def _load_timetable(path: Path, service_date: date) -> Timetable:
    with np.load(path) as data:
        return Timetable(
            service_date=service_date,
            dep_stop=data["dep_stop"],
            arr_stop=data["arr_stop"],
            dep_s=data["dep_s"],
            arr_s=data["arr_s"],
            trip=data["trip"],
            board=data["board"],
            alight=data["alight"],
            num_trips=int(data["num_trips"][0]),
        )


def cached_dates(prepared_dir: Path) -> list[date]:
    """Service dates with a built timetable on disk."""
    return sorted(
        date.fromisoformat(p.stem.removeprefix("tt-")) for p in prepared_dir.glob("tt-*.npz")
    )
