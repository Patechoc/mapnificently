"""City configuration models."""

from typing import Annotated, Any, Self
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


def _timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        msg = f"unknown timezone {value!r}"
        raise ValueError(msg) from exc
    return value


def _latlon(value: object) -> object:
    if isinstance(value, list | tuple) and len(value) == 2:
        return {"lat": value[0], "lon": value[1]}
    return value


def _upper(value: object) -> object:
    return value.upper() if isinstance(value, str) else value


def _bbox(value: object) -> object:
    if isinstance(value, list | tuple):
        return BBox.from_list(list(value))
    return value


RegionId = Annotated[str, Field(pattern=r"^[a-z0-9_-]+$")]
CountryCode = Annotated[str, Field(pattern=r"^[A-Z]{2}$", description="ISO 3166-1 alpha-2.")]
REGION_FIELDS = ("timezone", "bbox", "provider", "feeds", "attribution")


class RegionConfig(BaseModel):
    """An area whose timetables are downloaded and prepared once, shared by its cities.

    A trip can use every operator in the region, so cities of one region (e.g. Oslo and Moss)
    reach each other and give the same answers.
    """

    model_config = ConfigDict(frozen=True)

    id: RegionId
    name: str
    timezone: str
    bbox: BBox
    provider: str
    feeds: Annotated[list[str], Field(min_length=1)]
    attribution: str = ""
    center: LatLon | None = Field(
        default=None, description="Centre of the metric projection; defaults to the bbox centre."
    )

    _check_timezone = field_validator("timezone")(_timezone)
    _center_from_list = field_validator("center", mode="before")(_latlon)
    _bbox_from_list = field_validator("bbox", mode="before")(_bbox)

    @property
    def projection_center(self) -> LatLon:
        """Where distances are measured from (the same for every city of the region)."""
        if self.center is not None:
            return self.center
        return LatLon(
            lat=(self.bbox.lat_min + self.bbox.lat_max) / 2,
            lon=(self.bbox.lon_min + self.bbox.lon_max) / 2,
        )


class CityConfig(BaseModel):
    """A city: a named view (map centre) onto a region's timetables.

    Either name a ``region``, or give the region fields (``timezone``, ``bbox``, ``provider``,
    ``feeds``, ``attribution``) directly: the city then is a region of its own.
    The region fields are always filled in, copied from the region.

    ``country`` belongs to the city, not the region: a region may cross a border.
    """

    model_config = ConfigDict(frozen=True)

    id: RegionId
    name: str
    country: CountryCode | None = None
    timezone: str
    center: LatLon
    bbox: BBox
    provider: str
    feeds: Annotated[list[str], Field(min_length=1)]
    attribution: str = ""
    region: RegionConfig

    _check_timezone = field_validator("timezone")(_timezone)
    _country_upper = field_validator("country", mode="before")(_upper)
    _center_from_list = field_validator("center", mode="before")(_latlon)
    _bbox_from_list = field_validator("bbox", mode="before")(_bbox)

    @model_validator(mode="before")
    @classmethod
    def _fill_region(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        region = data.get("region")
        if region is None:  # a city with its own feeds is a region of its own
            data = dict(data)
            data["region"] = {
                "id": data.get("id"),
                "name": data.get("name"),
                "center": data.get("center"),
                **{k: data[k] for k in REGION_FIELDS if k in data},
            }
            return data
        if isinstance(region, dict):
            region = RegionConfig(**region)
        own = [k for k in REGION_FIELDS if k in data and k != "timezone"]
        if own:
            msg = (
                f"city {data.get('id')!r} is in region {region.id!r}: set "
                f"{', '.join(own)} on the region, not the city"
            )
            raise ValueError(msg)
        return {**data, "region": region, **{k: getattr(region, k) for k in REGION_FIELDS}}

    @property
    def tz(self) -> ZoneInfo:
        """The city's timezone."""
        return ZoneInfo(self.timezone)
