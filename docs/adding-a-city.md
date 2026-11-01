# Adding a city or a country

**Contents**

- [The four ideas you need](#the-four-ideas-you-need)
- [Step 1: Find the timetables](#step-1-find-the-timetables)
- [Step 2: Check the files before using them](#step-2-check-the-files-before-using-them)
- [Step 3: Draw the region](#step-3-draw-the-region)
- [Step 4: Write the configuration](#step-4-write-the-configuration)
- [Step 5: Download and prepare the data](#step-5-download-and-prepare-the-data)
- [Step 6: Check the results](#step-6-check-the-results)
- [Step 7: See it on the map](#step-7-see-it-on-the-map)
- [Step 8: Keep it up to date](#step-8-keep-it-up-to-date)
- [Step 9: Share it with everyone](#step-9-share-it-with-everyone)
- [When configuration is not enough](#when-configuration-is-not-enough)
- [Troubleshooting](#troubleshooting)

This guide walks you through adding a new place to Mapnificently, from finding its timetables
to seeing it on the map. It uses **Paris** as the worked example: it is already in
[`cities.toml`](../src/mapnificently/cities.toml), so you can compare your work with a region
that is known to work.

For most cities you will not write any code: you add a few lines of configuration that point
to timetables the transport authority already publishes. Plan for about an hour the first
time, most of it spent finding the right data.

## The four ideas you need

**GTFS** is the common file format for public transport timetables: a zip file of CSV tables
(stops, lines, trips, the time each trip reaches each stop, the days it runs). Mapnificently
reads only GTFS. Thousands of transport authorities publish it, and the rest of the app (routing,
the reachable area, the map) works the same for every one of them.

A **region** is an area whose timetables are downloaded and prepared together. A trip can use
every operator in the region: in Paris, a metro then an SNCF train. A region has:

- one or more **feeds**: the GTFS files to download
- a **bbox**: the rectangle of the region; stops outside it are dropped
- a **provider**: how the feeds are downloaded (see below)
- a **timezone** and an **attribution** line, shown under the map

A **city** is a named place in a region: where the map opens. Several cities can share one
region (Oslo and Moss share the Oslofjord region), and they then reach each other. A city also
has a **country**, which the web map uses to filter its city list.

A **provider** knows how to download a region's feeds. Two come with the app:

| Provider | Feeds are                              | Place search uses                                   | Use it for        |
| -------- | -------------------------------------- | --------------------------------------------------- | ----------------- |
| `gtfs`   | GTFS zip URLs (or local files)         | [Photon](https://photon.komoot.io/) (OpenStreetMap) | almost everywhere |
| `entur`  | Entur codespaces, e.g. `rut` for Ruter | Entur's own search                                  | Norway            |

> **Country belongs to the city, not the region.** A region can cross a border (think of
> Geneva and the French towns around it). Its cities then reach each other whatever their
> country: the country menu only narrows the list of cities, never what a trip can use.

## Step 1: Find the timetables

You are looking for **static GTFS** (timetables), not "GTFS-RT" or "SIRI" (live delays, which
Mapnificently does not use) and not only "NeTEx" (another timetable format the app cannot read).

Look in this order:

1. **The country's national access point.** Every EU and EEA country must run one, and many
   other countries have one too. Examples: [transport.data.gouv.fr](https://transport.data.gouv.fr)
   (France), [Entur](https://developer.entur.org) (Norway),
   [opentransportdata.swiss](https://opentransportdata.swiss) (Switzerland), [gtfs.de](https://gtfs.de) (Germany).
2. **The city's transport authority**, on its "open data" or "developers" page.
3. **A catalogue of feeds**: the [Mobility Database](https://mobilitydatabase.org) and
   [Transitland](https://www.transit.land) list feeds from all over the world, each with its
   download URL.

Make a list of **every operator** people actually use in the area. One feed is rarely enough.
For Paris:

| Feed                                      | Covers                                                        | Found on               |
| ----------------------------------------- | ------------------------------------------------------------- | ---------------------- |
| `IDFM-gtfs.zip`                           | Île-de-France Mobilités: metro, RER, Transilien, trams, buses | transport.data.gouv.fr |
| `Export_OpenData_SNCF_GTFS_NewTripId.zip` | SNCF national trains: TGV, Intercités, TER                    | transport.data.gouv.fr |

Without the second feed, trips would stop at the edge of Île-de-France: no train to Rouen.

### In Norway: use Entur codes instead of URLs

Entur publishes every Norwegian operator's timetables, one feed per operator. With
`provider = "entur"` you list the operators' short **codespace** codes, and the app builds the
URLs itself:

| Code  | Operator                    | Code  | Operator         |
| ----- | --------------------------- | ----- | ---------------- |
| `rut` | Ruter (Oslo, Akershus)      | `sky` | Skyss (Bergen)   |
| `vyg` | Vy trains                   | `atb` | AtB (Trondheim)  |
| `ost` | Østfold kollektivtrafikk    | `flt` | Flytoget         |
| `bra` | Brakar (Buskerud)           | `goa` | Go-Ahead trains  |
| `vkt` | Vestfold og Telemark        | `sjn` | SJ trains        |

The full list is on [Entur's developer site](https://developer.entur.org). Entur gives a stop
the same id in every feed, so a station served by Ruter and Vy becomes one stop in the app.

## Step 2: Check the files before using them

For each feed, answer these questions before going further:

| Question                                                  | Why it matters                                      | Paris                          |
| --------------------------------------------------------- | --------------------------------------------------- | ------------------------------ |
| Is there a **direct URL** to the zip, without logging in? | The app downloads it without you                    | Yes, both                      |
| Does the URL **stay the same** when the data is updated?  | Otherwise your configuration breaks at every update | Yes ("-last", "IDFM-gtfs.zip") |
| What is the **licence**?                                  | You must respect it and credit the publisher        | ODbL, credit IDFM and SNCF     |
| **How far ahead** does it go?                             | Tells you how often to refresh (step 8)             | IDFM: 3 weeks. SNCF: 5 months  |
| Does it cover the **operators** on your list?             | Missing operators mean missing trips                | Yes, together                  |

You can check that a URL answers, and how big the file is, without downloading it:

```bash
curl -sIL https://eu.ftp.opendatasoft.com/stif/GTFS/IDFM-gtfs.zip | grep -iE "^(HTTP|content-length|etag|last-modified)"
```

```
HTTP/2 200
content-length: 143102162
last-modified: Sat, 31 Oct 2026 06:23:57 GMT
etag: "6ae5897d-88790d2"
```

`HTTP/2 200` means the file is there. `content-length` is its size in bytes (143 MB here).
`etag` or `last-modified` are a bonus: the app uses them to skip downloads when nothing changed.

> **If the publisher asks you to accept a licence or create an account**, do it once, even if
> the direct URL works without it. IDFM asks for this on its own portal.

## Step 3: Draw the region

Decide the **bbox**: the rectangle the region covers, written
`[lon_min, lat_min, lon_max, lat_max]` (longitude first, west to east, then south to north).
Stops outside are dropped, so the bbox is also how you keep a national feed (like SNCF's) from
pulling in the whole country.

The easiest way is to draw it on [bboxfinder.com](http://bboxfinder.com) and copy the numbers.
Then think about **how far people travel**: the map's slider goes up to 220 minutes, so include
the places reachable in that time that you care about.

For Paris, Île-de-France alone is about `[1.44, 48.12, 3.56, 49.24]`. The region was widened to
`[0.90, 47.80, 4.20, 49.95]` to take in Rouen, Chartres, Orléans, Reims and Amiens.

Two things to weigh:

- **Outside the city's own network, only the trains are known.** The Paris region reaches
  Rouen station, but not Rouen's buses: their feed is not included.
- **A bigger bbox costs memory.** Paris (36,000 stops) needs about 4 GB to prepare and 1 GB to
  serve. Oslofjord (16,000 stops) needs far less.

Also note the **timezone** of the region, from the
[list of tz database names](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones):
`Europe/Paris`, `Europe/Oslo`, `America/New_York`...

## Step 4: Write the configuration

You can put your region in either of two places:

- **Your own file**, for trying things out or for a private setup. Point the
  `MAPNIFICENTLY_CITIES_FILE` environment variable to it. Its blocks are added to the packaged
  ones, and replace them when they have the same id, regions included. For example, to add an
  operator to Oslofjord, copy its `[regions.oslofjord]` block into your file and extend `feeds`:
  Oslo and Moss follow.
- **The packaged [`src/mapnificently/cities.toml`](../src/mapnificently/cities.toml)**, to make it
  part of the app for everyone (step 9).

Start with your own file, say `~/my-cities.toml`. Here is the Paris region, with every line
explained:

```toml
[regions.paris]                     # "paris" is the region id: lowercase, digits, - or _
name = "Paris and Île-de-France"    # shown in messages
timezone = "Europe/Paris"           # timetables are read in this timezone
bbox = [0.90, 47.80, 4.20, 49.95]   # lon_min, lat_min, lon_max, lat_max (step 3)
provider = "gtfs"                   # plain GTFS URLs
feeds = [                           # every feed of step 1, in any order
    "https://eu.ftp.opendatasoft.com/stif/GTFS/IDFM-gtfs.zip",
    "https://eu.ftp.opendatasoft.com/sncf/plandata/Export_OpenData_SNCF_GTFS_NewTripId.zip",
]
attribution = "Timetables: Île-de-France Mobilités / SNCF Voyageurs (ODbL)"   # licence credit

[cities.paris]                      # the city id, used in URLs and commands
name = "Paris"                      # shown in the city menu
country = "FR"                      # two-letter ISO 3166 code: FR, NO, DE, CH, US...
region = "paris"                    # the region above
center = [48.8566, 2.3522]          # lat, lon: where the map opens (latitude first here!)
```

> **Watch the order of coordinates.** `bbox` is longitude first, `center` is latitude first.
> Swapping them is the most common mistake; the app refuses a bbox whose minimum is larger than
> its maximum, but a swapped `center` silently opens the map in the wrong place.

To add **another city to the same region**, add only a city block. It shares the region's data
and reaches everything the region contains:

```toml
[cities.versailles]
name = "Versailles"
country = "FR"
region = "paris"
center = [48.8049, 2.1204]
```

A **city on its own** can skip the region block: give it the region fields (`timezone`, `bbox`,
`provider`, `feeds`, `attribution`) directly, and it becomes a region of its own, with the same
id. This is the shortest way to add a place served by a single feed:

```toml
[cities.helsinki]
name = "Helsinki"
country = "FI"
timezone = "Europe/Helsinki"
center = [60.1699, 24.9384]
bbox = [24.50, 60.05, 25.30, 60.40]
provider = "gtfs"
feeds = ["https://infopalvelut.storage.hsldev.com/gtfs/hsl.zip"]
attribution = "Timetables: HSL (CC BY 4.0)"
```

Once you want a second city there, move those fields into a `[regions.helsinki]` block, as for
Paris.

To add **a whole new country**, nothing more is needed: give its cities the new `country` code.
The web map shows a **Country** menu as soon as cities come from two or more countries, with the
country names in the visitor's language.

Check that the configuration loads:

```bash
export MAPNIFICENTLY_CITIES_FILE=~/my-cities.toml
mapnificently cities
```

```
id         name       region     data
---------  ---------  ---------  ----
oslo       Oslo       oslofjord  yes
...
paris      Paris      paris      no
```

Your city is listed, with `no` data yet. A mistake in the file (a missing field, an unknown
timezone, a country written `"France"` instead of `"FR"`) stops this command with an error. The
last lines name the field and the reason:

```
pydantic_core._pydantic_core.ValidationError: 1 validation error for CityConfig
country
  String should match pattern '^[A-Z]{2}$' [type=string_pattern_mismatch, input_value='FRANCE', input_type=str]
```

## Step 5: Download and prepare the data

```bash
mapnificently data fetch paris      # or: make data-fetch CITY=paris
```

```
paris (paris): fetching https://eu.ftp.opendatasoft.com/stif/GTFS/IDFM-gtfs.zip, https://eu.ftp.opendatasoft.com/sncf/plandata/Export_OpenData_SNCF_GTFS_NewTripId.zip …
paris: 35989 stops, 542816 trips, service 2026-10-28 → 2027-03-31
```

This downloads every feed, keeps the stops inside the bbox, and prepares the tables the router
uses. For Paris it takes about 20 seconds. Then look at the summary:

```bash
mapnificently data status
```

```
region     cities      data   stops  service dates            prepared (UTC)    size
---------  ----------  -----  -----  -----------------------  ----------------  --------
paris      paris       ready  35989  2026-10-28 → 2027-03-31  2026-10-31T23:28  115.4 MB
```

> **The service dates are those of the feed that lasts longest.** Here the end date comes from
> SNCF (March 2027); IDFM's buses and metro stop at the end of November. After that date the map
> would show trains only. That is why step 8 matters.

## Step 6: Check the results

Don't trust a new region until you have checked a few trips **you know**, or compared them with
the operator's own journey planner.

First, check that place search finds things in your region:

```bash
mapnificently geocode paris "gare de lyon"
```

Then ask for routes, inside the city and towards the edges of the region:

```bash
mapnificently route paris --from "Châtelet" --to "Gare de Rouen Rive Droite" --at 2026-11-03T08:00
```

```
From Châtelet, Paris to Gare Rive Droite, Rouen: 106 min, 1 change, arrive 09:46
  08:00-08:00  walk 34 m to Châtelet
  08:00-08:04  metro 14 towards Saint-Denis - Pleyel: Châtelet → Saint-Lazare (3 stops)
  08:04-08:06  walk 110 m to Paris Saint-Lazare
  08:14-09:44  rail C1 towards 13113: Paris Saint-Lazare → Rouen Rive Droite (7 stops)   (wait 8 min)
  09:44-09:46  walk 162 m to Gare Rive Droite, Rouen
```

This one trip shows several things worth checking in your own region:

- **The feeds work together.** The metro comes from IDFM, the train from SNCF, and the app
  links their stops with a short walk (110 m at Saint-Lazare). Stops of different feeds are
  never merged unless they have the same id; walking links do the job.
- **The data has its quirks.** "towards 13113" is a train number, because SNCF's feed has no
  proper destination name. This comes from the publisher, not from the app.
- **Outside the city's own network, you need to be near a station.** Asking for just `"Rouen"`
  gives *not reachable*: Rouen's centre is more than 1 km from the station and Rouen's buses are
  not in the data. Add their feed to the region if you need them.

Use a **weekday date inside the service dates** for these tests. A date outside them, or a
public holiday, gives few or no trips.

## Step 7: See it on the map

```bash
mapnificently serve                 # or: make serve, then open http://127.0.0.1:8000
```

Pick your country in the **Country** menu and your city in the **City** menu. Then click the
map and move the slider. The status line at the bottom of the panel says how many stops were
loaded, or that the city has no data yet. If the reachable area looks too small, check the date
(step 6): the map starts on the next weekday, which may still be a public holiday or outside the
service dates.

## Step 8: Keep it up to date

Timetables expire. Look at **how far ahead** each feed goes (step 2) and refresh well before
that:

```bash
mapnificently data fetch paris          # downloads only the feeds that changed
mapnificently data fetch --all          # every region
```

For Paris, refresh at least **every week**: IDFM's feed covers only three weeks. Feeds that
cover months (most Norwegian ones, SNCF) can be refreshed monthly. On a server, run the command
from a scheduled job (cron, a CI schedule, or a new deployment: see [cloud-run.md](cloud-run.md)).

## Step 9: Share it with everyone

To make your region part of the app:

1. Move your blocks into [`src/mapnificently/cities.toml`](../src/mapnificently/cities.toml),
   under a header comment like the others, explaining which operators the feeds cover and any
   limit (how far ahead they go, what is left out).
2. Give **every** city a `country`: a test checks it.
3. Add the publishers and their licence to the **Licence and data** section of the
   [README](../README.md#licence-and-data).
4. Run `make check` (lint, types and tests) and open a pull request.

## When configuration is not enough

You need to write a **provider** (some Python, see
[Adding a provider](../README.md#adding-a-provider) in the README) only when:

- the feeds need an **API key** or a special request header
- the feed URLs **change** at every update and must be looked up first (in a catalogue, for
  example)
- the data is **not GTFS**: the provider then converts it to GTFS files, and the rest of the
  app is unchanged
- you want a **different place search** than Photon, for example a national address service

Live data (delays, cancellations) and journey planners run by others are not supported by
providers: the app plans every trip itself, from the published timetables.

## Troubleshooting

| What you see                                          | Likely cause                                 | What to do                                                   |
| ----------------------------------------------------- | -------------------------------------------- | ------------------------------------------------------------ |
| `mapnificently cities` fails with a `ValidationError` | A typo or a missing field in your file       | The last lines of the error name the field                   |
| `could not download ...`                              | URL wrong, moved, or behind a login          | Open it with `curl -sIL` (step 2); look for the current URL  |
| Very few stops after `data fetch`                     | bbox too small or lon/lat swapped            | Check the bbox order: `[lon_min, lat_min, lon_max, lat_max]` |
| The map opens in the sea                              | `center` written lon, lat                    | `center` is `[lat, lon]`                                     |
| Nothing reachable on the map                          | Date outside the service dates, or a holiday | Pick a weekday inside the dates shown by `data status`       |
| Reachable area stops at a border or region edge       | An operator's feed is missing                | Add its feed to the region (step 1)                          |
| Trips only by train after some date                   | One feed expired before the others           | Refresh (step 8)                                             |
| `data fetch` is killed or very slow                   | Not enough memory for the region             | Use a smaller bbox, or prepare on a bigger machine           |
| Country menu does not appear                          | All cities have the same country             | Expected: it appears from two countries on                   |
