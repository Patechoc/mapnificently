"""Pytest fixtures for Mapnificently tests."""

from datetime import date, time

import pytest

from mapnificently.models import (
    Agency,
    Calendar,
    IsochroneConfig,
    Route,
    Stop,
    StopTime,
    Trip,
)
from mapnificently.models.gtfs import LocationType, RouteType


@pytest.fixture
def sample_agency() -> Agency:
    """Create a sample transit agency."""
    return Agency(
        agency_id="OSL",
        agency_name="Oslo Public Transit",
        agency_url="https://ruter.no",
        agency_timezone="Europe/Oslo",
        agency_lang="no",
    )


@pytest.fixture
def sample_stops() -> list[Stop]:
    """Create sample stops for testing."""
    return [
        Stop(
            stop_id="stop_1",
            stop_name="Central Station",
            stop_lat=59.9110,
            stop_lon=10.7528,
            location_type=LocationType.STATION,
        ),
        Stop(
            stop_id="stop_2",
            stop_name="City Hall",
            stop_lat=59.9127,
            stop_lon=10.7347,
        ),
        Stop(
            stop_id="stop_3",
            stop_name="University",
            stop_lat=59.9403,
            stop_lon=10.7215,
        ),
    ]


@pytest.fixture
def sample_route() -> Route:
    """Create a sample transit route."""
    return Route(
        route_id="route_1",
        route_short_name="1",
        route_long_name="Central Line",
        route_type=RouteType.SUBWAY,
        agency_id="OSL",
        route_color="FF0000",
    )


@pytest.fixture
def sample_calendar() -> Calendar:
    """Create a sample service calendar."""
    return Calendar(
        service_id="weekday",
        monday=True,
        tuesday=True,
        wednesday=True,
        thursday=True,
        friday=True,
        saturday=False,
        sunday=False,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 31),
    )


@pytest.fixture
def sample_trip(sample_route: Route, sample_calendar: Calendar) -> Trip:
    """Create a sample trip."""
    return Trip(
        trip_id="trip_1",
        route_id=sample_route.route_id,
        service_id=sample_calendar.service_id,
        trip_headsign="University",
        direction_id=0,
    )


@pytest.fixture
def sample_stop_times(sample_trip: Trip, sample_stops: list[Stop]) -> list[StopTime]:
    """Create sample stop times for a trip."""
    return [
        StopTime(
            trip_id=sample_trip.trip_id,
            stop_id=sample_stops[0].stop_id,
            stop_sequence=0,
            arrival_time=time(8, 0, 0),
            departure_time=time(8, 0, 0),
        ),
        StopTime(
            trip_id=sample_trip.trip_id,
            stop_id=sample_stops[1].stop_id,
            stop_sequence=1,
            arrival_time=time(8, 5, 0),
            departure_time=time(8, 5, 30),
        ),
        StopTime(
            trip_id=sample_trip.trip_id,
            stop_id=sample_stops[2].stop_id,
            stop_sequence=2,
            arrival_time=time(8, 15, 0),
            departure_time=time(8, 15, 0),
        ),
    ]


@pytest.fixture
def sample_isochrone_config() -> IsochroneConfig:
    """Create a sample isochrone configuration."""
    return IsochroneConfig(
        origin_lat=59.9110,
        origin_lon=10.7528,
        departure_time=time(8, 0, 0),
        max_travel_time_seconds=1800,  # 30 minutes
        walking_speed_mps=1.4,
        max_walking_distance_m=500,
    )
