"""Keeps each city's prepared data on disk and its routing structures in memory."""

import threading
from collections import OrderedDict
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from mapnificently.gtfs import Feed, prepare_city
from mapnificently.models import CityConfig
from mapnificently.providers import TransitProvider, get_provider
from mapnificently.providers.gtfs_url import read_feed_meta
from mapnificently.settings import Settings
from mapnificently.timetable.build import (
    StopNetwork,
    Timetable,
    build_network,
    build_timetable,
    read_meta,
)


class DataNotReadyError(RuntimeError):
    """The city's feeds have not been fetched and prepared yet."""


class CityDataStore:
    """Fetch, prepare and serve routing data for the configured cities."""

    def __init__(
        self,
        settings: Settings,
        cities: dict[str, CityConfig],
        http_client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings
        self.cities = cities
        self.http_client = http_client
        self._lock = threading.RLock()
        self._networks: dict[str, StopNetwork] = {}
        self._timetables: OrderedDict[tuple[str, date], Timetable] = OrderedDict()
        self._providers: dict[str, TransitProvider] = {}

    def provider(self, city_id: str) -> TransitProvider:
        """The data provider of a city (one instance per provider name)."""
        name = self.cities[city_id].provider
        with self._lock:
            if name not in self._providers:
                self._providers[name] = get_provider(name, self.settings, self.http_client)
            return self._providers[name]

    def prepared_dir(self, city_id: str) -> Path:
        """Where the prepared tables of a city live."""
        return self.settings.city_dir(city_id) / "prepared"

    def feeds_dir(self, city_id: str) -> Path:
        """Where the downloaded feeds of a city live."""
        return self.settings.city_dir(city_id) / "feeds"

    def is_ready(self, city_id: str) -> bool:
        """Whether the city has prepared data."""
        return (self.prepared_dir(city_id) / "meta.json").is_file()

    def meta(self, city_id: str) -> dict[str, Any]:
        """Metadata of the prepared data."""
        self._require_ready(city_id)
        return read_meta(self.prepared_dir(city_id))

    def refresh(self, city_id: str, *, force: bool = False) -> dict[str, object]:
        """Download the city's feeds and prepare them, replacing the previous data."""
        city = self.cities[city_id]
        paths = self.provider(city_id).fetch_feeds(city, self.feeds_dir(city_id), force=force)
        feeds = []
        for path in paths:
            download = read_feed_meta(path)
            feeds.append(
                Feed(
                    name=path.stem,
                    path=path,
                    source=download.get("source", ""),
                    last_modified=download.get("last_modified"),
                )
            )
        meta = prepare_city(feeds, city.bbox, self.prepared_dir(city_id))
        self.invalidate(city_id)
        return meta

    def invalidate(self, city_id: str) -> None:
        """Forget in-memory structures of a city."""
        with self._lock:
            self._networks.pop(city_id, None)
            for key in [k for k in self._timetables if k[0] == city_id]:
                del self._timetables[key]

    def network(self, city_id: str) -> StopNetwork:
        """Stops and walking links of a city."""
        with self._lock:
            if city_id not in self._networks:
                self._require_ready(city_id)
                city = self.cities[city_id]
                self._networks[city_id] = build_network(
                    self.prepared_dir(city_id), city.center.lat, city.center.lon
                )
            return self._networks[city_id]

    def timetable(self, city_id: str, service_date: date) -> Timetable:
        """Connections of a city on one service date (least recently used ones are dropped)."""
        key = (city_id, service_date)
        with self._lock:
            if key in self._timetables:
                self._timetables.move_to_end(key)
                return self._timetables[key]
            self._require_ready(city_id)
            timetable = build_timetable(self.prepared_dir(city_id), service_date)
            self._timetables[key] = timetable
            while len(self._timetables) > self.settings.timetable_cache_size:
                self._timetables.popitem(last=False)
            return timetable

    def _require_ready(self, city_id: str) -> None:
        if not self.is_ready(city_id):
            msg = (
                f"no data for {city_id!r} yet; run `mapnificently data fetch {city_id}` "
                f"or POST /api/cities/{city_id}/refresh"
            )
            raise DataNotReadyError(msg)
