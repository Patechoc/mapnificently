"""Keeps each region's prepared data on disk and its routing structures in memory.

Data belongs to regions, not cities: cities of one region (e.g. Oslo and Moss) share one
prepared copy on disk and one network and timetable cache in memory. Downloads are shared even
more widely: one folder per provider, so a feed used by several regions is stored once.

Methods take a city id (what the API and CLI are given) and work on its region.
"""

import threading
from collections import OrderedDict
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from mapnificently.gtfs import Feed, prepare_city
from mapnificently.models import CityConfig, RegionConfig
from mapnificently.providers import TransitProvider, get_provider
from mapnificently.providers.gtfs_url import read_feed_meta
from mapnificently.settings import Settings
from mapnificently.timetable.build import (
    StopNetwork,
    Timetable,
    TripInfo,
    build_network,
    build_timetable,
    load_trip_info,
    read_meta,
)


class DataNotReadyError(RuntimeError):
    """The region's feeds have not been fetched and prepared yet."""


class CityDataStore:
    """Fetch, prepare and serve routing data for the configured cities and their regions."""

    def __init__(
        self,
        settings: Settings,
        cities: dict[str, CityConfig],
        http_client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings
        self.cities = cities
        self.http_client = http_client
        self.regions: dict[str, RegionConfig] = {}
        for city in cities.values():
            known = self.regions.setdefault(city.region.id, city.region)
            if known != city.region:
                msg = f"region {city.region.id!r} is defined differently by two cities"
                raise ValueError(msg)
        self._lock = threading.RLock()
        self._networks: dict[str, StopNetwork] = {}
        self._trip_info: dict[str, TripInfo] = {}
        self._timetables: OrderedDict[tuple[str, date], Timetable] = OrderedDict()
        self._providers: dict[str, TransitProvider] = {}

    # ------------------------------------------------------------ regions and their files

    def region_of(self, city_id: str) -> RegionConfig:
        """The region whose timetables a city uses."""
        return self.cities[city_id].region

    def cities_in(self, region_id: str) -> list[str]:
        """Ids of the cities that use a region."""
        return [c.id for c in self.cities.values() if c.region.id == region_id]

    def _any_city(self, region_id: str) -> CityConfig:
        # Providers work on a city; every city of a region has the region's feeds and bbox.
        return self.cities[self.cities_in(region_id)[0]]

    def provider(self, city_id: str) -> TransitProvider:
        """The data provider of a city (one instance per provider name)."""
        name = self.cities[city_id].provider
        with self._lock:
            if name not in self._providers:
                self._providers[name] = get_provider(name, self.settings, self.http_client)
            return self._providers[name]

    def region_prepared_dir(self, region_id: str) -> Path:
        """Where a region's prepared tables live."""
        return self.settings.region_dir(region_id) / "prepared"

    def prepared_dir(self, city_id: str) -> Path:
        """Where the prepared tables of a city's region live."""
        return self.region_prepared_dir(self.region_of(city_id).id)

    def feeds_dir(self, city_id: str) -> Path:
        """The shared download folder of the city's provider."""
        return self.settings.feeds_dir(self.cities[city_id].provider)

    def feed_paths(self, region_id: str) -> list[Path] | None:
        """Where the region's feeds are (or would be) downloaded; ``None`` if unknown."""
        city = self._any_city(region_id)
        return self.provider(city.id).feed_paths(city, self.feeds_dir(city.id))

    def region_ready(self, region_id: str) -> bool:
        """Whether a region has prepared data."""
        return (self.region_prepared_dir(region_id) / "meta.json").is_file()

    def is_ready(self, city_id: str) -> bool:
        """Whether the city's region has prepared data."""
        return self.region_ready(self.region_of(city_id).id)

    def meta(self, city_id: str) -> dict[str, Any]:
        """Metadata of the prepared data."""
        self._require_ready(city_id)
        return read_meta(self.prepared_dir(city_id))

    def refresh(self, city_id: str, *, force: bool = False) -> dict[str, object]:
        """Download and prepare the city's region (for every city of that region)."""
        return self.refresh_region(self.region_of(city_id).id, force=force)

    def refresh_region(self, region_id: str, *, force: bool = False) -> dict[str, object]:
        """Download the region's feeds (unchanged ones are skipped) and prepare them."""
        region = self.regions[region_id]
        city = self._any_city(region_id)
        paths = self.provider(city.id).fetch_feeds(city, self.feeds_dir(city.id), force=force)
        stems = [path.stem for path in paths]
        feeds = []
        for path in paths:
            download = read_feed_meta(path)
            # Feed names prefix trip ids; keep them unique even if two files share a name.
            name = path.stem if stems.count(path.stem) == 1 else f"{path.parent.name}-{path.stem}"
            feeds.append(
                Feed(
                    name=name,
                    path=path,
                    source=download.get("source", ""),
                    last_modified=download.get("last_modified"),
                )
            )
        meta = prepare_city(feeds, region.bbox, self.region_prepared_dir(region_id))
        self.invalidate_region(region_id)
        return meta

    def invalidate(self, city_id: str) -> None:
        """Forget in-memory structures of a city's region."""
        self.invalidate_region(self.region_of(city_id).id)

    def invalidate_region(self, region_id: str) -> None:
        """Forget in-memory structures of a region."""
        with self._lock:
            self._networks.pop(region_id, None)
            self._trip_info.pop(region_id, None)
            for key in [k for k in self._timetables if k[0] == region_id]:
                del self._timetables[key]

    # ------------------------------------------------------------ routing structures

    def network(self, city_id: str) -> StopNetwork:
        """Stops and walking links of a city's region."""
        region = self.region_of(city_id)
        with self._lock:
            if region.id not in self._networks:
                self._require_ready(city_id)
                center = region.projection_center
                self._networks[region.id] = build_network(
                    self.region_prepared_dir(region.id), center.lat, center.lon
                )
            return self._networks[region.id]

    def trip_info(self, city_id: str) -> TripInfo:
        """Line names, directions and route types of the region's trips."""
        region_id = self.region_of(city_id).id
        with self._lock:
            if region_id not in self._trip_info:
                self._require_ready(city_id)
                self._trip_info[region_id] = load_trip_info(self.region_prepared_dir(region_id))
            return self._trip_info[region_id]

    def timetable(self, city_id: str, service_date: date) -> Timetable:
        """Connections of the region on one service date (least recently used are dropped)."""
        region_id = self.region_of(city_id).id
        key = (region_id, service_date)
        with self._lock:
            if key in self._timetables:
                self._timetables.move_to_end(key)
                return self._timetables[key]
            self._require_ready(city_id)
            timetable = build_timetable(self.region_prepared_dir(region_id), service_date)
            self._timetables[key] = timetable
            while len(self._timetables) > self.settings.timetable_cache_size:
                self._timetables.popitem(last=False)
            return timetable

    def _require_ready(self, city_id: str) -> None:
        if not self.is_ready(city_id):
            region = self.region_of(city_id).id
            msg = (
                f"no data for {city_id!r} (region {region!r}) yet; run "
                f"`mapnificently data fetch {city_id}` or POST /api/cities/{city_id}/refresh"
            )
            raise DataNotReadyError(msg)
