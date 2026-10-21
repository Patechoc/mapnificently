"""HTTP API (OpenAPI docs at ``/docs``) and the web map at ``/``."""

from datetime import date, datetime
from importlib import resources
from pathlib import Path
from typing import Annotated, Any

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from mapnificently import __version__, engine
from mapnificently.models import (
    AppConfig,
    CityConfig,
    CityInfo,
    CityStatus,
    FeedStatus,
    IsochroneRequest,
    Place,
    ReachRequest,
    ReachResponse,
    RouteRequest,
    RouteResponse,
    StopsResponse,
)
from mapnificently.models.api import MAX_MINUTES
from mapnificently.providers import ProviderError, load_cities
from mapnificently.settings import Settings, get_settings
from mapnificently.timetable import CityDataStore, DataNotReadyError, cached_dates

WEB_DIR = Path(str(resources.files("mapnificently").joinpath("web")))


def create_app(settings: Settings | None = None, store: CityDataStore | None = None) -> FastAPI:
    """Build the application. Tests pass their own settings or store."""
    settings = settings or get_settings()
    store = store or CityDataStore(settings, load_cities(settings))

    app = FastAPI(
        title="Mapnificently",
        version=__version__,
        description="Where can you get by public transport in a given time? "
        "A Python take on the original Mapnificent.",
    )
    app.state.store = store

    def get_store(request: Request) -> CityDataStore:
        result: CityDataStore = request.app.state.store
        return result

    StoreDep = Annotated[CityDataStore, Depends(get_store)]

    def get_city(city_id: str, store: StoreDep) -> CityConfig:
        try:
            return store.cities[city_id]
        except KeyError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown city {city_id!r}") from None

    CityDep = Annotated[CityConfig, Depends(get_city)]

    @app.exception_handler(DataNotReadyError)
    async def _not_ready(_: Request, exc: DataNotReadyError) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc)})

    def city_info(city: CityConfig, store: CityDataStore) -> CityInfo:
        return CityInfo(
            **city.model_dump(exclude={"region"}),
            region=city.region.id,
            region_name=city.region.name,
            data_ready=store.is_ready(city.id),
        )

    @app.get("/api/config", tags=["cities"])
    def config() -> AppConfig:
        """Limits the web map adapts to, such as the longest travel time."""
        return AppConfig(max_minutes=MAX_MINUTES)

    @app.get("/api/cities", tags=["cities"])
    def list_cities(store: StoreDep) -> list[CityInfo]:
        """All configured cities."""
        return [city_info(c, store) for c in store.cities.values()]

    @app.get("/api/cities/{city_id}", tags=["cities"])
    def get_city_info(city: CityDep, store: StoreDep) -> CityInfo:
        """One city."""
        return city_info(city, store)

    @app.get("/api/cities/{city_id}/status", tags=["data"])
    def city_status(city: CityDep, store: StoreDep) -> CityStatus:
        """Whether data is prepared, from which feeds, and the dates it covers."""
        if not store.is_ready(city.id):
            return CityStatus(city=city.id, data_ready=False)
        meta = store.meta(city.id)
        return CityStatus(
            city=city.id,
            data_ready=True,
            feeds=[FeedStatus(**f) for f in meta["feeds"]],
            prepared_at=datetime.fromisoformat(meta["prepared_at"]),
            num_stops=meta["num_stops"],
            num_trips=meta["num_trips"],
            service_start=date.fromisoformat(meta["service_start"])
            if meta["service_start"]
            else None,
            service_end=date.fromisoformat(meta["service_end"]) if meta["service_end"] else None,
            cached_dates=cached_dates(store.prepared_dir(city.id)),
        )

    @app.post(
        "/api/cities/{city_id}/refresh",
        tags=["data"],
        status_code=status.HTTP_202_ACCEPTED,
    )
    def refresh_city(
        city: CityDep, store: StoreDep, background: BackgroundTasks, force: bool = False
    ) -> dict[str, str]:
        """Download the feeds of the city's region again and rebuild it, in the background.

        Every city of the same region gets the new data.
        """
        background.add_task(store.refresh, city.id, force=force)
        return {"status": "accepted", "city": city.id}

    @app.get("/api/cities/{city_id}/stops", tags=["transit"])
    def city_stops(city: CityDep, store: StoreDep) -> StopsResponse:
        """All stops, as parallel arrays. Reach results refer to them by index."""
        network = store.network(city.id)
        return StopsResponse(
            city=city.id,
            ids=network.ids,
            names=network.names,
            lat=network.lat.round(6).tolist(),
            lon=network.lon.round(6).tolist(),
        )

    @app.get("/api/cities/{city_id}/geocode", tags=["transit"])
    def geocode(
        city: CityDep,
        store: StoreDep,
        q: Annotated[str, Query(min_length=2, max_length=200)],
        limit: Annotated[int, Query(ge=1, le=20)] = 8,
    ) -> list[Place]:
        """Search addresses and stops in the city."""
        try:
            return store.provider(city.id).geocode(city, q, limit)
        except ProviderError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    @app.post("/api/cities/{city_id}/reach", tags=["routing"])
    def reach(city: CityDep, store: StoreDep, body: ReachRequest) -> ReachResponse:
        """Earliest arrival at every stop reachable from each origin.

        The web map draws its circles from this, so moving the time slider needs no new request.
        """
        return engine.reach(store, city, body)

    @app.post("/api/cities/{city_id}/route", tags=["routing"])
    def route(city: CityDep, store: StoreDep, body: RouteRequest) -> RouteResponse:
        """Fastest journey from each origin to the destination: every walk, wait and ride.

        With several origins, ``fastest_s`` is the quickest journey and ``everyone_s`` the time
        when the last one arrives (null if an origin cannot get there within ``max_minutes``).
        """
        return engine.route(store, city, body)

    @app.post("/api/cities/{city_id}/isochrone", tags=["routing"])
    def isochrone(city: CityDep, store: StoreDep, body: IsochroneRequest) -> dict[str, Any]:
        """Reachable area as a GeoJSON FeatureCollection."""
        return engine.isochrone(store, city, body)

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    return app
