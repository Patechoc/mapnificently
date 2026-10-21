"""Find providers and cities.

Providers are looked up by name: the built-in ones below, plus any registered by other
packages under the ``mapnificently.providers`` entry-point group.
"""

import tomllib
from functools import cache
from importlib import resources
from importlib.metadata import entry_points
from typing import Any

import httpx

from mapnificently.models import CityConfig, RegionConfig
from mapnificently.providers.base import TransitProvider
from mapnificently.providers.entur import EnturProvider
from mapnificently.providers.gtfs_url import GtfsUrlProvider
from mapnificently.settings import Settings

BUILTIN_PROVIDERS: dict[str, type[TransitProvider]] = {
    "gtfs": GtfsUrlProvider,
    "entur": EnturProvider,
}


class UnknownProviderError(KeyError):
    """No provider is registered under this name."""


@cache
def provider_classes() -> dict[str, type[TransitProvider]]:
    """All provider classes, built-in first, then entry points."""
    classes = dict(BUILTIN_PROVIDERS)
    for ep in entry_points(group="mapnificently.providers"):
        classes[ep.name] = ep.load()
    return classes


def get_provider(
    name: str, settings: Settings, client: httpx.Client | None = None
) -> TransitProvider:
    """Instantiate the provider registered under ``name``."""
    try:
        cls = provider_classes()[name]
    except KeyError:
        known = ", ".join(sorted(provider_classes()))
        msg = f"unknown provider {name!r} (known: {known})"
        raise UnknownProviderError(msg) from None
    return cls(settings, client)


class UnknownRegionError(KeyError):
    """A city names a region that is not defined."""


def _cities_from_toml(data: dict[str, Any]) -> dict[str, CityConfig]:
    regions = {
        region_id: RegionConfig(id=region_id, **fields)
        for region_id, fields in data.get("regions", {}).items()
    }
    cities = {}
    for city_id, raw in data.get("cities", {}).items():
        fields = dict(raw)
        if "region" in fields:
            region_id = fields.pop("region")
            if region_id not in regions:
                msg = f"city {city_id!r} uses unknown region {region_id!r}"
                raise UnknownRegionError(msg)
            fields["region"] = regions[region_id]
        cities[city_id] = CityConfig(id=city_id, **fields)
    return cities


def load_cities(settings: Settings) -> dict[str, CityConfig]:
    """Packaged regions and cities, overridden and extended by ``settings.cities_file``.

    Both files are merged before cities are linked to regions, so a user file can redefine a
    packaged region (e.g. add a feed) and every city of that region follows.
    """
    packaged = resources.files("mapnificently").joinpath("cities.toml").read_text("utf-8")
    data = tomllib.loads(packaged)
    if settings.cities_file is not None:
        user = tomllib.loads(settings.cities_file.read_text("utf-8"))
        for section in ("regions", "cities"):
            data[section] = data.get(section, {}) | user.get(section, {})
    cities = _cities_from_toml(data)
    for city in cities.values():
        if city.provider not in provider_classes():
            msg = f"city {city.id!r} uses unknown provider {city.provider!r}"
            raise UnknownProviderError(msg)
    return cities
