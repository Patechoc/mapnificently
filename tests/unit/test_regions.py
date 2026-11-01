"""Regions: data shared by cities, downloads shared by regions, status, prune and remove."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from mapnificently.api import create_app
from mapnificently.cli import cli
from mapnificently.providers import UnknownRegionError, load_cities
from mapnificently.settings import Settings, get_settings
from mapnificently.static_site import export_static_site
from mapnificently.timetable import CityDataStore
from tests.conftest import TEST_BBOX

BBOX = json.dumps(TEST_BBOX)


def cities_toml(feed1: Path, feed2: Path) -> str:
    """Region "valley" (two feeds) with two cities, and "solo", a city that is its own region
    and shares feed2 with valley."""
    return f"""
[regions.valley]
name = "Valley"
timezone = "Europe/Oslo"
bbox = {BBOX}
provider = "gtfs"
feeds = ["{feed1}", "{feed2}"]

[cities.testville]
name = "Testville"
country = "no"
region = "valley"
center = [59.91, 10.75]

[cities.otherville]
name = "Otherville"
region = "valley"
center = [59.91, 10.72]

[cities.solo]
name = "Solo"
timezone = "Europe/Oslo"
center = [59.91, 10.75]
bbox = {BBOX}
provider = "gtfs"
feeds = ["{feed2}"]
"""


@pytest.fixture
def env(tmp_path: Path, feed_zips, monkeypatch: pytest.MonkeyPatch) -> Settings:
    cities_file = tmp_path / "cities.toml"
    cities_file.write_text(cities_toml(*feed_zips))
    monkeypatch.setenv("MAPNIFICENTLY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MAPNIFICENTLY_CITIES_FILE", str(cities_file))
    get_settings.cache_clear()
    return get_settings()


def run(*args: str, input: str | None = None):  # noqa: A002
    return CliRunner().invoke(cli, list(args), input=input)


def zips(settings: Settings) -> list[Path]:
    return sorted(settings.feeds_root.rglob("*.zip"), key=lambda p: p.name)


class TestConfig:
    def test_cities_share_their_region(self, env: Settings):
        cities = load_cities(env)
        assert cities["testville"].region is cities["otherville"].region
        assert cities["testville"].feeds == cities["otherville"].feeds
        assert cities["solo"].region.id == "solo"  # its own region
        assert cities["moss"].region.id == cities["oslo"].region.id == "oslofjord"

    def test_country_is_per_city(self, env: Settings):
        cities = load_cities(env)
        assert cities["testville"].country == "NO"  # written "no": upper-cased
        assert cities["otherville"].country is None  # optional
        assert cities["paris"].country == "FR"

    def test_country_must_be_a_two_letter_code(self, tmp_path: Path):
        user = tmp_path / "c.toml"
        user.write_text(
            '[cities.x]\nname = "X"\ncountry = "Norway"\n'
            'region = "oslofjord"\ncenter = [59.9, 10.7]\n'
        )
        with pytest.raises(ValueError, match="country"):
            load_cities(Settings(cities_file=user))

    def test_unknown_region(self, tmp_path: Path):
        user = tmp_path / "c.toml"
        user.write_text('[cities.x]\nname = "X"\nregion = "nowhere"\ncenter = [0, 0]\n')
        with pytest.raises(UnknownRegionError, match="nowhere"):
            load_cities(Settings(cities_file=user))

    def test_city_cannot_override_region_feeds(self, tmp_path: Path):
        user = tmp_path / "c.toml"
        user.write_text(
            '[cities.x]\nname = "X"\nregion = "oslofjord"\ncenter = [59.9, 10.7]\nfeeds = ["rut"]\n'
        )
        with pytest.raises(ValueError, match="set feeds on the region"):
            load_cities(Settings(cities_file=user))

    def test_user_file_can_redefine_a_packaged_region(self, tmp_path: Path):
        user = tmp_path / "c.toml"
        user.write_text(
            '[regions.oslofjord]\nname = "Oslo only"\ntimezone = "Europe/Oslo"\n'
            'bbox = [10.4, 59.7, 11.2, 60.2]\nprovider = "entur"\nfeeds = ["rut"]\n'
        )
        cities = load_cities(Settings(cities_file=user))
        assert cities["oslo"].feeds == cities["moss"].feeds == ["rut"]


class TestSharing:
    def test_one_region_prepared_once_for_its_cities(self, env: Settings):
        result = run("data", "fetch", "testville", "otherville")
        assert result.exit_code == 0, result.output
        assert result.output.count("fetching") == 1
        assert "valley (testville, otherville): fetching" in result.output
        assert (env.region_dir("valley") / "prepared" / "meta.json").is_file()
        assert not env.region_dir("testville").exists()
        store = CityDataStore(env, load_cities(env))
        assert store.network("testville") is store.network("otherville")

    def test_downloads_shared_between_regions(self, env: Settings):
        assert run("data", "fetch", "valley", "solo").exit_code == 0
        assert [p.name for p in zips(env)] == ["feed1.zip", "feed2.zip"]  # feed2 once

    def test_api_reports_the_region(self, env: Settings):
        run("data", "fetch", "valley")
        with TestClient(create_app(env)) as client:
            cities = {c["id"]: c for c in client.get("/api/cities").json()}
        assert cities["otherville"]["region"] == "valley"
        assert cities["otherville"]["region_name"] == "Valley"
        assert cities["otherville"]["data_ready"] is True
        assert cities["testville"]["country"] == "NO"
        assert cities["otherville"]["country"] is None
        assert cities["solo"]["data_ready"] is False

    def test_static_export_writes_a_region_once(self, env: Settings, tmp_path: Path):
        run("data", "fetch", "valley")
        store = CityDataStore(env, load_cities(env))
        out = tmp_path / "site"
        summary = export_static_site(store, ["testville", "otherville"], [], out)
        assert list(summary["regions"]) == ["valley"]
        manifest = json.loads((out / "data" / "manifest.json").read_text())
        assert {c["id"]: c["country"] for c in manifest["cities"]} == {
            "testville": "NO",
            "otherville": None,
        }
        files = {c["id"]: c["files"]["network"] for c in manifest["cities"]}
        assert files == {
            "testville": "data/valley/network.bin",
            "otherville": "data/valley/network.bin",
        }


class TestStatus:
    def test_status_by_region(self, env: Settings):
        run("data", "fetch", "valley", "solo")
        old = env.data_dir / "oslo"  # the per-city layout of earlier versions
        old.mkdir()
        (old / "rut.zip").write_bytes(b"0" * 2500)
        status = run("data", "status")
        assert status.exit_code == 0, status.output
        rows = {line.split()[0]: line for line in status.output.splitlines() if line.strip()}
        assert "testville, otherville" in rows["valley"]
        assert "ready" in rows["valley"]
        assert "missing" in rows["oslofjord"]
        assert "downloads" in rows
        assert "2.5 KB" in rows["unused"]
        assert f"  {old}  2.5 KB" in status.output
        assert "data prune" in status.output

    def test_paths(self, env: Settings):
        run("data", "fetch", "valley", "solo")
        paths = run("data", "status", "--paths").output
        assert f"prepared data  {env.region_dir('valley')}" in paths
        assert "(shared with solo)" in paths
        assert "(shared with valley)" in paths
        assert "oslofjord (oslo, moss)" in paths
        assert "not yet" in paths


class TestCleanup:
    def test_prune(self, env: Settings):
        run("data", "fetch", "valley")
        old = env.data_dir / "moss"
        old.mkdir()
        (old / "x.zip").write_bytes(b"0" * 10)
        stray = env.feeds_root / "gtfs" / "0123456789ab" / "gone.zip"
        stray.parent.mkdir(parents=True)
        stray.write_bytes(b"0" * 10)
        (env.regions_root / "removed-region").mkdir()

        kept = run("data", "prune", input="n\n")
        assert "Nothing removed" in kept.output
        assert old.exists()

        pruned = run("data", "prune", "--yes")
        assert pruned.exit_code == 0, pruned.output
        assert not old.exists()
        assert not stray.parent.exists()  # its emptied folder too
        assert not (env.regions_root / "removed-region").exists()
        assert [p.name for p in zips(env)] == ["feed1.zip", "feed2.zip"]
        assert (env.region_dir("valley") / "prepared" / "meta.json").is_file()
        assert "Nothing to remove" in run("data", "prune", "--yes").output

    def test_remove_keeps_shared_downloads(self, env: Settings):
        run("data", "fetch", "valley", "solo")
        removed = run("data", "remove", "testville", "--yes")
        assert removed.exit_code == 0, removed.output
        assert "used by: testville, otherville" in removed.output
        assert not env.region_dir("valley").exists()
        assert [p.name for p in zips(env)] == ["feed2.zip"]  # feed1 was valley's only
        assert env.region_dir("solo").exists()
