"""GTFS (General Transit Feed Specification) Pydantic models.

Reference: https://gtfs.org/schedule/reference/
"""

from datetime import date, time
from enum import IntEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class RouteType(IntEnum):
    """GTFS route types."""

    TRAM = 0
    SUBWAY = 1
    RAIL = 2
    BUS = 3
    FERRY = 4
    CABLE_TRAM = 5
    AERIAL_LIFT = 6
    FUNICULAR = 7
    TROLLEYBUS = 11
    MONORAIL = 12


class LocationType(IntEnum):
    """GTFS location types for stops."""

    STOP = 0
    STATION = 1
    ENTRANCE_EXIT = 2
    GENERIC_NODE = 3
    BOARDING_AREA = 4


class WheelchairAccessible(IntEnum):
    """Wheelchair accessibility information."""

    NO_INFO = 0
    ACCESSIBLE = 1
    NOT_ACCESSIBLE = 2


class PickupDropOffType(IntEnum):
    """Pickup and drop-off availability."""

    REGULAR = 0
    NONE = 1
    PHONE_AGENCY = 2
    COORDINATE_WITH_DRIVER = 3


class TransferType(IntEnum):
    """Transfer types between stops."""

    RECOMMENDED = 0
    TIMED = 1
    MIN_TIME_REQUIRED = 2
    NOT_POSSIBLE = 3


class ExceptionType(IntEnum):
    """Calendar date exception types."""

    SERVICE_ADDED = 1
    SERVICE_REMOVED = 2


class Agency(BaseModel):
    """Transit agency information (agency.txt)."""

    model_config = ConfigDict(frozen=True)

    agency_id: str
    agency_name: str
    agency_url: str
    agency_timezone: str
    agency_lang: str | None = None
    agency_phone: str | None = None
    agency_fare_url: str | None = None
    agency_email: str | None = None


class Stop(BaseModel):
    """Transit stop or station (stops.txt)."""

    model_config = ConfigDict(frozen=True)

    stop_id: str
    stop_name: str
    stop_lat: Annotated[float, Field(ge=-90, le=90)]
    stop_lon: Annotated[float, Field(ge=-180, le=180)]
    stop_code: str | None = None
    stop_desc: str | None = None
    zone_id: str | None = None
    stop_url: str | None = None
    location_type: LocationType = LocationType.STOP
    parent_station: str | None = None
    stop_timezone: str | None = None
    wheelchair_boarding: WheelchairAccessible = WheelchairAccessible.NO_INFO
    platform_code: str | None = None

    @property
    def coordinates(self) -> tuple[float, float]:
        """Return (longitude, latitude) tuple for GeoJSON compatibility."""
        return (self.stop_lon, self.stop_lat)


class Route(BaseModel):
    """Transit route (routes.txt)."""

    model_config = ConfigDict(frozen=True)

    route_id: str
    route_short_name: str | None = None
    route_long_name: str | None = None
    route_type: RouteType
    agency_id: str | None = None
    route_desc: str | None = None
    route_url: str | None = None
    route_color: str | None = None
    route_text_color: str | None = None
    route_sort_order: int | None = None


class Calendar(BaseModel):
    """Service availability by day of week (calendar.txt)."""

    model_config = ConfigDict(frozen=True)

    service_id: str
    monday: bool
    tuesday: bool
    wednesday: bool
    thursday: bool
    friday: bool
    saturday: bool
    sunday: bool
    start_date: date
    end_date: date

    def is_active_on_weekday(self, weekday: int) -> bool:
        """Check if service is active on a given weekday (0=Monday, 6=Sunday)."""
        days = [
            self.monday,
            self.tuesday,
            self.wednesday,
            self.thursday,
            self.friday,
            self.saturday,
            self.sunday,
        ]
        return days[weekday]


class CalendarDate(BaseModel):
    """Service exceptions for specific dates (calendar_dates.txt)."""

    model_config = ConfigDict(frozen=True)

    service_id: str
    date: date
    exception_type: ExceptionType


class Trip(BaseModel):
    """A sequence of stops that occur during a specific time (trips.txt)."""

    model_config = ConfigDict(frozen=True)

    trip_id: str
    route_id: str
    service_id: str
    trip_headsign: str | None = None
    trip_short_name: str | None = None
    direction_id: int | None = None
    block_id: str | None = None
    shape_id: str | None = None
    wheelchair_accessible: WheelchairAccessible = WheelchairAccessible.NO_INFO
    bikes_allowed: WheelchairAccessible = WheelchairAccessible.NO_INFO


class StopTime(BaseModel):
    """Times that a vehicle arrives/departs from stops (stop_times.txt)."""

    model_config = ConfigDict(frozen=True)

    trip_id: str
    stop_id: str
    stop_sequence: Annotated[int, Field(ge=0)]
    arrival_time: time
    departure_time: time
    stop_headsign: str | None = None
    pickup_type: PickupDropOffType = PickupDropOffType.REGULAR
    drop_off_type: PickupDropOffType = PickupDropOffType.REGULAR
    timepoint: bool = True

    @property
    def arrival_seconds(self) -> int:
        """Get arrival time as seconds since midnight."""
        return (
            self.arrival_time.hour * 3600
            + self.arrival_time.minute * 60
            + self.arrival_time.second
        )

    @property
    def departure_seconds(self) -> int:
        """Get departure time as seconds since midnight."""
        return (
            self.departure_time.hour * 3600
            + self.departure_time.minute * 60
            + self.departure_time.second
        )


class Transfer(BaseModel):
    """Transfer rules between stops (transfers.txt)."""

    model_config = ConfigDict(frozen=True)

    from_stop_id: str
    to_stop_id: str
    transfer_type: TransferType = TransferType.RECOMMENDED
    min_transfer_time: Annotated[int, Field(ge=0)] | None = None
