"""End-to-end ingestion: GBFS -> bus -> consumer -> store, plus the CV splitter."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pandas as pd
import pytest

from services.common.bus.memory import MemoryBus, reset_topics
from services.common.config import Settings
from services.common.gbfs import GBFSClient
from services.common.models import WeatherPoint
from services.forecast.baseline import mae, rmse, seasonal_naive, skill
from services.forecast.train import rolling_origin_splits
from services.ingest.consumer import ObservationConsumer
from services.ingest.poller import Poller

START = datetime(2026, 8, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _clean_topics():
    reset_topics()
    yield
    reset_topics()


class StubWeather:
    def recent(self, past_days: int = 2, forecast_days: int = 2) -> list[WeatherPoint]:
        return [
            WeatherPoint(ts=START + timedelta(hours=h), temp_c=20.0, precip_mm=0.0, wind_kmh=4.0)
            for h in range(3)
        ]

    def close(self) -> None:
        return None


def stub_gbfs(discovery, station_information, station_status) -> GBFSClient:
    routes = {
        "https://disco/gbfs.json": discovery,
        "https://api.cyclocity.fr/contracts/ljubljana/gbfs/v3/station_information.json": (
            station_information
        ),
        "https://api.cyclocity.fr/contracts/ljubljana/gbfs/v3/station_status.json": station_status,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=routes[str(request.url)])

    return GBFSClient(
        "https://disco/gbfs.json", client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def test_memory_bus_round_trip():
    producer = MemoryBus("t")
    consumer = MemoryBus("t")  # separate instance, same in-process topic
    producer.produce("a", {"n": 1})
    producer.produce("b", {"n": 2})
    assert producer.flush() == 2

    stream = consumer.consume(timeout_s=0.1)
    assert next(stream) == {"n": 1}
    assert next(stream) == {"n": 2}
    consumer.stop()
    assert list(stream) == []


def test_poll_produces_one_message_per_station(
    store, discovery, station_information, station_status
):
    bus = MemoryBus("station_status")
    poller = Poller(
        bus,
        store,
        Settings(backend="sqlite"),
        gbfs=stub_gbfs(discovery, station_information, station_status),
        weather=StubWeather(),
    )
    poller.refresh_metadata(force=True)
    assert len(store.list_stations()) == 88

    produced = poller.poll_once()
    assert produced == 88
    assert bus.qsize() == 88


def test_consumer_persists_what_the_poller_produced(
    store, discovery, station_information, station_status
):
    bus = MemoryBus("station_status")
    poller = Poller(
        bus,
        store,
        Settings(backend="sqlite"),
        gbfs=stub_gbfs(discovery, station_information, station_status),
        weather=StubWeather(),
    )
    poller.refresh_metadata(force=True)
    poller.poll_once()

    consumer = ObservationConsumer(MemoryBus("station_status"), store, batch_size=25)
    consumer.run(max_messages=88)

    assert consumer.written == 88
    assert store.observation_count() == 88
    assert len(store.latest_snapshots()) == 88


def test_consumer_skips_malformed_messages(store, sample_stations):
    store.upsert_stations(sample_stations)
    bus = MemoryBus("station_status")
    bus.produce("bad", {"nonsense": True})
    bus.produce(
        "good",
        {
            "station_id": sample_stations[0].station_id,
            "ts": START.isoformat(),
            "num_bikes": 4,
            "num_docks": 6,
        },
    )
    consumer = ObservationConsumer(bus, store, batch_size=1)
    consumer.run(max_messages=1)
    assert store.observation_count() == 1


def test_seasonal_naive_shifts_by_a_week_minus_the_horizon():
    series = pd.Series(range(1000), dtype="float64")
    shifted = seasonal_naive(series, 60)
    # target is t+4 buckets; naive value comes from t+4-672
    assert shifted.iloc[700] == series.iloc[700 - 672 + 4]


def test_error_metrics_and_skill():
    truth = [1.0, 2.0, 3.0]
    assert mae(truth, [1.0, 2.0, 3.0]) == 0.0
    assert mae(truth, [2.0, 3.0, 4.0]) == 1.0
    assert rmse(truth, [1.0, 2.0, 5.0]) == pytest.approx((4 / 3) ** 0.5)
    assert skill(0.5, 1.0) == 0.5
    assert skill(1.0, 0.0) == 0.0


def test_rolling_origin_splits_never_leak_targets():
    buckets = pd.Series(pd.date_range(START, periods=1000, freq="15min", tz="UTC"))
    splits = rolling_origin_splits(buckets, n_folds=4, horizon_min=60)
    assert len(splits) == 4
    previous_end = None
    for train_end, test_start, test_end in splits:
        assert train_end < test_start, "a gap separates train from test"
        assert (test_start - train_end) >= pd.Timedelta(minutes=60)
        assert test_start < test_end
        if previous_end is not None:
            assert test_start >= previous_end, "test windows move forward, never overlap"
        previous_end = test_end
