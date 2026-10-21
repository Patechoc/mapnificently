"""Request and response schemas of the HTTP API."""

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from mapnificently.models.city import BBox, LatLon

WalkSpeed = Annotated[float, Field(gt=0.3, le=3.0, description="Walking speed in m/s.")]
MaxWalk = Annotated[
    float,
    Field(ge=0, le=5000, description="Longest walk, in metres, to or from a stop."),
]
Origins = Annotated[list[LatLon], Field(min_length=1, max_length=10)]

MAX_MINUTES = 180  # longest travel time accepted; keep SLIDER_MAX in web/app.js in step
DEFAULT_WALK_SPEED_MPS = 1.25  # 4.5 km/h
DEFAULT_MAX_WALK_M = 1000.0


class TripRequest(BaseModel):
    """What every reachability query has in common."""

    model_config = ConfigDict(frozen=True)

    origins: Origins
    departure: datetime | None = Field(
        default=None,
        description="Departure time. Without timezone it is read in the city's timezone. "
        "Defaults to now.",
    )
    walk_speed_mps: WalkSpeed = DEFAULT_WALK_SPEED_MPS
    max_walk_m: MaxWalk = DEFAULT_MAX_WALK_M


class ReachRequest(TripRequest):
    """Earliest arrival at every stop reachable within ``max_minutes``."""

    max_minutes: Annotated[int, Field(ge=1, le=MAX_MINUTES)] = 60


class IsochroneRequest(TripRequest):
    """Area reachable within ``minutes``, as a GeoJSON polygon."""

    minutes: Annotated[int, Field(ge=1, le=MAX_MINUTES)] = 30
    mode: Literal["union", "intersection"] = "union"


class OriginReach(BaseModel):
    """Stops reached from one origin, as parallel arrays to keep responses small."""

    origin: LatLon
    stop_idx: list[int] = Field(description="Index into the city's stop list.")
    arrival_s: list[int] = Field(description="Seconds after departure.")


class ReachResponse(BaseModel):
    """Result of a reach query."""

    city: str
    departure: datetime
    service_date: date
    max_minutes: int
    walk_speed_mps: float
    max_walk_m: float
    walk_detour: float = Field(description="Walking distance / straight-line distance.")
    origins: list[OriginReach]
    computation_ms: float


class StopsResponse(BaseModel):
    """All stops of a city as parallel arrays; reach results index into them."""

    city: str
    ids: list[str]
    names: list[str]
    lat: list[float]
    lon: list[float]


class Place(BaseModel):
    """A geocoder hit."""

    name: str
    label: str
    lat: float
    lon: float
    kind: str = ""


class CityInfo(BaseModel):
    """A city as listed by the API."""

    id: str
    name: str
    timezone: str
    center: LatLon
    bbox: BBox
    provider: str
    feeds: list[str]
    attribution: str
    data_ready: bool


class FeedStatus(BaseModel):
    """One downloaded feed."""

    name: str
    source: str
    size_bytes: int
    last_modified: str | None = None


class CityStatus(BaseModel):
    """Data state of a city."""

    city: str
    data_ready: bool
    feeds: list[FeedStatus] = []
    prepared_at: datetime | None = None
    num_stops: int = 0
    num_trips: int = 0
    service_start: date | None = None
    service_end: date | None = None
    cached_dates: list[date] = []
