"""Command line interface.

``serve`` and ``data`` work locally; the other commands talk to a running API
(``--api-url`` or ``MAPNIFICENTLY_API_URL``).
"""

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import click
import httpx

from mapnificently import __version__
from mapnificently.models.api import DEFAULT_MIN_CHANGE_MIN, MAX_MINUTES
from mapnificently.settings import get_settings

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
    """Download and prepare timetable data (runs locally, no server needed)."""


@data.command("fetch")
@click.argument("cities", nargs=-1)
@click.option("--all", "fetch_all", is_flag=True, help="Every configured city.")
@click.option("--force", is_flag=True, help="Download even if unchanged.")
def data_fetch(cities: tuple[str, ...], fetch_all: bool, force: bool) -> None:
    """Download feeds for CITIES and prepare them for routing."""
    from mapnificently.providers import ProviderError, load_cities  # noqa: PLC0415
    from mapnificently.timetable import CityDataStore  # noqa: PLC0415

    settings = get_settings()
    known = load_cities(settings)
    selected = list(known) if fetch_all else list(cities)
    if not selected:
        msg = f"name a city or use --all (known: {', '.join(known)})"
        raise click.UsageError(msg)
    unknown = [c for c in selected if c not in known]
    if unknown:
        msg = f"unknown cities: {', '.join(unknown)} (known: {', '.join(known)})"
        raise click.UsageError(msg)

    store = CityDataStore(settings, known)
    for city_id in selected:
        click.echo(f"{city_id}: fetching {', '.join(known[city_id].feeds)} …")
        try:
            meta = store.refresh(city_id, force=force)
        except ProviderError as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(
            f"{city_id}: {meta['num_stops']} stops, {meta['num_trips']} trips, "
            f"service {meta['service_start']} → {meta['service_end']}"
        )


@data.command("status")
def data_status() -> None:
    """Show which cities have data."""
    from mapnificently.providers import load_cities  # noqa: PLC0415
    from mapnificently.timetable import CityDataStore  # noqa: PLC0415

    settings = get_settings()
    store = CityDataStore(settings, load_cities(settings))
    rows = []
    for city_id in store.cities:
        if store.is_ready(city_id):
            meta = store.meta(city_id)
            rows.append(
                [
                    city_id,
                    "ready",
                    str(meta["num_stops"]),
                    f"{meta['service_start']} → {meta['service_end']}",
                    meta["prepared_at"][:16],
                ]
            )
        else:
            rows.append([city_id, "missing", "-", "-", "-"])
    _table(rows, ["city", "data", "stops", "service dates", "prepared (UTC)"])
    click.echo(f"\ndata dir: {settings.data_dir}")


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


def _describe_leg(leg: dict[str, Any]) -> str:
    if leg["mode"] == "walk":
        if leg["from_name"] == leg["to_name"]:
            return f"walk {leg['distance_m']} m to another platform at {leg['to_name']}"
        return f"walk {leg['distance_m']} m to {leg['to_name']}"
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
            click.echo(
                f"  {_hhmm(leg['depart'])}-{_hhmm(leg['arrive'])}  {_describe_leg(leg)}{wait}"
            )
    if len(starts) > 1:
        fastest, everyone = result["fastest_s"], result["everyone_s"]
        summary = f"fastest {_minutes(fastest)}" if fastest is not None else "nobody gets there"
        if everyone is not None:
            summary += f", everyone there after {_minutes(everyone)}"
        elif fastest is not None:
            summary += ", not everyone can get there"
        click.echo(f"\n{summary.capitalize()}.")
