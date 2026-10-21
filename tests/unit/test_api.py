"""HTTP API."""

import httpx
from fastapi.testclient import TestClient
from shapely.geometry import shape

from mapnificently.api import create_app
from mapnificently.settings import Settings
from mapnificently.timetable import CityDataStore
from tests.conftest import COORDS

A = {"lat": COORDS["A"][0], "lon": COORDS["A"][1]}
D = {"lat": COORDS["D"][0], "lon": COORDS["D"][1]}


def test_list_cities(client: TestClient):
    cities = {c["id"]: c for c in client.get("/api/cities").json()}
    assert cities["testville"]["data_ready"] is True
    assert cities["nodata"]["data_ready"] is False
    assert cities["testville"]["bbox"]["lon_min"] == 10.70


def test_get_city_and_unknown(client: TestClient):
    assert client.get("/api/cities/testville").json()["name"] == "Testville"
    response = client.get("/api/cities/atlantis")
    assert response.status_code == 404
    assert "atlantis" in response.json()["detail"]


def test_status(client: TestClient):
    client.post(
        "/api/cities/testville/reach", json={"origins": [A], "departure": "2026-10-22T08:00"}
    )
    status = client.get("/api/cities/testville/status").json()
    assert status["data_ready"] is True
    assert status["num_stops"] == len(COORDS)
    assert status["service_start"] == "2026-01-01"
    assert "2026-10-22" in status["cached_dates"]
    assert [f["name"] for f in status["feeds"]] == ["feed1", "feed2"]
    assert client.get("/api/cities/nodata/status").json()["data_ready"] is False


def test_stops(client: TestClient):
    stops = client.get("/api/cities/testville/stops").json()
    assert len(stops["ids"]) == len(stops["lat"]) == len(stops["names"]) == len(COORDS)
    i = stops["ids"].index("A")
    assert (stops["lat"][i], stops["lon"][i]) == COORDS["A"]


def test_reach(client: TestClient):
    response = client.post(
        "/api/cities/testville/reach",
        json={"origins": [A, D], "departure": "2026-10-22T08:00", "max_minutes": 30},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["departure"] == "2026-10-22T08:00:00+02:00"
    assert body["service_date"] == "2026-10-22"
    assert body["walk_detour"] > 1
    stops = client.get("/api/cities/testville/stops").json()
    from_a = dict(zip(body["origins"][0]["stop_idx"], body["origins"][0]["arrival_s"], strict=True))
    assert from_a[stops["ids"].index("D")] == 1200
    assert body["origins"][0]["arrival_s"] == sorted(body["origins"][0]["arrival_s"])
    assert len(body["origins"]) == 2


def test_isochrone_union_and_intersection(client: TestClient):
    def area(mode: str) -> float:
        fc = client.post(
            "/api/cities/testville/isochrone",
            json={"origins": [A, D], "departure": "2026-10-22T08:00", "minutes": 25, "mode": mode},
        ).json()
        assert fc["type"] == "FeatureCollection"
        props = fc["features"][0]["properties"]
        assert props["mode"] == mode
        assert shape(fc["features"][0]["geometry"]).is_valid
        return props["area_km2"]

    assert area("union") > area("intersection") > 0


def test_validation_and_missing_data(client: TestClient):
    bad = client.post("/api/cities/testville/reach", json={"origins": [A], "max_minutes": 999})
    assert bad.status_code == 422
    missing = client.post("/api/cities/nodata/reach", json={"origins": [A]})
    assert missing.status_code == 409
    assert "data fetch nodata" in missing.json()["detail"]
    assert client.get("/api/cities/nodata/stops").status_code == 409


def test_refresh_runs_in_background(client: TestClient):
    response = client.post("/api/cities/testville/refresh")
    assert response.status_code == 202
    assert client.get("/api/cities/testville/status").json()["data_ready"] is True


def test_geocode(settings: Settings, test_city, store: CityDataStore):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "features": [
                    {
                        "geometry": {"coordinates": [10.72, 59.91]},
                        "properties": {"name": "Stop A"},
                    }
                ]
            },
        )

    store.http_client = httpx.Client(transport=httpx.MockTransport(handler))
    store._providers.clear()
    with TestClient(create_app(settings, store)) as client:
        hits = client.get("/api/cities/testville/geocode", params={"q": "stop"}).json()
        assert hits[0]["name"] == "Stop A"
        assert client.get("/api/cities/testville/geocode", params={"q": "x"}).status_code == 422

    store._providers.clear()
    store.http_client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500)))
    with TestClient(create_app(settings, store)) as client:
        assert client.get("/api/cities/testville/geocode", params={"q": "stop"}).status_code == 502


def test_web_ui_served(client: TestClient):
    index = client.get("/")
    assert index.status_code == 200
    assert "Mapnificently" in index.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/openapi.json").json()["info"]["title"] == "Mapnificently"
