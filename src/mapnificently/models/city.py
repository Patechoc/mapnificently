"""City configuration models."""

from typing import Annotated, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]


class BBox(BaseModel):
    """A lon/lat bounding box."""

    model_config = ConfigDict(frozen=True)

    lon_min: Longitude
    lat_min: Latitude
    lon_max: Longitude
    lat_max: Latitude

    @model_validator(mode="after")
    def _check_order(self) -> Self:
        if self.lon_min >= self.lon_max or self.lat_min >= self.lat_max:
            msg = "bbox must be [lon_min, lat_min, lon_max, lat_max] with min < max"
            raise ValueError(msg)
        return self

    @classmethod
    def from_list(cls, values: list[float] | tuple[float, ...]) -> "BBox":
        """Build from ``[lon_min, lat_min, lon_max, lat_max]``."""
        if len(values) != 4:
            msg = "bbox needs exactly 4 numbers"
            raise ValueError(msg)
        return cls(lon_min=values[0], lat_min=values[1], lon_max=values[2], lat_max=values[3])

    def as_list(self) -> list[float]:
        """Return ``[lon_min, lat_min, lon_max, lat_max]``."""
        return [self.lon_min, self.lat_min, self.lon_max, self.lat_max]

    def contains(self, lat: float, lon: float) -> bool:
        """Whether a point lies inside the box."""
        return self.lat_min <= lat <= self.lat_max and self.lon_min <= lon <= self.lon_max


class LatLon(BaseModel):
    """A WGS84 coordinate."""

    model_config = ConfigDict(frozen=True)

    lat: Latitude
    lon: Longitude


class CityConfig(BaseModel):
    """One city: where its data comes from and what area it covers."""

    model_config = ConfigDict(frozen=True)

    id: Annotated[str, Field(pattern=r"^[a-z0-9_-]+$")]
    name: str
    timezone: str
    center: LatLon
    bbox: BBox
    provider: str
    feeds: Annotated[list[str], Field(min_length=1)]
    attribution: str = ""

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            msg = f"unknown timezone {value!r}"
            raise ValueError(msg) from exc
        return value

    @field_validator("center", mode="before")
    @classmethod
    def _center_from_list(cls, value: object) -> object:
        if isinstance(value, list | tuple) and len(value) == 2:
            return {"lat": value[0], "lon": value[1]}
        return value

    @field_validator("bbox", mode="before")
    @classmethod
    def _bbox_from_list(cls, value: object) -> object:
        if isinstance(value, list | tuple):
            return BBox.from_list(list(value))
        return value

    @property
    def tz(self) -> ZoneInfo:
        """The city's timezone."""
        return ZoneInfo(self.timezone)
