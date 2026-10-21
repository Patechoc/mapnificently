"""Entur, Norway's national journey planner: GTFS per codespace plus a geocoder.

Data is free under NLOD. Entur asks every client to identify itself with ``ET-Client-Name``.
"""

import httpx

from mapnificently.models import CityConfig, Place
from mapnificently.providers.base import ProviderError
from mapnificently.providers.gtfs_url import GtfsUrlProvider

ENTUR_GTFS_URL = (
    "https://storage.googleapis.com/marduk-production/outbound/gtfs/rb_{code}-aggregated-gtfs.zip"
)
ENTUR_GEOCODER_URL = "https://api.entur.io/geocoder/v1/autocomplete"


class EnturProvider(GtfsUrlProvider):
    """Feeds are Entur codespaces, e.g. ``rut`` (Ruter), ``vyg`` (Vy), ``atb`` (AtB)."""

    def default_headers(self) -> dict[str, str]:
        return {**super().default_headers(), "ET-Client-Name": self.settings.entur_client_name}

    def feed_sources(self, city: CityConfig) -> list[tuple[str, str]]:
        return [(code, ENTUR_GTFS_URL.format(code=code)) for code in city.feeds]

    def geocode(self, city: CityConfig, text: str, limit: int = 8) -> list[Place]:
        params: dict[str, str | int | float] = {
            "text": text,
            "size": limit,
            "lang": "no",
            "boundary.rect.min_lat": city.bbox.lat_min,
            "boundary.rect.max_lat": city.bbox.lat_max,
            "boundary.rect.min_lon": city.bbox.lon_min,
            "boundary.rect.max_lon": city.bbox.lon_max,
        }
        try:
            response = self.client.get(ENTUR_GEOCODER_URL, params=params)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            msg = f"geocoding failed: {exc}"
            raise ProviderError(msg) from exc
        places = []
        for feature in response.json().get("features", []):
            props = feature.get("properties", {})
            lon, lat = feature["geometry"]["coordinates"]
            name = props.get("name", text)
            places.append(
                Place(
                    name=name,
                    label=props.get("label", name),
                    lat=lat,
                    lon=lon,
                    kind=props.get("layer", ""),
                )
            )
        return places
