"""Unit tests for Pydantic models."""

from datetime import date, time

import pytest
from pydantic import ValidationError

from mapnificently.models import (
    Agency,
    Calendar,
    CalendarDate,
    IsochroneConfig,
    IsochroneResult,
    ReachableStop,
    Route,
    Stop,
    StopTime,
    Transfer,
)
from mapnificently.models.gtfs import (
    ExceptionType,
    RouteType,
    TransferType,
)


class TestStop:
    """Tests for Stop model."""

    def test_create_valid_stop(self, sample_stops: list[Stop]) -> None:
        """Test creating a valid stop."""
        stop = sample_stops[0]
        assert stop.stop_id == "stop_1"
        assert stop.stop_name == "Central Station"
        assert stop.stop_lat == 59.9110
        assert stop.stop_lon == 10.7528

    def test_stop_coordinates_property(self, sample_stops: list[Stop]) -> None:
        """Test coordinates property returns (lon, lat) for GeoJSON."""
        stop = sample_stops[0]
        assert stop.coordinates == (10.7528, 59.9110)

    def test_invalid_latitude(self) -> None:
        """Test that invalid latitude raises validation error."""
        with pytest.raises(ValidationError):
            Stop(
                stop_id="invalid",
                stop_name="Invalid",
                stop_lat=91.0,  # Invalid: > 90
                stop_lon=10.0,
            )

    def test_invalid_longitude(self) -> None:
        """Test that invalid longitude raises validation error."""
        with pytest.raises(ValidationError):
            Stop(
                stop_id="invalid",
                stop_name="Invalid",
                stop_lat=59.0,
                stop_lon=181.0,  # Invalid: > 180
            )


class TestRoute:
    """Tests for Route model."""

    def test_create_valid_route(self, sample_route: Route) -> None:
        """Test creating a valid route."""
        assert sample_route.route_id == "route_1"
        assert sample_route.route_type == RouteType.SUBWAY

    def test_route_types(self) -> None:
        """Test different route types."""
        bus_route = Route(
            route_id="bus_1",
            route_short_name="100",
            route_type=RouteType.BUS,
        )
        assert bus_route.route_type == RouteType.BUS
        assert bus_route.route_type.value == 3


class TestCalendar:
    """Tests for Calendar model."""

    def test_is_active_on_weekday(self, sample_calendar: Calendar) -> None:
        """Test checking service activity by weekday."""
        # Monday = 0, Saturday = 5, Sunday = 6
        assert sample_calendar.is_active_on_weekday(0) is True  # Monday
        assert sample_calendar.is_active_on_weekday(4) is True  # Friday
        assert sample_calendar.is_active_on_weekday(5) is False  # Saturday
        assert sample_calendar.is_active_on_weekday(6) is False  # Sunday

    def test_calendar_date_range(self, sample_calendar: Calendar) -> None:
        """Test calendar date range."""
        assert sample_calendar.start_date == date(2024, 1, 1)
        assert sample_calendar.end_date == date(2024, 12, 31)


class TestCalendarDate:
    """Tests for CalendarDate model."""

    def test_service_added(self) -> None:
        """Test calendar date with service added."""
        exception = CalendarDate(
            service_id="weekday",
            date=date(2024, 12, 25),
            exception_type=ExceptionType.SERVICE_REMOVED,
        )
        assert exception.exception_type == ExceptionType.SERVICE_REMOVED

    def test_service_removed(self) -> None:
        """Test calendar date with service removed."""
        exception = CalendarDate(
            service_id="weekend",
            date=date(2024, 5, 1),
            exception_type=ExceptionType.SERVICE_ADDED,
        )
        assert exception.exception_type == ExceptionType.SERVICE_ADDED


class TestStopTime:
    """Tests for StopTime model."""

    def test_arrival_seconds(self, sample_stop_times: list[StopTime]) -> None:
        """Test arrival time conversion to seconds."""
        stop_time = sample_stop_times[0]
        # 8:00:00 = 8 * 3600 = 28800 seconds
        assert stop_time.arrival_seconds == 28800

    def test_departure_seconds(self, sample_stop_times: list[StopTime]) -> None:
        """Test departure time conversion to seconds."""
        stop_time = sample_stop_times[1]
        # 8:05:30 = 8*3600 + 5*60 + 30 = 29130 seconds
        assert stop_time.departure_seconds == 29130


class TestTransfer:
    """Tests for Transfer model."""

    def test_create_transfer(self) -> None:
        """Test creating a transfer between stops."""
        transfer = Transfer(
            from_stop_id="stop_1",
            to_stop_id="stop_2",
            transfer_type=TransferType.MIN_TIME_REQUIRED,
            min_transfer_time=180,  # 3 minutes
        )
        assert transfer.transfer_type == TransferType.MIN_TIME_REQUIRED
        assert transfer.min_transfer_time == 180


class TestIsochroneConfig:
    """Tests for IsochroneConfig model."""

    def test_create_valid_config(self, sample_isochrone_config: IsochroneConfig) -> None:
        """Test creating a valid isochrone config."""
        assert sample_isochrone_config.origin_lat == 59.9110
        assert sample_isochrone_config.max_travel_time_seconds == 1800

    def test_origin_coordinates_property(
        self, sample_isochrone_config: IsochroneConfig,
    ) -> None:
        """Test origin coordinates property."""
        assert sample_isochrone_config.origin_coordinates == (10.7528, 59.9110)

    def test_max_travel_time_limit(self) -> None:
        """Test that travel time cannot exceed 3 hours."""
        with pytest.raises(ValidationError):
            IsochroneConfig(
                origin_lat=59.9,
                origin_lon=10.7,
                departure_time=time(8, 0),
                max_travel_time_seconds=14400,  # 4 hours, exceeds limit
            )

    def test_walking_speed_bounds(self) -> None:
        """Test walking speed validation."""
        with pytest.raises(ValidationError):
            IsochroneConfig(
                origin_lat=59.9,
                origin_lon=10.7,
                departure_time=time(8, 0),
                walking_speed_mps=5.0,  # Too fast
            )


class TestReachableStop:
    """Tests for ReachableStop model."""

    def test_create_reachable_stop(self) -> None:
        """Test creating a reachable stop."""
        stop = ReachableStop(
            stop_id="stop_1",
            stop_name="Central Station",
            lat=59.9110,
            lon=10.7528,
            travel_time_seconds=600,
            num_transfers=0,
        )
        assert stop.travel_time_seconds == 600
        assert stop.coordinates == (10.7528, 59.9110)


class TestIsochroneResult:
    """Tests for IsochroneResult model."""

    def test_num_reachable_stops(
        self, sample_isochrone_config: IsochroneConfig,
    ) -> None:
        """Test counting reachable stops."""
        stops = [
            ReachableStop(
                stop_id=f"stop_{i}",
                stop_name=f"Stop {i}",
                lat=59.9 + i * 0.01,
                lon=10.7 + i * 0.01,
                travel_time_seconds=i * 300,
                num_transfers=0,
            )
            for i in range(5)
        ]
        result = IsochroneResult(
            config=sample_isochrone_config,
            reachable_stops=stops,
        )
        assert result.num_reachable_stops == 5

    def test_get_stops_within_time(
        self, sample_isochrone_config: IsochroneConfig,
    ) -> None:
        """Test filtering stops by travel time."""
        stops = [
            ReachableStop(
                stop_id=f"stop_{i}",
                stop_name=f"Stop {i}",
                lat=59.9,
                lon=10.7,
                travel_time_seconds=i * 300,  # 0, 300, 600, 900, 1200 seconds
                num_transfers=0,
            )
            for i in range(5)
        ]
        result = IsochroneResult(
            config=sample_isochrone_config,
            reachable_stops=stops,
        )
        # Get stops reachable within 10 minutes (600 seconds)
        within_10_min = result.get_stops_within_time(600)
        assert len(within_10_min) == 3  # 0, 300, 600 seconds


class TestModelImmutability:
    """Tests for model immutability (frozen=True)."""

    def test_stop_immutable(self, sample_stops: list[Stop]) -> None:
        """Test that Stop model is immutable."""
        stop = sample_stops[0]
        with pytest.raises(ValidationError):
            stop.stop_name = "New Name"

    def test_agency_immutable(self, sample_agency: Agency) -> None:
        """Test that Agency model is immutable."""
        with pytest.raises(ValidationError):
            sample_agency.agency_name = "New Agency"
