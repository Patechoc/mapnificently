# Mapnificently

Where can you get by public transport in a given time? Mapnificently is a Python take on the
original [Mapnificent](https://www.mapnificent.net/). Pick a city, click one or more start points
on the map and drag the time slider. Everything you can reach by transit plus walking stays clear,
and the rest of the map is greyed out.

- **Web map** (Leaflet), served by the API:
  - several start points
  - "where anyone can get" (union) or "where everyone can get" (intersection)
  - a slider that redraws instantly
  - address search
  - shareable URLs
- **HTTP API** (FastAPI, OpenAPI docs at `/docs`)
- **CLI** (click) for the API and for managing data
- **Pluggable data providers**: a city with a GTFS feed takes one config block

Configured cities: **Oslo, Bergen, Trondheim and Moss**. Their data comes from
[Entur](https://developer.entur.org/), Norway's national journey planner, under the NLOD licence.

## Quick start

```bash
uv sync --all-extras

uv run mapnificently data fetch trondheim     # ~40 MB, a few seconds
uv run mapnificently data fetch --all         # all cities, ~360 MB on disk
uv run mapnificently serve                    # http://127.0.0.1:8000
```

Open http://127.0.0.1:8000, click the map and move the slider.

## CLI

`serve` and `data` run locally. The other commands call the API (`--api-url`, or
`MAPNIFICENTLY_API_URL`, default `http://127.0.0.1:8000`). A `--from` can be `lat,lon` or a place
name, which is looked up with the city's geocoder.

```bash
mapnificently data status                      # which cities have data, service dates
mapnificently cities
mapnificently geocode moss "Moss stasjon"
mapnificently reach moss --from "Moss stasjon" --at 2026-10-22T07:30 --minutes 45
mapnificently isochrone oslo --from Jernbanetorget --minutes 30 -o oslo-30.geojson
mapnificently isochrone oslo --from Majorstuen --from "Oslo lufthavn" \
    --minutes 60 --mode intersection -o meet.geojson
```

The GeoJSON output can be opened in QGIS or on [geojson.io](https://geojson.io).

## API

| Method | Path | What |
|---|---|---|
| GET | `/api/cities` | Configured cities and whether their data is ready |
| GET | `/api/cities/{city}/status` | Feeds, number of stops and trips, service dates |
| POST | `/api/cities/{city}/refresh` | Re-download and rebuild the data, in the background |
| GET | `/api/cities/{city}/stops` | All stops, as parallel arrays |
| GET | `/api/cities/{city}/geocode?q=` | Address and stop search |
| POST | `/api/cities/{city}/reach` | Earliest arrival at every reachable stop, per origin |
| POST | `/api/cities/{city}/isochrone` | Reachable area as GeoJSON (union or intersection) |

A departure without a timezone is read in the city's local time. Errors are `404` for an unknown
city, `409` when the city's data hasn't been fetched yet, and `422` for an invalid request.

## How it works

```
GTFS zips ─▶ providers/  download, skip unchanged (ETag / Last-Modified)
          ─▶ gtfs/       polars: keep stops in the city bbox, merge feeds, write Parquet
          ─▶ timetable/  one service day = vehicle hops sorted by departure (cached .npz)
                         + walking links between stops less than 400 m apart
          ─▶ routing/    Connection Scan Algorithm: earliest arrival at every stop
          ─▶ isochrone/  circle of remaining walking time around each stop, union / intersection
```

This is the original Mapnificent model. Around a stop you reach after `t` minutes, you can still
walk for `T − t` minutes, so the reachable area is the union of those circles. Walking distance
is taken as 1.2 × the straight-line distance. The web map draws the circles itself from the
`/reach` response, so moving the slider doesn't need a new request.

Typical timings on a laptop:

| City | Query | Time |
|---|---|---|
| Oslo (Ruter + Vy, about 440k connections a day) | `/reach` with a 120-minute window | about 10–20 ms |
| Oslo | 30-minute `/isochrone` polygon | about 70 ms |

## Adding a city

A city that publishes GTFS takes one block in `src/mapnificently/cities.toml`. You can also put
the block in your own file and point `MAPNIFICENTLY_CITIES_FILE` at it; a block with the same id
overrides the packaged one.

```toml
[cities.helsinki]
name = "Helsinki"
timezone = "Europe/Helsinki"
center = [60.1699, 24.9384]               # lat, lon: where the map opens
bbox = [24.50, 60.05, 25.30, 60.40]       # lon_min, lat_min, lon_max, lat_max
provider = "gtfs"                         # plain GTFS URLs (or local paths)
feeds = ["https://infopalvelut.storage.hsldev.com/gtfs/hsl.zip"]
attribution = "Timetables: HSL (CC BY 4.0)"
```

Then run `mapnificently data fetch helsinki`. For Norwegian cities, use `provider = "entur"` and
list Entur codespaces as feeds, for example `rut` for Ruter, `vyg` for Vy trains, `sky` for Skyss,
`atb` for AtB and `ost` for Østfold. Feeds are merged, and stops shared between feeds are counted
once.

## Adding a provider

A provider decides how a city's GTFS is obtained and, optionally, how places are searched:

```python
from mapnificently.providers import TransitProvider

class MyCityProvider(TransitProvider):
    def fetch_feeds(self, city, dest_dir, *, force=False):
        ...  # write GTFS zip(s) into dest_dir and return their paths

    def geocode(self, city, text, limit=8):
        ...  # optional: return a list of mapnificently.models.Place
```

There are two ways to register a provider:
- Add it to `BUILTIN_PROVIDERS` in `providers/registry.py`.
- In a separate package, declare an entry point. Cities then use `provider = "mycity"`.

  ```toml
  [project.entry-points."mapnificently.providers"]
  mycity = "my_package.providers:MyCityProvider"
  ```

If a source has no GTFS, for example a REST API, its provider converts the data to GTFS files
inside `fetch_feeds`, and the rest of the pipeline stays the same.

## Configuration

Settings are read from environment variables or a `.env` file:

| Variable | Default | |
|---|---|---|
| `MAPNIFICENTLY_DATA_DIR` | `~/.cache/mapnificently` | Downloaded feeds and prepared data |
| `MAPNIFICENTLY_CITIES_FILE` | – | Extra cities, or overrides of packaged ones |
| `MAPNIFICENTLY_ENTUR_CLIENT_NAME` | `mapnificently-dev` | Sent to Entur as `ET-Client-Name`. Use `<organisation>-<application>`, never a personal email |
| `MAPNIFICENTLY_API_URL` | `http://127.0.0.1:8000` | Used by the CLI |

## Known limitations

- **Timetables only.** Real-time delays and cancellations are ignored.
- **No ferry to Horten from Moss.** The Moss–Horten ferry is not in the `ost` or `vyg` feeds.
- **Simplified transfers.** The router changes vehicles with no minimum transfer time, and
  `transfers.txt` is not used.
- **Approximate walking.** Distances are straight lines × 1.2, not routed along streets. Water and
  motorways are not taken into account.
- **Shared tile server.** The web map uses the public OpenStreetMap tiles. Before publishing
  widely, switch to a tile provider that allows that traffic.

## Development

```bash
uv sync --all-extras
uv run pytest                        # unit tests on a synthetic GTFS feed
uv run pytest --run-integration      # also downloads real Entur data (~40 MB)
uv run ruff check . && uv run ruff format --check .
uv run mypy src/
```

## Licence and data

The code is MIT-licensed and was written with AI assistance (Claude).
Timetables: © Entur AS and the transport operators, under the
[NLOD](https://data.norge.no/nlod/en/2.0) licence. Map tiles: © OpenStreetMap contributors.
