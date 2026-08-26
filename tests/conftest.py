from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from services.common.gbfs import parse_station, parse_status
from services.common.models import Observation, Station, WeatherPoint
from services.common.storage.sqlite import SQLiteStore

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def station_information() -> dict:
    return load_fixture("gbfs_station_information.json")


@pytest.fixture
def station_status() -> dict:
    return load_fixture("gbfs_station_status.json")


@pytest.fixture
def discovery() -> dict:
    return load_fixture("gbfs_gbfs.json")


@pytest.fixture
def store(tmp_path: Path) -> SQLiteStore:
    s = SQLiteStore(str(tmp_path / "test.sqlite"))
    s.migrate()
    yield s
    s.close()


@pytest.fixture
def sample_stations(station_information: dict) -> list[Station]:
    return [parse_station(s) for s in station_information["data"]["stations"][:5]]


@pytest.fixture
def sample_observations(station_status: dict) -> list[Observation]:
    feed_ts = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    return [parse_status(s, feed_ts) for s in station_status["data"]["stations"][:5]]


def make_series(
    store: SQLiteStore,
    stations: list[Station],
    start: datetime,
    steps: int,
    step_min: int = 5,
) -> None:
    """Write a deterministic saw-tooth series plus matching hourly weather."""
    obs: list[Observation] = []
    for st in stations:
        cap = max(st.capacity, 8)
        for i in range(steps):
            obs.append(
                Observation(
                    station_id=st.station_id,
                    ts=start + timedelta(minutes=step_min * i),
                    num_bikes=i % (cap + 1),
                    num_docks=cap - (i % (cap + 1)),
                )
            )
    store.upsert_stations(stations)
    store.insert_observations(obs)

    hours = (steps * step_min) // 60 + 2
    store.upsert_weather(
        [
            WeatherPoint(
                ts=(start + timedelta(hours=h)).replace(minute=0, second=0, microsecond=0),
                temp_c=15.0 + h % 10,
                precip_mm=0.4 if h % 7 == 0 else 0.0,
                wind_kmh=8.0,
            )
            for h in range(hours)
        ]
    )
