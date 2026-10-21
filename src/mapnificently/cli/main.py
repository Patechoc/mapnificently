"""Command line interface.

``serve`` and ``data`` work locally; the other commands talk to a running API
(``--api-url`` or ``MAPNIFICENTLY_API_URL``).
"""

import json
import re
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click
import httpx

from mapnificently import __version__
from mapnificently.models.api import DEFAULT_MIN_CHANGE_MIN, MAX_MINUTES
from mapnificently.settings import get_settings

if TYPE_CHECKING:
    from mapnificently.timetable import CityDataStore

COORD_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")
TIME_FORMATS = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%H:%M"]


class ApiClient:
    """Thin wrapper turning HTTP errors into readable CLI errors."""

    def __init__(self, client: httpx.Client) -> None:
        self.client = client

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self.client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            msg = f"cannot reach the API ({exc}). Is `mapnificently serve` running?"
            raise click.ClickException(msg) from exc
        if response.is_error:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            msg = f"{response.status_code}: {detail}"
            raise click.ClickException(msg)
        return response.json()


def _api(ctx: click.Context) -> ApiClient:
    obj = ctx.ensure_object(dict)
    if "api" not in obj:
        client = obj.get("client") or httpx.Client(base_url=obj["api_url"], timeout=120)
        obj["api"] = ApiClient(client)
    api: ApiClient = obj["api"]
    return api


def _departure(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.year == 1900:  # only a time was given: today
        value = datetime.combine(date.today(), value.time())  # noqa: DTZ011
    return value.isoformat()


def _resolve_place(api: ApiClient, city: str, value: str) -> tuple[dict[str, float], str]:
    """A "lat,lon" or a place name (looked up with the city's geocoder), and a label for it."""
    if match := COORD_RE.match(value):
        return {"lat": float(match[1]), "lon": float(match[2])}, value.strip()
    hits = api.request("GET", f"/api/cities/{city}/geocode", params={"q": value, "limit": 1})
    if not hits:
        msg = f"no place found for {value!r}"
        raise click.ClickException(msg)
    hit = hits[0]
    click.echo(f"{value!r} → {hit['label']} ({hit['lat']:.5f}, {hit['lon']:.5f})", err=True)
    return {"lat": hit["lat"], "lon": hit["lon"]}, hit["label"]


def _resolve_origins(api: ApiClient, city: str, values: tuple[str, ...]) -> list[dict[str, float]]:
    return [_resolve_place(api, city, value)[0] for value in values]


def _table(rows: list[list[str]], headers: list[str]) -> None:
    widths = [max(len(str(x)) for x in col) for col in zip(headers, *rows, strict=False)]
    line = "  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True))
    click.echo(line)
    click.echo("  ".join("-" * w for w in widths))
    for row in rows:
        click.echo("  ".join(str(c).ljust(w) for c, w in zip(row, widths, strict=True)))


trip_options = [
    click.option(
        "--from",
        "origins",
        multiple=True,
        required=True,
        help='Start point: "lat,lon" or a place name. Repeat for several.',
    ),
    click.option(
        "--at",
        "departure",
        type=click.DateTime(TIME_FORMATS),
        help="Departure, local time (YYYY-MM-DDTHH:MM or HH:MM). Default: now.",
    ),
    click.option("--walk-speed", type=float, default=1.25, show_default=True, help="m/s"),
    click.option("--max-walk", type=float, default=1000, show_default=True, help="metres"),
    click.option(
        "--min-change",
        type=click.IntRange(0, 15),
        default=DEFAULT_MIN_CHANGE_MIN,
        show_default=True,
        help="Minutes needed to change between vehicles.",
    ),
]


def with_trip_options(func: Any) -> Any:
    for option in reversed(trip_options):
        func = option(func)
    return func


@click.group()
@click.version_option(__version__)
@click.option(
    "--api-url",
    envvar="MAPNIFICENTLY_API_URL",
    default=None,
    help="API base URL (default from settings: http://127.0.0.1:8000).",
)
@click.pass_context
def cli(ctx: click.Context, api_url: str | None) -> None:
    """Mapnificently: where can you get by public transport in a given time?"""
    obj = ctx.ensure_object(dict)
    obj.setdefault("api_url", api_url or get_settings().api_url)


@cli.command()
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8000, show_default=True, type=int)
@click.option("--reload", is_flag=True, help="Restart on code changes (development).")
def serve(host: str, port: int, reload: bool) -> None:
    """Run the API and web map."""
    import uvicorn  # noqa: PLC0415

    click.echo(f"Map: http://{host}:{port}/   API docs: http://{host}:{port}/docs", err=True)
    uvicorn.run(
        "mapnificently.api.app:create_app", factory=True, host=host, port=port, reload=reload
    )


@cli.group()
def data() -> None:
    """Download and prepare timetable data (runs locally, no server needed).

    Data belongs to regions: the cities of a region share it. Downloads are shared by all
    regions, so a feed used by several regions is stored once.
    """


def _local_store() -> "CityDataStore":
    from mapnificently.providers import load_cities  # noqa: PLC0415
    from mapnificently.timetable import CityDataStore  # noqa: PLC0415

    settings = get_settings()
    return CityDataStore(settings, load_cities(settings))


def _regions_named(store: "CityDataStore", names: tuple[str, ...]) -> list[str]:
    """Region ids for city or region names, each once, in the order given."""
    regions: list[str] = []
    unknown = []
    for name in names:
        region = store.regions.get(name) or (
            store.cities[name].region if name in store.cities else None
        )
        if region is None:
            unknown.append(name)
        elif region.id not in regions:
            regions.append(region.id)
    if unknown:
        known = ", ".join([*store.cities, *(r for r in store.regions if r not in store.cities)])
        msg = f"unknown cities or regions: {', '.join(unknown)} (known: {known})"
        raise click.UsageError(msg)
    return regions


@data.command("fetch")
@click.argument("names", nargs=-1)
@click.option("--all", "fetch_all", is_flag=True, help="Every configured region.")
@click.option("--force", is_flag=True, help="Download even if unchanged.")
def data_fetch(names: tuple[str, ...], fetch_all: bool, force: bool) -> None:
    """Download and prepare the regions of NAMES (cities or regions)."""
    from mapnificently.providers import ProviderError  # noqa: PLC0415

    store = _local_store()
    if not names and not fetch_all:
        msg = f"name a city or region, or use --all (cities: {', '.join(store.cities)})"
        raise click.UsageError(msg)
    regions = list(store.regions) if fetch_all else _regions_named(store, names)
    for region_id in regions:
        region = store.regions[region_id]
        cities = ", ".join(store.cities_in(region_id))
        click.echo(f"{region_id} ({cities}): fetching {', '.join(region.feeds)} …")
        try:
            meta = store.refresh_region(region_id, force=force)
        except ProviderError as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(
            f"{region_id}: {meta['num_stops']} stops, {meta['num_trips']} trips, "
            f"service {meta['service_start']} → {meta['service_end']}"
        )


def _disk_size(path: Path) -> int:
    """Bytes used by ``path`` (a file, or all files under a folder; 0 if missing)."""
    if path.is_file():
        return path.stat().st_size
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _human(size: int) -> str:
    """Bytes as B, KB, MB or GB (decimal units)."""
    if size < 1000:
        return f"{size} B"
    value = size / 1000
    for unit in ("KB", "MB"):
        if value < 1000:
            return f"{value:.1f} {unit}"
        value /= 1000
    return f"{value:.1f} GB"


def _with_sidecar(path: Path) -> list[Path]:
    """A downloaded feed and the metadata file kept next to it."""
    return [path, path.with_suffix(".json")]


def _feed_users(store: "CityDataStore") -> dict[Path, list[str]]:
    """Each known feed file (and its metadata) → the regions using it."""
    users: dict[Path, list[str]] = {}
    for region_id in store.regions:
        for path in store.feed_paths(region_id) or []:
            for file in _with_sidecar(path):
                users.setdefault(file, []).append(region_id)
    return users


def _unused(store: "CityDataStore") -> list[Path]:
    """Files and folders under the data directory that no configured region uses."""
    settings = store.settings
    data_dir = settings.data_dir
    if not data_dir.is_dir():
        return []
    unused = [p for p in sorted(data_dir.iterdir()) if p.name not in {"feeds", "regions"}]
    if settings.regions_root.is_dir():
        unused += [
            p for p in sorted(settings.regions_root.iterdir()) if p.name not in store.regions
        ]
    if settings.feeds_root.is_dir():
        users = _feed_users(store)
        providers = {r.provider for r in store.regions.values()}
        for provider_dir in sorted(settings.feeds_root.iterdir()):
            if provider_dir.name not in providers:
                unused.append(provider_dir)
                continue
            known = [
                r
                for r in store.regions.values()
                if r.provider == provider_dir.name and store.feed_paths(r.id) is None
            ]
            if known:  # a provider that does not say where its files are: leave them
                continue
            unused += [f for f in sorted(provider_dir.rglob("*")) if f.is_file() and f not in users]
    return unused


def _remove(paths: list[Path], root: Path) -> None:
    for path in paths:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    # Drop folders left empty (e.g. a feed's own folder), but never the data directory itself.
    for folder in sorted((d for d in root.rglob("*") if d.is_dir()), reverse=True):
        if not any(folder.iterdir()):
            folder.rmdir()


@data.command("status")
@click.option("--paths", is_flag=True, help="Also show where each region's files are stored.")
def data_status(paths: bool) -> None:
    """Show which regions have data, and the disk space it takes."""
    from mapnificently.timetable import cached_dates  # noqa: PLC0415
    from mapnificently.timetable.build import read_meta  # noqa: PLC0415

    store = _local_store()
    settings = store.settings
    rows = []
    for region_id in store.regions:
        cities = ", ".join(store.cities_in(region_id))
        size = (
            _human(_disk_size(settings.region_dir(region_id)))
            if store.region_ready(region_id)
            else "-"
        )
        if store.region_ready(region_id):
            meta = read_meta(store.region_prepared_dir(region_id))
            rows.append(
                [
                    region_id,
                    cities,
                    "ready",
                    str(meta["num_stops"]),
                    f"{meta['service_start']} → {meta['service_end']}",
                    meta["prepared_at"][:16],
                    size,
                ]
            )
        else:
            rows.append([region_id, cities, "missing", "-", "-", "-", size])

    users = _feed_users(store)
    downloads = sum(_disk_size(f) for f in users)
    unused = _unused(store)
    unused_size = sum(_disk_size(p) for p in unused)
    total = _disk_size(settings.data_dir)
    rows.append(["downloads", "(shared)", "", "", "", "", _human(downloads)])
    if unused:
        rows.append(["unused", "", "", "", "", "", _human(unused_size)])
    rows.append(["total", "", "", "", "", "", _human(total)])
    _table(rows, ["region", "cities", "data", "stops", "service dates", "prepared (UTC)", "size"])
    click.echo(f"\ndata dir: {settings.data_dir}")
    if unused:
        click.echo("\nNot used by any region (remove with `mapnificently data prune`):")
        for path in unused:
            click.echo(f"  {path}  {_human(_disk_size(path))}")
    if not paths:
        click.echo("\n(use --paths to see where each region's files are stored)")
        return

    click.echo("\nWhere the data is stored:")
    for region_id in store.regions:
        region_dir = settings.region_dir(region_id)
        click.echo(f"\n{region_id} ({', '.join(store.cities_in(region_id))})")
        if region_dir.exists():
            days = cached_dates(store.region_prepared_dir(region_id))
            cached = f"{len(days)} day{'s' if len(days) != 1 else ''} of timetables cached"
            click.echo(
                f"  prepared data  {region_dir}  {_human(_disk_size(region_dir))}  ({cached})"
            )
        else:
            click.echo("  prepared data  not yet (run `mapnificently data fetch`)")
        for feed in store.feed_paths(region_id) or []:
            others = [r for r in users.get(feed, []) if r != region_id]
            shared = f"  (shared with {', '.join(others)})" if others else ""
            size = _human(_disk_size(feed)) if feed.exists() else "not downloaded"
            click.echo(f"  feed           {feed}  {size}{shared}")


def _confirm_and_remove(paths: list[Path], root: Path, yes: bool, what: str) -> None:
    if not paths:
        click.echo(f"Nothing to remove: {what}.")
        return
    for path in paths:
        click.echo(f"  {path}  {_human(_disk_size(path))}")
    total = _human(sum(_disk_size(p) for p in paths))
    if not yes and not click.confirm(f"Remove these ({total})?"):
        click.echo("Nothing removed.")
        return
    _remove(paths, root)
    click.echo(f"Removed {total}.")


@data.command("prune")
@click.option("--yes", is_flag=True, help="Do not ask for confirmation.")
def data_prune(yes: bool) -> None:
    """Remove data no configured region uses (old layouts, removed regions, unused feeds)."""
    store = _local_store()
    _confirm_and_remove(_unused(store), store.settings.data_dir, yes, "everything is in use")


@data.command("remove")
@click.argument("name")
@click.option("--yes", is_flag=True, help="Do not ask for confirmation.")
def data_remove(name: str, yes: bool) -> None:
    """Remove the data of NAME's region (a city or a region), and feeds only it uses."""
    store = _local_store()
    [region_id] = _regions_named(store, (name,))
    cities = store.cities_in(region_id)
    click.echo(f"Region {region_id} is used by: {', '.join(cities)}.")
    users = _feed_users(store)
    own_feeds = [
        file
        for feed in store.feed_paths(region_id) or []
        for file in _with_sidecar(feed)
        if users.get(file) == [region_id] and file.exists()
    ]
    region_dir = store.settings.region_dir(region_id)
    paths = ([region_dir] if region_dir.exists() else []) + own_feeds
    _confirm_and_remove(paths, store.settings.data_dir, yes, f"no data for {region_id}")


@cli.command("export-static")
@click.argument("cities", nargs=-1)
@click.option("--all", "export_all", is_flag=True, help="Every city with data.")
@click.option(
    "--start",
    type=click.DateTime(["%Y-%m-%d"]),
    help="First service date to include. Default: today.",
)
@click.option("--days", type=click.IntRange(1, 60), default=7, show_default=True)
@click.option(
    "-o",
    "--output",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("site"),
    show_default=True,
    help="Directory to write (replaced if it exists).",
)
def export_static(
    cities: tuple[str, ...],
    export_all: bool,
    start: datetime | None,
    days: int,
    output: Path,
) -> None:
    """Write a static, offline-capable copy of the web map (e.g. for GitHub Pages).

    Routing then runs in the browser on the exported timetables, which only cover --days days
    from --start: export again to move the window.
    """
    from mapnificently.providers import load_cities  # noqa: PLC0415
    from mapnificently.static_site import export_static_site  # noqa: PLC0415
    from mapnificently.timetable import CityDataStore  # noqa: PLC0415

    settings = get_settings()
    store = CityDataStore(settings, load_cities(settings))
    ready = [c for c in store.cities if store.is_ready(c)]
    selected = ready if export_all else list(cities)
    if not selected:
        msg = f"name a city or use --all (with data: {', '.join(ready) or 'none'})"
        raise click.UsageError(msg)
    missing = [c for c in selected if c not in ready]
    if missing:
        msg = f"no data for {', '.join(missing)}: run `mapnificently data fetch` first"
        raise click.UsageError(msg)

    first = start.date() if start else date.today()  # noqa: DTZ011
    dates = [first + timedelta(days=i) for i in range(days)]
    try:
        summary = export_static_site(store, selected, dates, output)
    except FileExistsError as exc:
        raise click.ClickException(str(exc)) from exc
    for region_id, sizes in summary["regions"].items():
        counts = summary["connections"][region_id]
        empty = [day for day, count in counts.items() if count == 0]
        members = ", ".join(c for c in selected if store.region_of(c).id == region_id)
        click.echo(
            f"{region_id} ({members}): {len(sizes) - 1} days, {sum(sizes.values()) / 1e6:.1f} MB"
        )
        if empty:
            click.echo(f"  warning: no trips on {', '.join(empty)} (outside the feed's dates?)")
    click.echo(f"wrote {output}/ ({summary['total_bytes'] / 1e6:.1f} MB)")


@cli.command()
@click.pass_context
def cities(ctx: click.Context) -> None:
    """List cities known to the API."""
    rows = [
        [
            c["id"],
            c["name"],
            c["provider"],
            ",".join(c["feeds"]),
            "yes" if c["data_ready"] else "no",
        ]
        for c in _api(ctx).request("GET", "/api/cities")
    ]
    _table(rows, ["id", "name", "provider", "feeds", "data"])


@cli.command()
@click.argument("city")
@click.argument("text")
@click.option("--limit", default=8, show_default=True)
@click.pass_context
def geocode(ctx: click.Context, city: str, text: str, limit: int) -> None:
    """Search places in CITY."""
    hits = _api(ctx).request(
        "GET", f"/api/cities/{city}/geocode", params={"q": text, "limit": limit}
    )
    _table(
        [[h["label"], h["kind"], f"{h['lat']:.5f},{h['lon']:.5f}"] for h in hits],
        ["place", "kind", "lat,lon"],
    )


@cli.command()
@click.argument("city")
@with_trip_options
@click.option("--minutes", default=30, show_default=True, type=click.IntRange(1, MAX_MINUTES))
@click.option("--top", default=20, show_default=True, help="Rows to show (0 = all).")
@click.option("--json", "as_json", is_flag=True, help="Print the raw API response.")
@click.pass_context
def reach(
    ctx: click.Context,
    city: str,
    origins: tuple[str, ...],
    departure: datetime | None,
    walk_speed: float,
    max_walk: float,
    min_change: int,
    minutes: int,
    top: int,
    as_json: bool,
) -> None:
    """Stops reachable within --minutes. Shows the --top stops reached last (the area's edge)."""
    api = _api(ctx)
    body = {
        "origins": _resolve_origins(api, city, origins),
        "departure": _departure(departure),
        "walk_speed_mps": walk_speed,
        "max_walk_m": max_walk,
        "min_change_min": min_change,
        "max_minutes": minutes,
    }
    result = api.request("POST", f"/api/cities/{city}/reach", json=body)
    if as_json:
        click.echo(json.dumps(result))
        return
    stops = api.request("GET", f"/api/cities/{city}/stops")
    click.echo(f"Departure {result['departure']}  ({result['computation_ms']} ms)", err=True)
    for origin in result["origins"]:
        pairs = list(zip(origin["stop_idx"], origin["arrival_s"], strict=True))
        click.echo(
            f"\nFrom {origin['origin']['lat']:.5f},{origin['origin']['lon']:.5f}: "
            f"{len(pairs)} stops within {minutes} min"
        )
        shown = pairs[-top:] if top else pairs
        _table(
            [[stops["names"][i], stops["ids"][i], f"{t // 60}:{t % 60:02d}"] for i, t in shown],
            ["stop", "id", "min:s"],
        )


@cli.command()
@click.argument("city")
@with_trip_options
@click.option("--minutes", default=30, show_default=True, type=click.IntRange(1, MAX_MINUTES))
@click.option(
    "--mode",
    type=click.Choice(["union", "intersection"]),
    default="union",
    show_default=True,
    help="With several --from: anywhere one can reach, or only where all can.",
)
@click.option(
    "-o",
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Write GeoJSON here instead of stdout.",
)
@click.pass_context
def isochrone(
    ctx: click.Context,
    city: str,
    origins: tuple[str, ...],
    departure: datetime | None,
    walk_speed: float,
    max_walk: float,
    min_change: int,
    minutes: int,
    mode: str,
    output: Path | None,
) -> None:
    """Area reachable within --minutes, as GeoJSON."""
    api = _api(ctx)
    body = {
        "origins": _resolve_origins(api, city, origins),
        "departure": _departure(departure),
        "walk_speed_mps": walk_speed,
        "max_walk_m": max_walk,
        "min_change_min": min_change,
        "minutes": minutes,
        "mode": mode,
    }
    result = api.request("POST", f"/api/cities/{city}/isochrone", json=body)
    text = json.dumps(result)
    if output is None:
        click.echo(text)
        return
    output.write_text(text)
    features = result["features"]
    area = features[0]["properties"]["area_km2"] if features else 0
    click.echo(f"wrote {output} ({area} km²)", err=True)


def _hhmm(iso: str) -> str:
    return iso[11:16]


def _minutes(seconds: int) -> str:
    return f"{round(seconds / 60)} min"


def _describe_leg(leg: dict[str, Any], dest_label: str) -> str:
    if leg["mode"] == "walk":
        if leg["from_name"] == leg["to_name"]:
            return f"walk {leg['distance_m']} m to another platform at {leg['to_name']}"
        to = dest_label if leg["to_name"] == "Destination" else leg["to_name"]
        return f"walk {leg['distance_m']} m to {to}"
    towards = f" towards {leg['headsign']}" if leg["headsign"] else ""
    stops = leg["num_stops"]
    return (
        f"{leg['mode']} {leg['line']}{towards}: {leg['from_name']} → {leg['to_name']}"
        f" ({stops} stop{'s' if stops != 1 else ''})"
    )


@cli.command()
@click.argument("city")
@with_trip_options
@click.option("--to", "destination", required=True, help='Destination: "lat,lon" or a place.')
@click.option("--json", "as_json", is_flag=True, help="Print the raw API response.")
@click.pass_context
def route(
    ctx: click.Context,
    city: str,
    origins: tuple[str, ...],
    departure: datetime | None,
    walk_speed: float,
    max_walk: float,
    min_change: int,
    destination: str,
    as_json: bool,
) -> None:
    """Fastest journey from each --from to --to: every walk, wait and ride."""
    api = _api(ctx)
    starts = [_resolve_place(api, city, value) for value in origins]
    dest, dest_label = _resolve_place(api, city, destination)
    body = {
        "origins": [point for point, _ in starts],
        "destination": dest,
        "departure": _departure(departure),
        "walk_speed_mps": walk_speed,
        "max_walk_m": max_walk,
        "min_change_min": min_change,
    }
    result = api.request("POST", f"/api/cities/{city}/route", json=body)
    if as_json:
        click.echo(json.dumps(result))
        return
    click.echo(
        f"Departure {result['departure'][:16].replace('T', ' ')}, "
        f"at least {result['min_change_min']} min to change ({result['computation_ms']} ms)",
        err=True,
    )
    for (_, label), journey in zip(starts, result["journeys"], strict=True):
        click.echo(f"\nFrom {label} to {dest_label}: ", nl=False)
        if not journey["reachable"]:
            click.echo(f"not reachable within {MAX_MINUTES} min")
            continue
        changes = journey["num_changes"]
        click.echo(
            f"{_minutes(journey['duration_s'])}, {changes} change{'s' if changes != 1 else ''}, "
            f"arrive {_hhmm(journey['arrive'])}"
        )
        for leg in journey["legs"]:
            wait = f"   (wait {_minutes(leg['wait_s'])})" if leg["wait_s"] >= 60 else ""
            times = f"{_hhmm(leg['depart'])}-{_hhmm(leg['arrive'])}"
            click.echo(f"  {times}  {_describe_leg(leg, dest_label)}{wait}")
    if len(starts) > 1:
        fastest, everyone = result["fastest_s"], result["everyone_s"]
        summary = f"fastest {_minutes(fastest)}" if fastest is not None else "nobody gets there"
        if everyone is not None:
            summary += f", everyone there after {_minutes(everyone)}"
        elif fastest is not None:
            summary += ", not everyone can get there"
        click.echo(f"\n{summary.capitalize()}.")
