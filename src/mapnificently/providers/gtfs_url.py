"""Generic provider: each feed is a GTFS zip URL, a ``file://`` URL or a local path."""

import hashlib
import json
import re
import shutil
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx

from mapnificently.models import CityConfig, Place
from mapnificently.providers.base import ProviderError, TransitProvider

PHOTON_URL = "https://photon.komoot.io/api/"


def feed_name(source: str) -> str:
    """A filesystem-safe name for a feed, derived from its URL or path."""
    stem = Path(urlparse(source).path).name.removesuffix(".zip") or "feed"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", stem)


class GtfsUrlProvider(TransitProvider):
    """Downloads GTFS zips from URLs, skipping unchanged ones (ETag / Last-Modified)."""

    def feed_sources(self, city: CityConfig) -> list[tuple[str, str]]:
        """``(name, url_or_path)`` for each feed of the city."""
        return [(feed_name(feed), feed) for feed in city.feeds]

    def feed_path(self, name: str, source: str, dest_dir: Path) -> Path:
        """One folder per source, so two URLs ending in the same file name never clash."""
        digest = hashlib.sha256(source.encode()).hexdigest()[:12]
        return dest_dir / digest / f"{name}.zip"

    def feed_paths(self, city: CityConfig, dest_dir: Path) -> list[Path]:
        return [self.feed_path(name, source, dest_dir) for name, source in self.feed_sources(city)]

    def fetch_feeds(self, city: CityConfig, dest_dir: Path, *, force: bool = False) -> list[Path]:
        paths = []
        for (_, source), dest in zip(
            self.feed_sources(city), self.feed_paths(city, dest_dir), strict=True
        ):
            dest.parent.mkdir(parents=True, exist_ok=True)
            paths.append(self._fetch_one(source, dest, force=force))
        return paths

    def _fetch_one(self, source: str, dest: Path, *, force: bool) -> Path:
        parsed = urlparse(source)
        if parsed.scheme in {"", "file"}:
            local = Path(unquote(parsed.path) if parsed.scheme == "file" else source)
            if not local.is_file():
                msg = f"feed file not found: {local}"
                raise ProviderError(msg)
            shutil.copyfile(local, dest)
            _write_sidecar(dest, {"source": source})
            return dest

        sidecar = _read_sidecar(dest)
        headers: dict[str, str] = {}
        if dest.is_file() and not force:
            if etag := sidecar.get("etag"):
                headers["If-None-Match"] = etag
            if last_modified := sidecar.get("last_modified"):
                headers["If-Modified-Since"] = last_modified

        tmp = dest.with_suffix(".part")
        try:
            with self.client.stream("GET", source, headers=headers) as response:
                if response.status_code == httpx.codes.NOT_MODIFIED:
                    return dest
                response.raise_for_status()
                with tmp.open("wb") as fh:
                    for chunk in response.iter_bytes(1 << 20):
                        fh.write(chunk)
                meta = {
                    "source": source,
                    "etag": response.headers.get("ETag"),
                    "last_modified": response.headers.get("Last-Modified"),
                }
        except httpx.HTTPError as exc:
            tmp.unlink(missing_ok=True)
            msg = f"could not download {source}: {exc}"
            raise ProviderError(msg) from exc
        tmp.replace(dest)
        _write_sidecar(dest, meta)
        return dest

    def geocode(self, city: CityConfig, text: str, limit: int = 8) -> list[Place]:
        """Search with Photon (OpenStreetMap), limited to the city's bbox."""
        bbox = ",".join(str(v) for v in city.bbox.as_list())
        try:
            response = self.client.get(PHOTON_URL, params={"q": text, "limit": limit, "bbox": bbox})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            msg = f"geocoding failed: {exc}"
            raise ProviderError(msg) from exc
        places = []
        for feature in response.json().get("features", []):
            props = feature.get("properties", {})
            lon, lat = feature["geometry"]["coordinates"]
            name = props.get("name") or props.get("street") or text
            parts = [name, props.get("city")]
            places.append(
                Place(
                    name=name,
                    label=", ".join(p for p in parts if p),
                    lat=lat,
                    lon=lon,
                    kind=props.get("osm_value", ""),
                )
            )
        return places


def _sidecar_path(dest: Path) -> Path:
    return dest.with_suffix(".json")


def _read_sidecar(dest: Path) -> dict[str, str]:
    path = _sidecar_path(dest)
    if not path.is_file():
        return {}
    data: dict[str, str] = json.loads(path.read_text())
    return data


def _write_sidecar(dest: Path, meta: dict[str, str | None]) -> None:
    clean = {k: v for k, v in meta.items() if v is not None}
    _sidecar_path(dest).write_text(json.dumps(clean, indent=2))


def read_feed_meta(zip_path: Path) -> dict[str, str]:
    """Download metadata stored next to a feed zip."""
    return _read_sidecar(zip_path)
