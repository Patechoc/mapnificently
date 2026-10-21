"""Static (offline) export."""

import json
from datetime import date
from pathlib import Path

import numpy as np
import pytest
from click.testing import CliRunner

from mapnificently.cli import cli
from mapnificently.models import CityConfig
from mapnificently.static_site import ALIGHT, BOARD, export_static_site, write_container
from mapnificently.timetable import CityDataStore

DAYS = [date(2026, 10, 22), date(2026, 10, 23)]


def read_container(path: Path) -> tuple[dict, dict[str, np.ndarray]]:
    """What offline.js does: header length, JSON header, typed arrays at absolute offsets."""
    raw = path.read_bytes()
    length = int.from_bytes(raw[:4], "little")
    header = json.loads(raw[4 : 4 + length])
    arrays = {}
    for name, a in header["arrays"].items():
        assert a["offset"] % 8 == 0, f"{name} is not 8-byte aligned"
        dtype = np.dtype(a["dtype"]).newbyteorder("<")
        arrays[name] = np.frombuffer(raw, dtype=dtype, count=a["length"], offset=a["offset"])
    return header["meta"], arrays


def test_container_round_trip(tmp_path: Path):
    arrays = {
        "a": np.arange(5, dtype=np.int32),
        "flags": np.array([1, 2, 3], dtype=np.uint8),
        "x": np.linspace(0, 1, 7),
    }
    write_container(tmp_path / "c.bin", {"hello": "Ålesund"}, arrays)
    meta, back = read_container(tmp_path / "c.bin")
    assert meta == {"hello": "Ålesund"}
    for name, array in arrays.items():
        np.testing.assert_array_equal(back[name], array)


@pytest.fixture
def site(store: CityDataStore, test_city: CityConfig, tmp_path: Path) -> Path:
    out = tmp_path / "site"
    export_static_site(store, [test_city.id], DAYS, out)
    return out


def test_site_layout(site: Path):
    for path in [
        "index.html",
        "sw.js",
        ".nojekyll",
        "data/manifest.json",
        "static/app.js",
        "static/offline.js",
        "static/vendor/leaflet/leaflet.min.js",
    ]:
        assert (site / path).is_file(), path
    index = (site / "index.html").read_text()
    assert '<script src="static/offline.js"></script>' in index
    assert "https://" not in index.split("<body>")[0]  # no CDN: works offline
    worker = (site / "sw.js").read_text()
    assert "__VERSION__" not in worker
    assert not (site / "static" / "sw.js").exists()


def test_manifest(site: Path, test_city: CityConfig):
    manifest = json.loads((site / "data" / "manifest.json").read_text())
    [city] = manifest["cities"]
    assert city["id"] == test_city.id
    assert city["data_ready"] is True
    assert city["dates"] == ["2026-10-22", "2026-10-23"]
    assert city["files"]["timetables"]["2026-10-22"] == "data/testville/tt-2026-10-22.bin"
    assert manifest["max_minutes"] >= 180
    assert manifest["walk_detour"] > 1


def test_network_file(site: Path, store: CityDataStore, test_city: CityConfig):
    meta, arrays = read_container(site / "data" / test_city.id / "network.bin")
    network = store.network(test_city.id)
    assert meta["names"] == network.names
    np.testing.assert_array_equal(arrays["lat"], network.lat)
    np.testing.assert_array_equal(arrays["fp_to"], network.fp_to)
    lines = [meta["lines"][i] for i in arrays["trip_line"]]
    assert lines == store.trip_info(test_city.id).line
    headsigns = [meta["headsigns"][i] if i >= 0 else None for i in arrays["trip_headsign"]]
    assert headsigns == store.trip_info(test_city.id).headsign


def test_timetable_file(site: Path, store: CityDataStore, test_city: CityConfig):
    meta, arrays = read_container(site / "data" / test_city.id / "tt-2026-10-22.bin")
    timetable = store.timetable(test_city.id, DAYS[0])
    assert meta["num_trips"] == timetable.num_trips
    np.testing.assert_array_equal(arrays["dep_s"], timetable.dep_s)
    np.testing.assert_array_equal(arrays["trip"], timetable.trip)
    np.testing.assert_array_equal((arrays["flags"] & BOARD) > 0, timetable.board)
    np.testing.assert_array_equal((arrays["flags"] & ALIGHT) > 0, timetable.alight)


def test_never_replaces_other_directories(
    store: CityDataStore, test_city: CityConfig, tmp_path: Path
):
    precious = tmp_path / "precious"
    precious.mkdir()
    (precious / "notes.txt").write_text("keep me")
    with pytest.raises(FileExistsError):
        export_static_site(store, [test_city.id], DAYS, precious)
    assert (precious / "notes.txt").read_text() == "keep me"


def test_replaces_a_previous_export(site: Path, store: CityDataStore, test_city: CityConfig):
    (site / "data" / test_city.id / "tt-2026-10-23.bin").unlink()
    export_static_site(store, [test_city.id], DAYS[:1], site)
    assert sorted(p.name for p in (site / "data" / test_city.id).iterdir()) == [
        "network.bin",
        "tt-2026-10-22.bin",
    ]


def test_cli_export(tmp_path: Path, feed_zips, monkeypatch: pytest.MonkeyPatch):
    cities_file = tmp_path / "cities.toml"
    feeds = ", ".join(f'"{p}"' for p in feed_zips)
    cities_file.write_text(
        '[cities.testville]\nname = "Testville"\ntimezone = "Europe/Oslo"\n'
        "center = [59.91, 10.75]\nbbox = [10.70, 59.89, 10.80, 59.95]\n"
        f'provider = "gtfs"\nfeeds = [{feeds}]\n'
    )
    monkeypatch.setenv("MAPNIFICENTLY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MAPNIFICENTLY_CITIES_FILE", str(cities_file))
    runner = CliRunner()
    assert runner.invoke(cli, ["data", "fetch", "testville"]).exit_code == 0
    out = tmp_path / "site"
    result = runner.invoke(
        cli, ["export-static", "testville", "--start", "2026-10-22", "--days", "3", "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert "testville (testville): 3 days" in result.output
    assert "no trips" not in result.output
    assert len(list((out / "data" / "testville").glob("tt-*.bin"))) == 3
    missing = runner.invoke(cli, ["export-static", "oslo", "-o", str(out)])
    assert missing.exit_code == 2
    assert "no data for oslo" in missing.output
    late = runner.invoke(
        cli, ["export-static", "testville", "--start", "2030-01-01", "--days", "1", "-o", str(out)]
    )
    assert "warning: no trips on 2030-01-01" in late.output
