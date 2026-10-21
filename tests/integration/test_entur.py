"""Real data from Entur. Run with ``pytest --run-integration`` (downloads about 40 MB)."""

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from mapnificently import engine
from mapnificently.models import LatLon, Place, ReachRequest
from mapnificently.providers import load_cities
from mapnificently.settings import Settings
from mapnificently.timetable import CityDataStore

TRONDHEIM_S = LatLon(lat=63.4365, lon=10.3990)

pytestmark = pytest.mark.integration


def next_weekday() -> date:
    day = date.today() + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


@pytest.fixture(scope="module")
def trondheim(tmp_path_factory: pytest.TempPathFactory) -> CityDataStore:
    data_dir = Path(tmp_path_factory.mktemp("entur"))
    settings = Settings(data_dir=data_dir)
    store = CityDataStore(settings, load_cities(settings))
    store.refresh("trondheim")
    return store


def test_reach_from_trondheim_s(trondheim: CityDataStore):
    city = trondheim.cities["trondheim"]
    departure = datetime.combine(next_weekday(), datetime.min.time()).replace(hour=8)
    response = engine.reach(
        trondheim, city, ReachRequest(origins=[TRONDHEIM_S], departure=departure, max_minutes=30)
    )
    assert len(response.origins[0].stop_idx) > 100


def test_entur_geocoder(trondheim: CityDataStore):
    city = trondheim.cities["trondheim"]
    hits: list[Place] = trondheim.provider("trondheim").geocode(city, "Trondheim S")
    assert hits
    assert city.bbox.contains(hits[0].lat, hits[0].lon)
