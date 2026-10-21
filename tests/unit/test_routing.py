"""Loader, timetable and Connection Scan behaviour on the synthetic network."""

from datetime import date, datetime

import polars as pl
import pytest

from mapnificently import engine
from mapnificently.gtfs import Feed, FeedError, prepare_city
from mapnificently.models import BBox, CityConfig, LatLon, ReachRequest
from mapnificently.timetable import CityDataStore
from tests.conftest import COORDS, FEED1, TEST_BBOX, write_gtfs


def arrivals(
    store: CityDataStore, city: CityConfig, origin: str, when: str, minutes: int = 30
) -> dict[str, int]:
    """Stop id → seconds after departure, from the stop ``origin``."""
    lat, lon = COORDS[origin]
    response = engine.reach(
        store,
        city,
        ReachRequest(
            origins=[LatLon(lat=lat, lon=lon)],
            departure=datetime.fromisoformat(when),
            max_minutes=minutes,
        ),
    )
    network = store.network(city.id)
    reach = response.origins[0]
    return {network.ids[i]: t for i, t in zip(reach.stop_idx, reach.arrival_s, strict=True)}


class TestLoader:
    def test_bbox_and_location_type_filter(self, store: CityDataStore, test_city: CityConfig):
        ids = store.network(test_city.id).ids
        assert "OUT" not in ids
        assert "STATION" not in ids
        assert set(ids) == set(COORDS)

    def test_duplicate_stop_keeps_first_feed(self, store: CityDataStore, test_city: CityConfig):
        network = store.network(test_city.id)
        assert network.names[network.ids.index("A")] == "Stop A"

    def test_times_past_midnight_and_short_hours(self, store: CityDataStore, test_city: CityConfig):
        prepared = store.prepared_dir(test_city.id)
        stop_times = pl.read_parquet(prepared / "stop_times.parquet")
        assert stop_times["dep_s"].max() == 25 * 3600 + 20 * 60
        assert 8 * 3600 + 5 * 60 in stop_times["dep_s"].to_list()

    def test_extended_route_type_kept(self, store: CityDataStore, test_city: CityConfig):
        trips = pl.read_parquet(store.prepared_dir(test_city.id) / "trips.parquet")
        assert 700 in trips["route_type"].to_list()
        assert trips["trip_id"].str.starts_with("feed1:").any()

    def test_meta(self, store: CityDataStore, test_city: CityConfig):
        meta = store.meta(test_city.id)
        assert meta["num_stops"] == len(COORDS)
        assert meta["service_start"] == "2026-01-01"
        assert meta["service_end"] == "2026-12-31"
        assert [f["name"] for f in meta["feeds"]] == ["feed1", "feed2"]

    def test_missing_file_is_reported(self, tmp_path):
        files = {k: v for k, v in FEED1.items() if k != "stop_times.txt"}
        feed = Feed("broken", write_gtfs(tmp_path / "broken.zip", files))
        with pytest.raises(FeedError, match="stop_times"):
            prepare_city([feed], BBox.from_list(TEST_BBOX), tmp_path / "out")

    def test_missing_calendars_is_reported(self, tmp_path):
        files = {k: v for k, v in FEED1.items() if not k.startswith("calendar")}
        feed = Feed("nocal", write_gtfs(tmp_path / "nocal.zip", files))
        with pytest.raises(FeedError, match="calendar"):
            prepare_city([feed], BBox.from_list(TEST_BBOX), tmp_path / "out")


class TestTimetable:
    def test_cached_on_disk(self, store: CityDataStore, test_city: CityConfig):
        timetable = store.timetable(test_city.id, date(2026, 10, 22))
        assert (store.prepared_dir(test_city.id) / "tt-2026-10-22.npz").is_file()
        store.invalidate(test_city.id)
        again = store.timetable(test_city.id, date(2026, 10, 22))
        assert again.num_connections == timetable.num_connections

    def test_sorted_by_departure(self, store: CityDataStore, test_city: CityConfig):
        timetable = store.timetable(test_city.id, date(2026, 10, 22))
        assert (timetable.dep_s[1:] >= timetable.dep_s[:-1]).all()

    def test_lru_keeps_cache_size(self, store: CityDataStore, test_city: CityConfig):
        for day in range(1, 8):
            store.timetable(test_city.id, date(2026, 10, day))
        assert len(store._timetables) == store.settings.timetable_cache_size

    def test_footpath_links_close_stops(self, store: CityDataStore, test_city: CityConfig):
        network = store.network(test_city.id)
        c, f = network.ids.index("C"), network.ids.index("F")
        assert f in [to for to, _ in network.footpaths()[c]]
        a = network.ids.index("A")
        assert network.footpaths()[a] == []


class TestConnectionScan:
    def test_direct_ride(self, store: CityDataStore, test_city: CityConfig):
        result = arrivals(store, test_city, "A", "2026-10-22T08:00")
        assert result["A"] == 0
        assert result["B"] == 10 * 60
        assert result["C"] == 15 * 60
        assert result["D"] == 20 * 60

    def test_transfer_by_foot(self, store: CityDataStore, test_city: CityConfig):
        result = arrivals(store, test_city, "A", "2026-10-22T08:00")
        assert 15 * 60 < result["F"] < 17 * 60  # walked from C
        assert result["E"] == 30 * 60  # then T2

    def test_missed_departure(self, store: CityDataStore, test_city: CityConfig):
        result = arrivals(store, test_city, "A", "2026-10-22T08:06")
        assert "B" not in result  # T1 left at 08:05
        assert result["D"] == 29 * 60  # T3 at 08:30

    def test_no_boarding_respected(self, store: CityDataStore, test_city: CityConfig):
        result = arrivals(store, test_city, "B", "2026-10-22T08:10:30")
        assert "D" not in result

    def test_previous_day_after_midnight(self, store: CityDataStore, test_city: CityConfig):
        result = arrivals(store, test_city, "A", "2026-10-23T01:00")
        assert result["B"] == 20 * 60

    def test_saturday_service(self, store: CityDataStore, test_city: CityConfig):
        result = arrivals(store, test_city, "A", "2026-10-24T08:00")
        assert result["D"] == 6 * 60
        assert "B" not in result

    def test_removed_by_calendar_exception(self, store: CityDataStore, test_city: CityConfig):
        result = arrivals(store, test_city, "A", "2026-12-24T08:00")
        assert set(result) == {"A"}

    def test_second_feed_calendar_dates_only(self, store: CityDataStore, test_city: CityConfig):
        result = arrivals(store, test_city, "G", "2026-10-22T07:45", minutes=40)
        assert result["A"] == 13 * 60
        assert result["D"] == 35 * 60  # TG to A, then T1

    def test_time_budget(self, store: CityDataStore, test_city: CityConfig):
        result = arrivals(store, test_city, "A", "2026-10-22T08:00", minutes=12)
        assert set(result) == {"A", "B"}

    def test_departure_timezone(self, test_city: CityConfig):
        naive = engine.local_departure(test_city, datetime(2026, 10, 22, 8, 0))
        assert naive.utcoffset() is not None
        assert naive.hour == 8
        aware = engine.local_departure(
            test_city, datetime.fromisoformat("2026-10-22T06:00:00+00:00")
        )
        assert aware.hour == 8
        assert engine.local_departure(test_city, None).tzinfo is not None
