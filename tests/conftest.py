"""Shared fixtures: a tiny synthetic GTFS network with known answers.

Stops lie on an east-west line at lat 59.91, about 1.1 km apart::

    G ── A ── B ── C ── D        E (2.2 km north of C)
                   F (60 m from C)

Weekday service (WK, Mon-Fri, removed on 2026-12-24):
    T1   A 08:05 → B 08:10 → C 08:15 → D 08:20 → OUT 08:40 (OUT is outside the bbox)
    T2   F 08:20 → E 08:30
    T3   A 08:30 → D 08:35
    TN   A 25:10 → B 25:20                      (runs after midnight)
    TNB  B 08:11 (no boarding) → D 08:13
Saturday service (SAT):
    TS   A 08:05 → D 08:06
Second feed (calendar_dates only, runs 2026-10-22):
    TG   G 07:50 → A 07:58
"""

import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mapnificently.api import create_app
from mapnificently.models import CityConfig
from mapnificently.settings import Settings, get_settings
from mapnificently.timetable import CityDataStore

THURSDAY = "2026-10-22"
COORDS = {
    "A": (59.91, 10.72),
    "B": (59.91, 10.74),
    "C": (59.91, 10.76),
    "D": (59.91, 10.78),
    "E": (59.93, 10.76),
    "F": (59.9101, 10.761),
    "G": (59.91, 10.705),
}

FEED1 = {
    "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\n"
    "X,Test Transit,https://example.org,Europe/Oslo\n",
    "stops.txt": "﻿stop_id,stop_name,stop_lat,stop_lon,location_type,parent_station\n"
    + "".join(f"{s},Stop {s},{lat},{lon},,\n" for s, (lat, lon) in COORDS.items() if s != "G")
    + "OUT,Far away,60.5,11.5,,\n"
    + 'STATION,"Central, station",59.91,10.72,1,\n',
    "routes.txt": "route_id,agency_id,route_short_name,route_type\n"
    "L1,X,1,700\nL2,X,2,3\nLN,X,N,3\n",
    "trips.txt": "route_id,service_id,trip_id,trip_headsign\n"
    "L1,WK,T1,Stop D\nL2,WK,T2,\nL1,WK,T3,Stop D\nLN,WK,TN,\nL1,WK,TNB,\nL1,SAT,TS,\n",
    "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence,"
    "pickup_type,drop_off_type\n"
    "T1,08:05:00,08:05:00,A,1,,\n"
    "T1,08:10:00,08:10:00,B,2,,\n"
    "T1,08:15:00,08:15:00,C,3,,\n"
    "T1,08:20:00,08:20:00,D,4,,\n"
    "T1,08:40:00,08:40:00,OUT,5,,\n"
    "T2,08:20:00,08:20:00,F,1,,\n"
    "T2,08:30:00,08:30:00,E,2,,\n"
    "T3,08:30:00,08:30:00,A,1,,\n"
    "T3,08:35:00,08:35:00,D,2,,\n"
    "TN,25:10:00,25:10:00,A,1,,\n"
    "TN,25:20:00,25:20:00,B,2,,\n"
    "TNB,08:11:00,08:11:00,B,1,1,\n"
    "TNB,08:13:00,08:13:00,D,2,,\n"
    "TS,8:05:00,8:05:00,A,1,,\n"
    "TS,8:06:00,8:06:00,D,2,,\n",
    "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
    "start_date,end_date\n"
    "WK,1,1,1,1,1,0,0,20260101,20261231\n"
    "SAT,0,0,0,0,0,1,0,20260101,20261231\n",
    "calendar_dates.txt": "service_id,date,exception_type\nWK,20261224,2\n",
}

FEED2 = {
    "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\n"
    "Y,Other Transit,https://example.org,Europe/Oslo\n",
    "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\n"
    f"A,Duplicate A,{COORDS['A'][0]},{COORDS['A'][1]}\n"
    f"G,Stop G,{COORDS['G'][0]},{COORDS['G'][1]}\n",
    "routes.txt": "route_id,route_type\nLG,3\n",
    "trips.txt": "route_id,service_id,trip_id\nLG,X1,TG\n",
    "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
    "TG,07:50:00,07:50:00,G,1\n"
    "TG,07:58:00,07:58:00,A,2\n",
    "calendar_dates.txt": "service_id,date,exception_type\nX1,20261022,1\n",
}

TEST_BBOX = [10.70, 59.89, 10.80, 59.95]


def write_gtfs(path: Path, files: dict[str, str]) -> Path:
    """Zip GTFS text files."""
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return path


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-integration", action="store_true", help="run tests that download real feeds"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-integration"):
        return
    skip = pytest.mark.skip(reason="needs --run-integration (downloads real feeds)")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def feed_zips(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("gtfs")
    return write_gtfs(root / "feed1.zip", FEED1), write_gtfs(root / "feed2.zip", FEED2)


@pytest.fixture
def test_city(feed_zips: tuple[Path, Path]) -> CityConfig:
    return CityConfig(
        id="testville",
        name="Testville",
        timezone="Europe/Oslo",
        center=[59.91, 10.75],
        bbox=TEST_BBOX,
        provider="gtfs",
        feeds=[str(p) for p in feed_zips],
        attribution="Synthetic data",
    )


@pytest.fixture
def empty_city() -> CityConfig:
    return CityConfig(
        id="nodata",
        name="No Data",
        timezone="Europe/Oslo",
        center=[60.0, 11.0],
        bbox=[10.9, 59.9, 11.1, 60.1],
        provider="gtfs",
        feeds=["https://example.org/missing.zip"],
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def store(settings: Settings, test_city: CityConfig, empty_city: CityConfig) -> CityDataStore:
    store = CityDataStore(settings, {test_city.id: test_city, empty_city.id: empty_city})
    store.refresh(test_city.id)
    return store


@pytest.fixture
def client(settings: Settings, store: CityDataStore) -> Iterator[TestClient]:
    with TestClient(create_app(settings, store)) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _fresh_settings() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
