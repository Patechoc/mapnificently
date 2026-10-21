"""Turn one or more GTFS zips into compact Parquet tables for one city.

Only what routing needs is kept (``shapes.txt`` is never read). The output lives in a
``prepared/`` directory:

- ``stops.parquet``: stop_idx, stop_id, name, lat, lon (quays/platforms inside the bbox)
- ``trips.parquet``: trip_idx, trip_id, route_id, service_id, route_type
- ``stop_times.parquet``: trip_idx, stop_idx, seq, arr_s, dep_s, board, alight
  (sorted by trip_idx, seq; times in seconds after service-day midnight, may exceed 86400)
- ``calendar.parquet`` / ``calendar_dates.parquet``: service definitions
- ``meta.json``: feed sources, counts and service date range

Trip, route and service ids are prefixed with the feed name so feeds never clash. Stop ids
are kept as-is, so a stop shared by two feeds (Entur's national ``NSR:Quay`` ids) is merged.
"""

import io
import json
import shutil
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from mapnificently.models import BBox

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
NO_SERVICE = "1"  # pickup_type / drop_off_type value meaning "not available"


class FeedError(ValueError):
    """A GTFS feed is missing required files or columns."""


@dataclass(frozen=True)
class Feed:
    """A GTFS zip and the name used to prefix its ids."""

    name: str
    path: Path
    source: str = ""
    last_modified: str | None = None


def gtfs_time_to_seconds(column: str) -> pl.Expr:
    """``H:MM:SS`` or ``HH:MM:SS`` (hours may exceed 24) to seconds; empty stays null."""
    parts = pl.col(column).str.strip_chars().str.split(":")
    return (
        parts.list.get(0, null_on_oob=True).cast(pl.Int32, strict=False) * 3600
        + parts.list.get(1, null_on_oob=True).cast(pl.Int32, strict=False) * 60
        + parts.list.get(2, null_on_oob=True).cast(pl.Int32, strict=False)
    )


def _read_table(
    zf: zipfile.ZipFile,
    name: str,
    required: Sequence[str],
    optional: Sequence[str] = (),
) -> pl.DataFrame | None:
    """Read ``name`` from the zip as strings, or ``None`` if the file is absent."""
    member = next((n for n in zf.namelist() if Path(n).name == name), None)
    if member is None:
        return None
    raw = zf.read(member)
    df = pl.read_csv(
        io.BytesIO(raw),
        infer_schema=False,
        with_column_names=lambda cols: [c.strip().lstrip("﻿") for c in cols],
        truncate_ragged_lines=True,
    )
    missing = [c for c in required if c not in df.columns]
    if missing:
        msg = f"{name} is missing columns {missing}"
        raise FeedError(msg)
    df = df.with_columns(
        [pl.lit(None, dtype=pl.String).alias(c) for c in optional if c not in df.columns]
    )
    return df.select([*required, *optional])


def _require(df: pl.DataFrame | None, name: str) -> pl.DataFrame:
    if df is None:
        msg = f"feed has no {name}"
        raise FeedError(msg)
    return df


def _prefix(column: str, feed: str) -> pl.Expr:
    return pl.concat_str(pl.lit(f"{feed}:"), pl.col(column)).alias(column)


@dataclass
class _FeedTables:
    stops: pl.DataFrame
    trips: pl.DataFrame
    stop_times: pl.DataFrame
    calendar: pl.DataFrame
    calendar_dates: pl.DataFrame


def _load_feed(feed: Feed) -> _FeedTables:
    with zipfile.ZipFile(feed.path) as zf:
        stops = _require(
            _read_table(
                zf, "stops.txt", ["stop_id", "stop_lat", "stop_lon"], ["stop_name", "location_type"]
            ),
            "stops.txt",
        )
        routes = _require(_read_table(zf, "routes.txt", ["route_id", "route_type"]), "routes.txt")
        trips = _require(
            _read_table(zf, "trips.txt", ["route_id", "service_id", "trip_id"]), "trips.txt"
        )
        stop_times = _require(
            _read_table(
                zf,
                "stop_times.txt",
                ["trip_id", "stop_id", "stop_sequence"],
                ["arrival_time", "departure_time", "pickup_type", "drop_off_type"],
            ),
            "stop_times.txt",
        )
        calendar = _read_table(
            zf, "calendar.txt", ["service_id", *WEEKDAYS, "start_date", "end_date"]
        )
        calendar_dates = _read_table(
            zf, "calendar_dates.txt", ["service_id", "date", "exception_type"]
        )
    if calendar is None and calendar_dates is None:
        msg = "feed has neither calendar.txt nor calendar_dates.txt"
        raise FeedError(msg)

    stops = (
        stops.filter(
            pl.col("location_type").is_null()
            | pl.col("location_type").str.strip_chars().is_in(["", "0"])
        )
        .select(
            pl.col("stop_id"),
            pl.col("stop_name").fill_null("").alias("name"),
            pl.col("stop_lat").cast(pl.Float64, strict=False).alias("lat"),
            pl.col("stop_lon").cast(pl.Float64, strict=False).alias("lon"),
        )
        .drop_nulls(["lat", "lon"])
    )

    trips = trips.join(
        routes.select(
            pl.col("route_id"),
            pl.col("route_type").cast(pl.Int32, strict=False).fill_null(3),
        ),
        on="route_id",
        how="left",
    ).with_columns(
        _prefix("trip_id", feed.name),
        _prefix("route_id", feed.name),
        _prefix("service_id", feed.name),
    )

    stop_times = (
        stop_times.with_columns(
            _prefix("trip_id", feed.name),
            pl.col("stop_sequence").cast(pl.Int32, strict=False).alias("seq"),
            gtfs_time_to_seconds("arrival_time").alias("arr_s"),
            gtfs_time_to_seconds("departure_time").alias("dep_s"),
            (pl.col("pickup_type").fill_null("").str.strip_chars() != NO_SERVICE).alias("board"),
            (pl.col("drop_off_type").fill_null("").str.strip_chars() != NO_SERVICE).alias("alight"),
        )
        .with_columns(
            pl.col("arr_s").fill_null(pl.col("dep_s")),
            pl.col("dep_s").fill_null(pl.col("arr_s")),
        )
        .select("trip_id", "stop_id", "seq", "arr_s", "dep_s", "board", "alight")
        .drop_nulls(["seq", "arr_s"])
    )

    if calendar is None:
        calendar = pl.DataFrame(
            schema={
                "service_id": pl.String,
                **dict.fromkeys(WEEKDAYS, pl.Boolean),
                "start_date": pl.Date,
                "end_date": pl.Date,
            }
        )
    else:
        calendar = calendar.with_columns(
            _prefix("service_id", feed.name),
            *[(pl.col(d).str.strip_chars() == "1").alias(d) for d in WEEKDAYS],
            pl.col("start_date").str.strip_chars().str.to_date("%Y%m%d"),
            pl.col("end_date").str.strip_chars().str.to_date("%Y%m%d"),
        )
    if calendar_dates is None:
        calendar_dates = pl.DataFrame(
            schema={"service_id": pl.String, "date": pl.Date, "exception_type": pl.Int8}
        )
    else:
        calendar_dates = calendar_dates.with_columns(
            _prefix("service_id", feed.name),
            pl.col("date").str.strip_chars().str.to_date("%Y%m%d"),
            pl.col("exception_type").str.strip_chars().cast(pl.Int8),
        )

    return _FeedTables(stops, trips, stop_times, calendar, calendar_dates)


def _dates(*values: object) -> list[date]:
    return [v for v in values if isinstance(v, date)]


def prepare_city(feeds: Sequence[Feed], bbox: BBox, out_dir: Path) -> dict[str, object]:
    """Merge ``feeds``, keep what lies inside ``bbox`` and write the tables to ``out_dir``."""
    tables = [_load_feed(feed) for feed in feeds]

    stops = (
        pl.concat([t.stops for t in tables])
        .unique("stop_id", keep="first", maintain_order=True)
        .filter(
            pl.col("lat").is_between(bbox.lat_min, bbox.lat_max)
            & pl.col("lon").is_between(bbox.lon_min, bbox.lon_max)
        )
        .sort("stop_id")
        .with_row_index("stop_idx")
        .with_columns(pl.col("stop_idx").cast(pl.Int32))
    )

    stop_times = (
        pl.concat([t.stop_times for t in tables])
        .join(stops.select("stop_id", "stop_idx"), on="stop_id", how="inner")
        .drop("stop_id")
    )
    # A trip needs at least two stops inside the area to give a connection.
    trip_counts = stop_times.group_by("trip_id").len().filter(pl.col("len") >= 2)
    trips = (
        pl.concat([t.trips for t in tables])
        .unique("trip_id", keep="first")
        .join(trip_counts.select("trip_id"), on="trip_id", how="semi")
        .sort("trip_id")
        .with_row_index("trip_idx")
        .with_columns(pl.col("trip_idx").cast(pl.Int32))
        .select("trip_idx", "trip_id", "route_id", "service_id", "route_type")
    )
    stop_times = (
        stop_times.join(trips.select("trip_id", "trip_idx"), on="trip_id", how="inner")
        .select("trip_idx", "stop_idx", "seq", "arr_s", "dep_s", "board", "alight")
        .sort("trip_idx", "seq")
    )

    used_services = trips.select("service_id").unique()
    calendar = pl.concat([t.calendar for t in tables]).join(
        used_services, on="service_id", how="semi"
    )
    calendar_dates = pl.concat([t.calendar_dates for t in tables]).join(
        used_services, on="service_id", how="semi"
    )
    starts = _dates(calendar["start_date"].min(), calendar_dates["date"].min())
    ends = _dates(calendar["end_date"].max(), calendar_dates["date"].max())

    meta: dict[str, object] = {
        "prepared_at": datetime.now(UTC).isoformat(),
        "bbox": bbox.as_list(),
        "feeds": [
            {
                "name": f.name,
                "source": f.source,
                "size_bytes": f.path.stat().st_size,
                "last_modified": f.last_modified,
            }
            for f in feeds
        ],
        "num_stops": stops.height,
        "num_trips": trips.height,
        "num_stop_times": stop_times.height,
        "service_start": min(starts).isoformat() if starts else None,
        "service_end": max(ends).isoformat() if ends else None,
    }

    tmp_dir = out_dir.with_name(out_dir.name + ".tmp")
    shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True)
    stops.write_parquet(tmp_dir / "stops.parquet")
    trips.write_parquet(tmp_dir / "trips.parquet")
    stop_times.write_parquet(tmp_dir / "stop_times.parquet")
    calendar.write_parquet(tmp_dir / "calendar.parquet")
    calendar_dates.write_parquet(tmp_dir / "calendar_dates.parquet")
    (tmp_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    shutil.rmtree(out_dir, ignore_errors=True)
    tmp_dir.rename(out_dir)
    return meta


def active_services(calendar: pl.DataFrame, calendar_dates: pl.DataFrame, day: date) -> set[str]:
    """Service ids running on ``day``, following calendar.txt and its exceptions."""
    weekday = WEEKDAYS[day.weekday()]
    regular = calendar.filter(
        pl.col(weekday) & (pl.col("start_date") <= day) & (pl.col("end_date") >= day)
    )["service_id"]
    exceptions = calendar_dates.filter(pl.col("date") == day)
    added = exceptions.filter(pl.col("exception_type") == 1)["service_id"]
    removed = exceptions.filter(pl.col("exception_type") == 2)["service_id"]
    return (set(regular.to_list()) | set(added.to_list())) - set(removed.to_list())
