"""Reachable areas as polygons, the way Mapnificent draws them.

Around every stop reached after ``t`` seconds we can keep walking for ``T - t`` seconds, so
the stop gets a circle of that walking radius (capped by the longest walk allowed). The
union of the circles, plus one around the start point, is the area reachable in ``T``.
"""

from collections.abc import Sequence
from typing import Any, Literal

import numpy as np
import shapely
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry

from mapnificently.geo import LocalProjection
from mapnificently.models import LatLon
from mapnificently.timetable import WALK_DETOUR

QUAD_SEGMENTS = 8
SIMPLIFY_M = 5.0


def reach_polygon(
    projection: LocalProjection,
    origin: LatLon,
    stop_lat: Sequence[float],
    stop_lon: Sequence[float],
    arrival_s: Sequence[int],
    minutes: int,
    walk_speed_mps: float,
    max_walk_m: float,
) -> BaseGeometry:
    """Area (in projected metres) reachable from one origin in ``minutes``."""
    budget = minutes * 60
    remaining = budget - np.asarray(arrival_s, dtype=np.float64)
    keep = remaining > 0
    lat = np.concatenate([[origin.lat], np.asarray(stop_lat, dtype=np.float64)[keep]])
    lon = np.concatenate([[origin.lon], np.asarray(stop_lon, dtype=np.float64)[keep]])
    remaining = np.concatenate([[budget], remaining[keep]])
    radii = np.minimum(remaining * walk_speed_mps, max_walk_m) / WALK_DETOUR
    x, y = projection.forward(lat, lon)
    circles = shapely.buffer(shapely.points(x, y), radii, quad_segs=QUAD_SEGMENTS)
    return shapely.union_all(circles)


def combine(areas: Sequence[BaseGeometry], mode: Literal["union", "intersection"]) -> BaseGeometry:
    """Several origins together: anywhere one can reach, or only where all can."""
    if mode == "union":
        return shapely.union_all(areas)
    return shapely.intersection_all(areas)


def to_feature_collection(
    projection: LocalProjection, area: BaseGeometry, properties: dict[str, Any]
) -> dict[str, Any]:
    """Project an area back to WGS84 and wrap it as a GeoJSON FeatureCollection."""
    features = []
    if not area.is_empty:
        simplified = area.simplify(SIMPLIFY_M)

        def to_lonlat(coords: np.ndarray) -> np.ndarray:
            lat, lon = projection.inverse(coords[:, 0], coords[:, 1])
            return np.round(np.column_stack([lon, lat]), 6)

        geometry = shapely.transform(simplified, to_lonlat)
        features.append(
            {"type": "Feature", "properties": properties, "geometry": mapping(geometry)}
        )
    return {"type": "FeatureCollection", "features": features}
