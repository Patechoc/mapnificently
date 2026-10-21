"""Command line interface, talking to the app in-process."""

import json
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from mapnificently.cli import cli

A = "59.91,10.72"


def run(client: TestClient, *args: str):
    return CliRunner().invoke(cli, list(args), obj={"client": client})


def test_cities(client: TestClient):
    result = run(client, "cities")
    assert result.exit_code == 0, result.output
    assert "testville" in result.output
    assert "nodata" in result.output


def test_reach(client: TestClient):
    result = run(
        client, "reach", "testville", "--from", A, "--at", "2026-10-22T08:00", "--minutes", "30"
    )
    assert result.exit_code == 0, result.output
    assert "Stop E" in result.output
    assert "30:00" in result.output


def test_reach_json(client: TestClient):
    result = run(client, "reach", "testville", "--from", A, "--at", "2026-10-22T08:00", "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["city"] == "testville"


def test_isochrone_to_file(client: TestClient, tmp_path: Path):
    out = tmp_path / "area.geojson"
    result = run(
        client,
        "isochrone",
        "testville",
        "--from",
        A,
        "--from",
        "59.91,10.78",
        "--at",
        "2026-10-22T08:00",
        "--minutes",
        "20",
        "--mode",
        "intersection",
        "-o",
        str(out),
    )
    assert result.exit_code == 0, result.output
    fc = json.loads(out.read_text())
    assert fc["features"][0]["properties"]["mode"] == "intersection"


def test_place_name_is_geocoded(client: TestClient, store):
    store.http_client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "features": [
                        {
                            "geometry": {"coordinates": [10.72, 59.91]},
                            "properties": {"name": "Stop A", "city": "Testville"},
                        }
                    ]
                },
            )
        )
    )
    store._providers.clear()
    result = run(client, "reach", "testville", "--from", "Stop A", "--at", "08:00")
    assert result.exit_code == 0, result.output
    assert "Stop A, Testville" in result.output
    geo = run(client, "geocode", "testville", "stop a")
    assert "59.91000,10.72000" in geo.output


def test_api_errors_are_readable(client: TestClient):
    result = run(client, "reach", "atlantis", "--from", A)
    assert result.exit_code == 1
    assert "404" in result.output
    assert "unknown city" in result.output


def test_api_down():
    down = httpx.Client(
        base_url="http://test",
        transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("x"))),
    )
    result = CliRunner().invoke(cli, ["cities"], obj={"client": down})
    assert result.exit_code == 1
    assert "mapnificently serve" in result.output


@pytest.fixture
def local_env(tmp_path: Path, feed_zips, monkeypatch: pytest.MonkeyPatch) -> Path:
    cities_file = tmp_path / "cities.toml"
    feeds = ", ".join(f'"{p}"' for p in feed_zips)
    cities_file.write_text(
        '[cities.testville]\nname = "Testville"\ntimezone = "Europe/Oslo"\n'
        "center = [59.91, 10.75]\nbbox = [10.70, 59.89, 10.80, 59.95]\n"
        f'provider = "gtfs"\nfeeds = [{feeds}]\n'
    )
    monkeypatch.setenv("MAPNIFICENTLY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MAPNIFICENTLY_CITIES_FILE", str(cities_file))
    return tmp_path


def test_data_fetch_and_status(local_env: Path):
    runner = CliRunner()
    result = runner.invoke(cli, ["data", "fetch", "testville"])
    assert result.exit_code == 0, result.output
    assert "7 stops" in result.output
    status = runner.invoke(cli, ["data", "status"])
    assert "testville" in status.output
    assert "ready" in status.output
    assert "missing" in status.output  # packaged cities without data


def test_data_fetch_usage_errors(local_env: Path):
    runner = CliRunner()
    assert runner.invoke(cli, ["data", "fetch"]).exit_code == 2
    unknown = runner.invoke(cli, ["data", "fetch", "atlantis"])
    assert unknown.exit_code == 2
    assert "unknown cities" in unknown.output


def test_route(client: TestClient):
    result = run(
        client,
        "route",
        "testville",
        "--from",
        A,
        "--from",
        "59.913,10.78",
        "--to",
        "59.91,10.78",
        "--at",
        "2026-10-22T08:00",
    )
    assert result.exit_code == 0, result.output
    assert "20 min, 0 changes, arrive 08:20" in result.output
    assert "bus 1 towards Stop D: Stop A → Stop D (3 stops)" in result.output
    assert "Fastest 5 min, everyone there after 20 min." in result.output
    assert "m to 59.91,10.78" in result.output  # the walk names the destination as given
    assert "Destination" not in result.output
