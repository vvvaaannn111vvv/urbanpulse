"""API surface: endpoints, cache accounting, TTL behaviour and the websocket."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from services.api.cache import MemoryCache, make_cache
from services.common.config import get_settings
from services.common.gbfs import parse_station
from services.common.storage.sqlite import SQLiteStore
from tests.conftest import load_fixture, make_series


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient backed by a small freshly-seeded SQLite database."""
    db = tmp_path / "api.sqlite"
    monkeypatch.setenv("URBANPULSE_BACKEND", "sqlite")
    monkeypatch.setenv("URBANPULSE_SQLITE_PATH", str(db))
    monkeypatch.setenv("URBANPULSE_REDIS_URL", "")
    monkeypatch.setenv("URBANPULSE_MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("URBANPULSE_WS_PUSH_INTERVAL_S", "0.05")
    get_settings.cache_clear()

    stations = [
        parse_station(s)
        for s in load_fixture("gbfs_station_information.json")["data"]["stations"][:4]
    ]
    seed = SQLiteStore(str(db))
    seed.migrate()
    # End the series "now" so the endpoints' now-relative windows see the data.
    end = datetime.now(tz=UTC).replace(second=0, microsecond=0)
    end -= timedelta(minutes=end.minute % 5)
    make_series(seed, stations, end - timedelta(hours=6), steps=72)
    seed.close()

    from services.api.main import app

    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


def test_health_reports_backend_and_span(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["backend"] == "sqlite"
    assert body["cache"] == "memory"
    # No model files in the temp dir -> the API must say so instead of pretending.
    assert body["model_ready"] is False
    assert body["observations"]["first"] is not None


def test_stations_returns_latest_reading_per_station(client):
    body = client.get("/stations").json()
    assert len(body) == 4
    assert {s["station_id"] for s in body} == {s["station_id"] for s in body}
    for s in body:
        assert 0.0 <= s["occupancy"] <= 1.0
        assert s["num_bikes"] >= 0


def test_history_returns_fifteen_minute_buckets(client):
    body = client.get("/stations/1/history?hours=2").json()
    assert body["bucket_minutes"] == 15
    assert 1 <= len(body["points"]) <= 9
    buckets = [p["bucket"] for p in body["points"]]
    assert buckets == sorted(buckets)
    assert all(p["samples"] > 0 for p in body["points"])


def test_history_rejects_bad_range_and_unknown_station(client):
    assert client.get("/stations/1/history?hours=0").status_code == 422
    assert client.get("/stations/does-not-exist/history").status_code == 404


def test_predict_falls_back_to_persistence_without_a_model(client):
    body = client.get("/predict/1").json()
    assert body["model"] == "persistence"
    assert [p["horizon_min"] for p in body["predictions"]] == [15, 30, 60]
    for p in body["predictions"]:
        assert 0 <= p["predicted_bikes"] <= body["capacity"]
    assert client.get("/predict/nope").status_code == 404


def test_replay_reconstructs_a_past_instant(client):
    span = client.get("/meta/span").json()
    mid = datetime.fromisoformat(span["first"]) + timedelta(hours=3)
    body = client.get("/replay", params={"ts": mid.isoformat()}).json()
    assert len(body["stations"]) == 4
    for s in body["stations"]:
        assert datetime.fromisoformat(s["ts"]) <= mid


def test_cache_counts_hits_and_can_be_reset(client):
    client.post("/metrics/cache/reset")
    client.get("/stations")
    client.get("/stations")
    client.get("/stations")
    stats = client.get("/metrics/cache").json()
    assert stats["misses"] == 1
    assert stats["hits"] == 2
    assert stats["hit_rate"] == pytest.approx(2 / 3, abs=1e-4)  # reported to 4 dp
    assert stats["ttl_seconds"]["stations"] > 0

    after = client.post("/metrics/cache/reset").json()
    assert after["hits"] == 0 and after["misses"] == 0


def test_websocket_sends_a_snapshot_on_connect(client):
    with client.websocket_connect("/ws") as ws:
        message = ws.receive_json()
    assert message["type"] == "snapshot"
    assert len(message["stations"]) == 4


def test_memory_cache_respects_ttl():
    cache = MemoryCache()
    calls = []

    def loader():
        calls.append(1)
        return {"v": len(calls)}

    assert cache.fetch("k", ttl=60, loader=loader) == {"v": 1}
    assert cache.fetch("k", ttl=60, loader=loader) == {"v": 1}
    assert len(calls) == 1

    cache.fetch("expiring", ttl=0, loader=loader)
    time.sleep(0.01)
    cache.fetch("expiring", ttl=0, loader=loader)
    assert len(calls) == 3, "an expired entry must not be served from cache"
    assert cache.stats()["backend"] == "memory"


def test_make_cache_falls_back_when_redis_is_unreachable():
    cache = make_cache("redis://127.0.0.1:1/0")
    assert cache.name == "memory"
