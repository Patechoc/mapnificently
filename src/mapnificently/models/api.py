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
MinChange = Annotated[
    int,
    Field(ge=0, le=15, description="Shortest time, in minutes, to change between vehicles."),
]
Mode = Literal[
    "walk", "bus", "coach", "tram", "metro", "rail", "ferry", "cable car", "funicular", "other"
]

MAX_MINUTES = 220  # longest travel time accepted; the web map reads it from /api/config
DEFAULT_WALK_SPEED_MPS = 1.25  # 4.5 km/h
DEFAULT_MAX_WALK_M = 1000.0
DEFAULT_MIN_CHANGE_MIN = 2


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
    min_change_min: MinChange = DEFAULT_MIN_CHANGE_MIN


class ReachRequest(TripRequest):
    """Earliest arrival at every stop reachable within ``max_minutes``."""

    max_minutes: Annotated[int, Field(ge=1, le=MAX_MINUTES)] = 60


class IsochroneRequest(TripRequest):
    """Area reachable within ``minutes``, as a GeoJSON polygon."""

    minutes: Annotated[int, Field(ge=1, le=MAX_MINUTES)] = 30
    mode: Literal["union", "intersection"] = "union"


class RouteRequest(TripRequest):
    """Fastest journey from each origin to ``destination``."""

    destination: LatLon
    max_minutes: Annotated[int, Field(ge=1, le=MAX_MINUTES)] = MAX_MINUTES


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
    min_change_min: int
    walk_detour: float = Field(description="Walking distance / straight-line distance.")
    origins: list[OriginReach]
    computation_ms: float


class JourneyLeg(BaseModel):
    """One walk or one ride."""

    mode: Mode
    line: str | None = Field(default=None, description='Line name, e.g. "R21" or "20".')
    headsign: str | None = Field(default=None, description="Where the vehicle is heading.")
    from_name: str
    to_name: str
    depart: datetime
    arrive: datetime
    wait_s: int = Field(default=0, description="Waiting at the start of this leg.")
    distance_m: int | None = Field(default=None, description="Walking distance in metres.")
    num_stops: int | None = Field(default=None, description="Stops travelled on a ride.")
    path: list[LatLon] = Field(description="Points to draw: stops on a ride, ends of a walk.")


class Journey(BaseModel):
    """Fastest journey from one origin, or ``reachable=False``."""

    origin: LatLon
    reachable: bool
    depart: datetime
    arrive: datetime | None = None
    duration_s: int | None = None
    num_changes: int = 0
    legs: list[JourneyLeg] = []


class RouteResponse(BaseModel):
    """Result of a route query: one journey per origin, in request order."""

    city: str
    departure: datetime
    destination: LatLon
    min_change_min: int
    journeys: list[Journey]
    fastest_s: int | None = Field(description="Duration of the quickest journey.")
    everyone_s: int | None = Field(
        description="When all origins have arrived; null if one cannot get there."
    )
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


class AppConfig(BaseModel):
    """Limits the web map needs to know."""

    max_minutes: int = Field(description="Longest travel time a query may ask for.")


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
