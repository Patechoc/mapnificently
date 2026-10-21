"""Reachable-area polygons."""

import math

import pytest
from shapely.geometry import shape

from mapnificently.geo import LocalProjection
from mapnificently.isochrone import combine, reach_polygon, to_feature_collection
from mapnificently.models import LatLon
from mapnificently.timetable import WALK_DETOUR

PROJ = LocalProjection(59.91, 10.75)
ORIGIN = LatLon(lat=59.91, lon=10.75)


def test_projection_round_trip():
    x, y = PROJ.forward(59.92, 10.77)
    lat, lon = PROJ.inverse(x, y)
    assert lat == pytest.approx(59.92)
    assert lon == pytest.approx(10.77)
    assert float(y) == pytest.approx(1112, rel=0.01)  # 0.01° of latitude


def test_walking_only_circle():
    area = reach_polygon(PROJ, ORIGIN, [], [], [], minutes=10, walk_speed_mps=1.25, max_walk_m=1000)
    radius = 10 * 60 * 1.25 / WALK_DETOUR
    assert area.area == pytest.approx(math.pi * radius**2, rel=0.02)


def test_walk_capped_by_max_walk():
    area = reach_polygon(PROJ, ORIGIN, [], [], [], minutes=60, walk_speed_mps=1.25, max_walk_m=500)
    radius = 500 / WALK_DETOUR
    assert area.area == pytest.approx(math.pi * radius**2, rel=0.02)


def test_stops_reached_too_late_are_ignored():
    far = (59.95, 10.75)
    late = reach_polygon(PROJ, ORIGIN, [far[0]], [far[1]], [700], 10, 1.25, 1000)
    alone = reach_polygon(PROJ, ORIGIN, [], [], [], 10, 1.25, 1000)
    assert late.area == pytest.approx(alone.area)
    early = reach_polygon(PROJ, ORIGIN, [far[0]], [far[1]], [60], 10, 1.25, 1000)
    assert early.area > 1.5 * alone.area


def test_union_contains_intersection():
    a = reach_polygon(PROJ, ORIGIN, [], [], [], 10, 1.25, 1000)
    b = reach_polygon(PROJ, LatLon(lat=59.91, lon=10.76), [], [], [], 10, 1.25, 1000)
    union = combine([a, b], "union")
    intersection = combine([a, b], "intersection")
    assert union.area > a.area
    assert 0 < intersection.area < a.area
    assert union.contains(intersection)


def test_feature_collection_is_valid_geojson():
    area = reach_polygon(PROJ, ORIGIN, [], [], [], 10, 1.25, 1000)
    fc = to_feature_collection(PROJ, area, {"minutes": 10})
    feature = fc["features"][0]
    geometry = shape(feature["geometry"])
    assert geometry.is_valid
    lon, lat = geometry.centroid.x, geometry.centroid.y
    assert lat == pytest.approx(59.91, abs=1e-3)
    assert lon == pytest.approx(10.75, abs=1e-3)
    assert feature["properties"] == {"minutes": 10}


def test_empty_area_gives_no_features():
    a = reach_polygon(PROJ, ORIGIN, [], [], [], 1, 1.25, 1000)
    b = reach_polygon(PROJ, LatLon(lat=59.95, lon=10.75), [], [], [], 1, 1.25, 1000)
    fc = to_feature_collection(PROJ, combine([a, b], "intersection"), {})
    assert fc == {"type": "FeatureCollection", "features": []}
