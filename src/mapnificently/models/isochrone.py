"""Isochrone calculation models."""

from datetime import datetime, time
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class IsochroneConfig(BaseModel):
    """Configuration for isochrone calculation."""

    model_config = ConfigDict(frozen=True)

    # Starting point
    origin_lat: Annotated[float, Field(ge=-90, le=90)]
    origin_lon: Annotated[float, Field(ge=-180, le=180)]

    # Time parameters
    departure_time: time | datetime
    max_travel_time_seconds: Annotated[int, Field(ge=0, le=10800)] = 3600  # Max 3 hours

    # Walking parameters
    walking_speed_mps: Annotated[float, Field(gt=0, le=3.0)] = 1.4  # ~5 km/h
    max_walking_distance_m: Annotated[float, Field(ge=0, le=3000)] = 1000

    # Algorithm parameters
    transfer_penalty_seconds: Annotated[int, Field(ge=0)] = 120  # 2 min penalty per transfer
    max_transfers: Annotated[int, Field(ge=0, le=5)] = 3

    @property
    def origin_coordinates(self) -> tuple[float, float]:
        """Return (longitude, latitude) tuple for GeoJSON compatibility."""
        return (self.origin_lon, self.origin_lat)


class ReachableStop(BaseModel):
    """A stop that can be reached within the time limit."""

    model_config = ConfigDict(frozen=True)

    stop_id: str
    stop_name: str
    lat: Annotated[float, Field(ge=-90, le=90)]
    lon: Annotated[float, Field(ge=-180, le=180)]
    travel_time_seconds: Annotated[int, Field(ge=0)]
    num_transfers: Annotated[int, Field(ge=0)]

    @property
    def coordinates(self) -> tuple[float, float]:
        """Return (longitude, latitude) tuple for GeoJSON compatibility."""
        return (self.lon, self.lat)


class IsochroneResult(BaseModel):
    """Result of isochrone calculation."""

    model_config = ConfigDict(frozen=True)

    config: IsochroneConfig
    reachable_stops: list[ReachableStop]
    polygon_geojson: dict | None = None  # GeoJSON polygon of reachable area
    computation_time_ms: float | None = None

    @property
    def num_reachable_stops(self) -> int:
        """Number of stops reachable within time limit."""
        return len(self.reachable_stops)

    def get_stops_within_time(self, max_seconds: int) -> list[ReachableStop]:
        """Get stops reachable within a specific time."""
        return [s for s in self.reachable_stops if s.travel_time_seconds <= max_seconds]
