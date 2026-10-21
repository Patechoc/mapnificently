"""Minimum change time and journey reconstruction, on the synthetic network."""

from datetime import datetime
from pathlib import Path

import polars as pl
import pytest

from mapnificently import engine
from mapnificently.models import CityConfig, LatLon, ReachRequest, RouteRequest
from mapnificently.routing import route_mode
from mapnificently.timetable import CityDataStore, load_trip_info
from tests.conftest import COORDS


def at(stop: str) -> LatLon:
    lat, lon = COORDS[stop]
    return LatLon(lat=lat, lon=lon)


def arrivals(
    store: CityDataStore, city: CityConfig, origin: str, when: str, change: int, minutes: int = 60
) -> dict[str, int]:
    response = engine.reach(
        store,
        city,
        ReachRequest(
            origins=[at(origin)],
            departure=datetime.fromisoformat(when),
            max_minutes=minutes,
            min_change_min=change,
        ),
    )
    network = store.network(city.id)
    reach = response.origins[0]
    return {network.ids[i]: t for i, t in zip(reach.stop_idx, reach.arrival_s, strict=True)}


def route(store: CityDataStore, city: CityConfig, origins: list[LatLon], dest: LatLon, **kw):
    return engine.route(
        store,
        city,
        RouteRequest(
            origins=origins,
            destination=dest,
            departure=datetime.fromisoformat(kw.pop("when", "2026-10-22T08:00")),
            **kw,
        ),
    )


class TestMinimumChangeTime:
    def test_change_fits(self, store: CityDataStore, test_city: CityConfig):
        # T1 reaches C at 08:15; the walk to F is about 1 min; T2 leaves F at 08:20.
        assert arrivals(store, test_city, "A", "2026-10-22T08:00", change=5)["E"] == 30 * 60

    def test_change_too_short(self, store: CityDataStore, test_city: CityConfig):
        result = arrivals(store, test_city, "A", "2026-10-22T08:00", change=6)
        assert "E" not in result
        assert result["F"] < 17 * 60  # still walked there, just too late for T2

    def test_staying_on_board_needs_no_change(self, store: CityDataStore, test_city: CityConfig):
        result = arrivals(store, test_city, "A", "2026-10-22T08:00", change=15)
        assert result["D"] == 20 * 60

    def test_first_boarding_needs_no_change(self, store: CityDataStore, test_city: CityConfig):
        result = arrivals(store, test_city, "A", "2026-10-22T08:05", change=15)
        assert result["B"] == 5 * 60

    def test_change_at_same_stop(self, store: CityDataStore, test_city: CityConfig):
        # TG reaches A at 07:58, T1 leaves A at 08:05.
        assert arrivals(store, test_city, "G", "2026-10-22T07:45", change=7)["D"] == 35 * 60
        late = arrivals(store, test_city, "G", "2026-10-22T07:45", change=8)
        assert late["D"] == 50 * 60  # missed T1, took T3 at 08:30

    def test_default_is_two_minutes(self):
        assert ReachRequest(origins=[at("A")]).min_change_min == 2


class TestRoute:
    def test_ride_walk_ride(self, store: CityDataStore, test_city: CityConfig):
        response = route(store, test_city, [at("A")], at("E"))
        [journey] = response.journeys
        assert journey.reachable
        assert journey.duration_s == 30 * 60
        assert journey.num_changes == 1
        ride1, walk, ride2 = journey.legs
        assert (ride1.mode, ride1.line, ride1.headsign) == ("bus", "1", "Stop D")
        assert (ride1.from_name, ride1.to_name, ride1.num_stops) == ("Stop A", "Stop C", 2)
        assert [p.lon for p in ride1.path] == [10.72, 10.74, 10.76]
        assert ride1.depart.strftime("%H:%M") == "08:05"
        assert ride1.wait_s == 5 * 60
        assert (walk.mode, walk.from_name, walk.to_name) == ("walk", "Stop C", "Stop F")
        assert 50 < walk.distance_m < 100
        assert (ride2.line, ride2.headsign, ride2.to_name) == ("2", None, "Stop E")
        assert 3 * 60 < ride2.wait_s < 5 * 60  # at F from about 08:16 until 08:20

    def test_walk_only(self, store: CityDataStore, test_city: CityConfig):
        dest = LatLon(lat=59.912, lon=10.72)  # about 220 m north of A
        [journey] = route(store, test_city, [at("A")], dest).journeys
        [walk] = journey.legs
        assert walk.mode == "walk"
        assert (walk.from_name, walk.to_name) == ("Start", "Destination")
        assert journey.num_changes == 0

    def test_walk_to_destination_after_ride(self, store: CityDataStore, test_city: CityConfig):
        dest = LatLon(lat=59.913, lon=10.78)  # about 330 m north of D
        [journey] = route(store, test_city, [at("A")], dest).journeys
        assert [leg.mode for leg in journey.legs] == ["bus", "walk"]
        assert journey.legs[-1].to_name == "Destination"

    def test_unreachable(self, store: CityDataStore, test_city: CityConfig):
        [journey] = route(store, test_city, [at("A")], at("E"), max_minutes=20).journeys
        assert not journey.reachable
        assert journey.legs == []

    def test_several_origins(self, store: CityDataStore, test_city: CityConfig):
        near_d = LatLon(lat=59.913, lon=10.78)  # about 330 m from D: walk there
        response = route(store, test_city, [at("A"), near_d], at("D"))
        by_bus, on_foot = (j.duration_s for j in response.journeys)
        assert by_bus == 20 * 60
        assert 4 * 60 < on_foot < 6 * 60
        assert response.fastest_s == on_foot
        assert response.everyone_s == by_bus

    def test_one_origin_cannot_arrive(self, store: CityDataStore, test_city: CityConfig):
        response = route(store, test_city, [at("A"), at("E")], at("D"), max_minutes=30)
        assert [j.reachable for j in response.journeys] == [True, False]
        assert response.fastest_s == 20 * 60
        assert response.everyone_s is None


@pytest.mark.parametrize(
    ("route_type", "mode"),
    [(3, "bus"), (1, "metro"), (2, "rail"), (4, "ferry"), (0, "tram"), (100, "rail"),
     (401, "metro"), (700, "bus"), (712, "bus"), (900, "tram"), (1000, "ferry"),
     (1200, "ferry"), (202, "coach"), (1400, "funicular"), (1700, "other")],
)  # fmt: skip
def test_route_mode(route_type: int, mode: str):
    assert route_mode(route_type) == mode


def test_trip_info_from_older_prepared_data(tmp_path: Path):
    pl.DataFrame(
        {"trip_idx": [0], "route_id": ["ost:OST:Line:1_20"], "route_type": [3]}
    ).write_parquet(tmp_path / "trips.parquet")
    info = load_trip_info(tmp_path)
    assert info.line == ["1_20"]
    assert info.headsign == [None]
