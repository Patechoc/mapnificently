"""The provider interface: how a city's timetable data is obtained."""

from abc import ABC, abstractmethod
from pathlib import Path

import httpx

from mapnificently.models import CityConfig, Place
from mapnificently.settings import Settings


class ProviderError(RuntimeError):
    """A provider could not deliver data."""


class TransitProvider(ABC):
    """Delivers GTFS feeds (and optionally place search) for cities.

    Everything after the download works on GTFS, so a source that does not publish GTFS is
    supported by writing a provider whose ``fetch_feeds`` produces GTFS-shaped zip files.
    """

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(30.0, read=300.0),
            follow_redirects=True,
            headers=self.default_headers(),
        )

    def default_headers(self) -> dict[str, str]:
        """Headers sent with every request."""
        return {"User-Agent": f"mapnificently/0.1 ({self.settings.entur_client_name})"}

    @abstractmethod
    def fetch_feeds(self, city: CityConfig, dest_dir: Path, *, force: bool = False) -> list[Path]:
        """Download (or refresh) the city's GTFS zips into ``dest_dir`` and return their paths.

        ``dest_dir`` is shared by every region using this provider, so a feed used by several
        regions is stored once.
        """

    def feed_paths(self, city: CityConfig, dest_dir: Path) -> list[Path] | None:  # noqa: ARG002
        """Where :meth:`fetch_feeds` stores the city's feeds, without downloading anything.

        ``None`` means unknown: cleanup then leaves this provider's files alone.
        """
        return None

    def geocode(self, city: CityConfig, text: str, limit: int = 8) -> list[Place]:  # noqa: ARG002
        """Search for places in the city. Providers without a geocoder return nothing."""
        return []
