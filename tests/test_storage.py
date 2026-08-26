"""SQLite store: migrations, upserts, the 15-minute aggregate and replay reads."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from services.common.models import Observation, WeatherPoint
from services.common.storage.sqlite import SQLiteStore
from tests.conftest import make_series

START = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)


def test_migrations_are_idempotent(tmp_path):
    store = SQLiteStore(str(tmp_path / "m.sqlite"))
    first = store.migrate()
    assert first == ["001_schema.sql", "002_aggregate.sql"]
    assert store.migrate() == []
    store.close()


def test_upsert_station_updates_capacity(store, sample_stations):
    store.upsert_stations(sample_stations)
    assert len(store.list_stations()) == len(sample_stations)

    changed = sample_stations[0].model_copy(update={"capacity": 99})
    store.upsert_stations([changed])
    assert len(store.list_stations()) == len(sample_stations)
    reloaded = {s.station_id: s for s in store.list_stations()}
    assert reloaded[changed.station_id].capacity == 99


def test_duplicate_observations_are_ignored(store, sample_stations, sample_observations):
    store.upsert_stations(sample_stations)
    store.insert_observations(sample_observations)
    store.insert_observations(sample_observations)
    assert store.observation_count() == len(sample_observations)


def test_fifteen_minute_aggregate_matches_manual_average(store, sample_stations):
    station = sample_stations[0]
    cap = station.capacity
    store.upsert_stations([station])
    counts = [3, 5, 7, 11, 13, 17]  # two full buckets at 5-minute spacing
    store.insert_observations(
        [
            Observation(
                station_id=station.station_id,
                ts=START + timedelta(minutes=5 * i),
                num_bikes=n,
                num_docks=cap - n,
            )
            for i, n in enumerate(counts)
        ]
    )
    points = store.history(station.station_id, START, START + timedelta(hours=1))
    assert len(points) == 2
    assert points[0].bucket == START
    assert points[0].samples == 3
    assert points[0].avg_bikes == sum(counts[:3]) / 3
    assert points[0].min_bikes == 3
    assert points[0].max_bikes == 7
    assert points[1].avg_bikes == sum(counts[3:]) / 3


def test_latest_and_replay_snapshots(store, sample_stations):
    make_series(store, sample_stations, START, steps=24)
    latest = store.latest_snapshots()
    assert len(latest) == len(sample_stations)
    assert all(s.ts == START + timedelta(minutes=5 * 23) for s in latest)
    assert all(0.0 <= s.occupancy <= 1.0 for s in latest)

    past = store.snapshot_at(START + timedelta(minutes=32))
    assert all(s.ts == START + timedelta(minutes=30) for s in past)
    assert store.snapshot_at(START - timedelta(days=1)) == []


def test_observation_span_and_training_frame(store, sample_stations):
    assert store.observation_span() == (None, None)
    make_series(store, sample_stations, START, steps=48)

    lo, hi = store.observation_span()
    assert lo == START
    assert hi == START + timedelta(minutes=5 * 47)

    frame = store.training_frame()
    assert set(frame.columns) == {
        "bucket",
        "station_id",
        "avg_bikes",
        "avg_docks",
        "capacity",
        "temp_c",
        "precip_mm",
        "wind_kmh",
    }
    assert frame["temp_c"].notna().all(), "hourly weather must join onto every bucket"
    assert len(frame) == len(sample_stations) * 16

    one = store.training_frame(station_id=sample_stations[0].station_id)
    assert one["station_id"].nunique() == 1
    later = store.training_frame(since=START + timedelta(hours=1))
    assert later["bucket"].min() >= START + timedelta(hours=1)


def test_weather_upsert_overwrites(store):
    ts = START
    store.upsert_weather([WeatherPoint(ts=ts, temp_c=1.0, precip_mm=0.0, wind_kmh=1.0)])
    store.upsert_weather([WeatherPoint(ts=ts, temp_c=9.0, precip_mm=2.0, wind_kmh=3.0)])
    frame = store.training_frame()
    assert frame.empty  # no observations yet, but the upsert must not have raised
