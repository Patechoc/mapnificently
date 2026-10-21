"""Models, settings, the city registry and providers."""

import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from mapnificently.models import BBox, CityConfig, ReachRequest
from mapnificently.providers import (
    EnturProvider,
    GtfsUrlProvider,
    ProviderError,
    UnknownProviderError,
    get_provider,
    load_cities,
)
from mapnificently.settings import Settings


class TestModels:
    def test_bbox_order(self):
        with pytest.raises(ValidationError):
            BBox.from_list([11, 60, 10, 59])
        with pytest.raises(ValueError, match="4 numbers"):
            BBox.from_list([1, 2, 3])
        assert BBox.from_list([10, 59, 11, 60]).contains(59.5, 10.5)

    def test_city_from_lists(self, test_city: CityConfig):
        assert test_city.center.lat == 59.91
        assert test_city.bbox.lon_min == 10.70
        assert str(test_city.tz) == "Europe/Oslo"

    def test_city_rejects_bad_timezone(self):
        with pytest.raises(ValidationError, match="timezone"):
            CityConfig(
                id="x",
                name="X",
                timezone="Mars/Olympus",
                center=[0, 0],
                bbox=[0, 0, 1, 1],
                provider="gtfs",
                feeds=["a.zip"],
            )

    def test_city_id_pattern(self):
        with pytest.raises(ValidationError):
            CityConfig(
                id="Bad City",
                name="X",
                timezone="UTC",
                center=[0, 0],
                bbox=[0, 0, 1, 1],
                provider="gtfs",
                feeds=["a.zip"],
            )

    def test_request_limits(self):
        with pytest.raises(ValidationError):
            ReachRequest(origins=[], max_minutes=10)
        with pytest.raises(ValidationError):
            ReachRequest(origins=[{"lat": 59.9, "lon": 10.7}], max_minutes=500)


class TestRegistry:
    def test_packaged_cities(self, settings: Settings):
        cities = load_cities(settings)
        assert {"oslo", "bergen", "trondheim", "moss"} <= set(cities)
        assert cities["moss"].region.id == cities["oslo"].region.id == "oslofjord"
        assert {"rut", "vyg", "ost", "flt"} <= set(cities["moss"].feeds)
        assert all(c.provider == "entur" for c in cities.values())

    def test_user_file_overrides_and_extends(self, tmp_path: Path):
        user_file = tmp_path / "cities.toml"
        user_file.write_text(
            '[cities.oslo]\nname = "Oslo (custom)"\ntimezone = "Europe/Oslo"\n'
            "center = [59.9, 10.7]\nbbox = [10.5, 59.8, 10.9, 60.0]\n"
            'provider = "entur"\nfeeds = ["rut"]\n\n'
            '[cities.helsinki]\nname = "Helsinki"\ntimezone = "Europe/Helsinki"\n'
            "center = [60.17, 24.94]\nbbox = [24.5, 60.05, 25.3, 60.4]\n"
            'provider = "gtfs"\nfeeds = ["https://example.org/hsl.zip"]\n'
        )
        cities = load_cities(Settings(cities_file=user_file))
        assert cities["oslo"].name == "Oslo (custom)"
        assert cities["oslo"].feeds == ["rut"]
        assert cities["helsinki"].provider == "gtfs"
        assert "bergen" in cities

    def test_unknown_provider(self, tmp_path: Path, settings: Settings):
        user_file = tmp_path / "cities.toml"
        user_file.write_text(
            '[cities.x]\nname = "X"\ntimezone = "UTC"\ncenter = [0, 0]\n'
            'bbox = [0, 0, 1, 1]\nprovider = "nope"\nfeeds = ["a"]\n'
        )
        with pytest.raises(UnknownProviderError, match="nope"):
            load_cities(Settings(cities_file=user_file))
        with pytest.raises(UnknownProviderError):
            get_provider("nope", settings)

    def test_get_provider(self, settings: Settings):
        assert isinstance(get_provider("entur", settings), EnturProvider)
        assert type(get_provider("gtfs", settings)) is GtfsUrlProvider


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


class TestGtfsUrlProvider:
    def test_download_then_not_modified(self, tmp_path: Path, settings: Settings, test_city):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if request.headers.get("If-None-Match") == '"v1"':
                return httpx.Response(304)
            return httpx.Response(200, content=b"zipdata", headers={"ETag": '"v1"'})

        city = test_city.model_copy(update={"feeds": ["https://example.org/feeds/city.zip"]})
        provider = GtfsUrlProvider(settings, _mock_client(handler))
        [path] = provider.fetch_feeds(city, tmp_path)
        assert path.name == "city.zip"
        assert path.read_bytes() == b"zipdata"
        assert json.loads(path.with_suffix(".json").read_text())["etag"] == '"v1"'

        provider.fetch_feeds(city, tmp_path)
        assert seen[-1].headers["If-None-Match"] == '"v1"'
        assert path.read_bytes() == b"zipdata"

        provider.fetch_feeds(city, tmp_path, force=True)
        assert "If-None-Match" not in seen[-1].headers

    def test_http_error(self, tmp_path: Path, settings: Settings, test_city):
        city = test_city.model_copy(update={"feeds": ["https://example.org/x.zip"]})
        provider = GtfsUrlProvider(settings, _mock_client(lambda _: httpx.Response(500)))
        with pytest.raises(ProviderError, match="could not download"):
            provider.fetch_feeds(city, tmp_path)
        assert not (tmp_path / "x.part").exists()

    def test_missing_local_file(self, tmp_path: Path, settings: Settings, test_city):
        city = test_city.model_copy(update={"feeds": [f"file://{tmp_path}/none.zip"]})
        with pytest.raises(ProviderError, match="not found"):
            GtfsUrlProvider(settings).fetch_feeds(city, tmp_path / "out")

    def test_photon_geocode(self, settings: Settings, test_city):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["bbox"] == "10.7,59.89,10.8,59.95"
            return httpx.Response(
                200,
                json={
                    "features": [
                        {
                            "geometry": {"coordinates": [10.75, 59.91]},
                            "properties": {
                                "name": "Torget",
                                "city": "Testville",
                                "osm_value": "square",
                            },
                        }
                    ]
                },
            )

        [place] = GtfsUrlProvider(settings, _mock_client(handler)).geocode(test_city, "torg")
        assert place.label == "Torget, Testville"
        assert (place.lat, place.lon, place.kind) == (59.91, 10.75, "square")


class TestEnturProvider:
    def test_feed_urls_and_client_name(self, settings: Settings):
        city = load_cities(settings)["oslo"]
        provider = EnturProvider(settings)
        names = [name for name, _ in provider.feed_sources(city)]
        assert names[:2] == ["rut", "vyg"]
        assert provider.feed_sources(city)[0][1].endswith("/rb_rut-aggregated-gtfs.zip")
        assert provider.client.headers["ET-Client-Name"] == "mapnificently-dev"

    def test_geocode(self, settings: Settings):
        city = load_cities(settings)["moss"]

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["boundary.rect.min_lat"] == str(city.bbox.lat_min)
            return httpx.Response(
                200,
                json={
                    "features": [
                        {
                            "geometry": {"coordinates": [10.657, 59.432]},
                            "properties": {
                                "name": "Moss stasjon",
                                "label": "Moss stasjon, Moss",
                                "layer": "venue",
                            },
                        }
                    ]
                },
            )

        [place] = EnturProvider(settings, _mock_client(handler)).geocode(city, "Moss stasjon")
        assert place.label == "Moss stasjon, Moss"
        assert place.kind == "venue"

    def test_geocode_error(self, settings: Settings):
        city = load_cities(settings)["moss"]
        provider = EnturProvider(settings, _mock_client(lambda _: httpx.Response(503)))
        with pytest.raises(ProviderError, match="geocoding failed"):
            provider.geocode(city, "x")


def test_tests_ignore_mapnificently_variables_from_the_environment():
    """CI sets MAPNIFICENTLY_ENTUR_CLIENT_NAME for its integration job; unit tests must still
    see the defaults (conftest clears these variables). Regression: CI failed when it was set."""
    import os  # noqa: PLC0415

    assert not [name for name in os.environ if name.startswith("MAPNIFICENTLY_")]
    assert Settings().entur_client_name == "mapnificently-dev"
