"""Export a static, offline-capable copy of the web map (for GitHub Pages or any file host).

Without a server, routing runs in the browser (``web/offline.js``), as in the original
Mapnificent. The export writes, per region, the stop network and one timetable per chosen date
as binary "containers": a little-endian ``uint32`` header length, a JSON header, then
8-byte-aligned arrays the browser wraps as typed arrays without parsing.

Layout of the exported directory::

    index.html, sw.js, .nojekyll
    static/            the web map (app.js, offline.js, style.css, vendor/leaflet)
    data/manifest.json cities, their dates and files
    data/<region>/network.bin    stops, walking links, trip names (shared by its cities)
    data/<region>/tt-<date>.bin  connections of one service day
"""

import json
import shutil
from datetime import UTC, date, datetime
from importlib import resources
from pathlib import Path
from typing import Any

import numpy as np

from mapnificently import __version__
from mapnificently.models.api import MAX_MINUTES
from mapnificently.timetable import WALK_DETOUR, CityDataStore

WEB_DIR = Path(str(resources.files("mapnificently").joinpath("web")))
BOARD, ALIGHT = 1, 2  # bits of the per-connection flags array


def write_container(path: Path, meta: dict[str, Any], arrays: dict[str, np.ndarray]) -> int:
    """Write ``meta`` and ``arrays`` in the container format; return the file size."""
    index: dict[str, dict[str, Any]] = {}
    offset = 0
    for name, array in arrays.items():
        offset = (offset + 7) // 8 * 8
        index[name] = {"dtype": str(array.dtype), "offset": offset, "length": int(array.size)}
        offset += array.nbytes

    def header_bytes(base: int) -> bytes:
        shifted = {n: {**a, "offset": a["offset"] + base} for n, a in index.items()}
        return json.dumps({"meta": meta, "arrays": shifted}, ensure_ascii=False).encode()

    # The header holds absolute offsets, which depend on the header's own (padded) length.
    base = 0
    while True:
        header = header_bytes(base)
        needed = (4 + len(header) + 7) // 8 * 8
        if needed == base:
            break
        base = needed
    with path.open("wb") as fh:
        fh.write(len(header).to_bytes(4, "little"))
        fh.write(header)
        fh.write(b"\0" * (base - 4 - len(header)))
        position = base
        for name, array in arrays.items():
            start = index[name]["offset"] + base
            fh.write(b"\0" * (start - position))
            fh.write(np.ascontiguousarray(array).astype(array.dtype.newbyteorder("<")).tobytes())
            position = start + array.nbytes
    return path.stat().st_size


def _dictionary(values: list[str | None]) -> tuple[list[str], np.ndarray]:
    """Unique strings and, per value, its index (or -1 for missing)."""
    words: dict[str, int] = {}
    codes = np.full(len(values), -1, dtype=np.int32)
    for i, value in enumerate(values):
        if value:
            codes[i] = words.setdefault(value, len(words))
    return list(words), codes


def _export_region(
    store: CityDataStore, city_id: str, dates: list[date], out: Path
) -> dict[str, Any]:
    """Write the network and timetables of ``city_id``'s region; return its files and sizes."""
    region = store.region_of(city_id)
    network = store.network(city_id)
    trips = store.trip_info(city_id)
    region_dir = out / "data" / region.id
    region_dir.mkdir(parents=True, exist_ok=True)

    lines, line_codes = _dictionary(list(trips.line))
    headsigns, headsign_codes = _dictionary(trips.headsign)
    sizes = {
        "network.bin": write_container(
            region_dir / "network.bin",
            {
                "region": region.id,
                "names": network.names,
                # The browser must measure distances exactly like the server.
                "center": [network.projection.lat0, network.projection.lon0],
                "num_trips": trips.num_trips,
                "lines": lines,
                "headsigns": headsigns,
            },
            {
                "lat": network.lat.astype(np.float64),
                "lon": network.lon.astype(np.float64),
                "fp_ptr": network.fp_ptr.astype(np.int32),
                "fp_to": network.fp_to.astype(np.int32),
                "fp_dist": network.fp_dist.astype(np.float64),
                "trip_line": line_codes,
                "trip_headsign": headsign_codes,
                "trip_route_type": trips.route_type.astype(np.int32),
            },
        )
    }
    timetables = {}
    connections = {}
    for day in dates:
        tt = store.timetable(city_id, day)
        connections[day.isoformat()] = tt.num_connections
        name = f"tt-{day.isoformat()}.bin"
        flags = tt.board.astype(np.uint8) * BOARD | tt.alight.astype(np.uint8) * ALIGHT
        sizes[name] = write_container(
            region_dir / name,
            {"date": day.isoformat(), "num_trips": tt.num_trips},
            {
                "dep_stop": tt.dep_stop.astype(np.int32),
                "arr_stop": tt.arr_stop.astype(np.int32),
                "dep_s": tt.dep_s.astype(np.int32),
                "arr_s": tt.arr_s.astype(np.int32),
                "trip": tt.trip.astype(np.int32),
                "flags": flags.astype(np.uint8),
            },
        )
        timetables[day.isoformat()] = f"data/{region.id}/{name}"
    files = {"network": f"data/{region.id}/network.bin", "timetables": timetables}
    return {"files": files, "sizes": sizes, "connections": connections}


def export_static_site(
    store: CityDataStore, city_ids: list[str], dates: list[date], out: Path
) -> dict[str, Any]:
    """Write the static site for ``city_ids`` and ``dates`` into ``out`` (replacing it).

    Only an empty directory or a previous export is replaced, never anything else.
    """
    if out.exists() and any(out.iterdir()) and not (out / "data" / "manifest.json").is_file():
        msg = f"{out} is not empty and is not a previous export; choose another directory"
        raise FileExistsError(msg)
    shutil.rmtree(out, ignore_errors=True)
    shutil.copytree(WEB_DIR, out / "static", ignore=shutil.ignore_patterns("index.html", "sw.js"))
    (out / ".nojekyll").write_text("")

    regions: dict[str, dict[str, Any]] = {}
    for city_id in city_ids:  # each region once, however many of its cities are exported
        region_id = store.region_of(city_id).id
        if region_id not in regions:
            regions[region_id] = _export_region(store, city_id, dates, out)
    cities = []
    for city_id in city_ids:
        city = store.cities[city_id]
        info = city.model_dump(mode="json", exclude={"region"})
        cities.append(
            {
                **info,
                "region": city.region.id,
                "region_name": city.region.name,
                "data_ready": True,
                "dates": [d.isoformat() for d in dates],
                "files": regions[city.region.id]["files"],
            }
        )
    generated = datetime.now(UTC).replace(microsecond=0)
    manifest = {
        "generated_at": generated.isoformat(),
        "version": __version__,
        "max_minutes": MAX_MINUTES,
        "walk_detour": WALK_DETOUR,
        # Sent to Entur when the page searches addresses online (Entur asks clients to say who
        # they are). Set MAPNIFICENTLY_ENTUR_CLIENT_NAME before exporting.
        "entur_client_name": store.settings.entur_client_name,
        "cities": cities,
    }
    (out / "data" / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))

    index = (WEB_DIR / "index.html").read_text()
    index = index.replace(
        '<script src="static/app.js"></script>',
        '<script src="static/offline.js"></script>\n  <script src="static/app.js"></script>',
    )
    (out / "index.html").write_text(index)
    worker = (WEB_DIR / "sw.js").read_text()
    (out / "sw.js").write_text(worker.replace("__VERSION__", generated.strftime("%Y%m%d%H%M%S")))

    return {
        "out": str(out),
        "regions": {r: e["sizes"] for r, e in regions.items()},
        "connections": {r: e["connections"] for r, e in regions.items()},
        "total_bytes": sum(p.stat().st_size for p in out.rglob("*") if p.is_file()),
    }
