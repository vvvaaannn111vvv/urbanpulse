"""GBFS v3 parsing, checked against real payload snapshots from the live feed."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from services.common.gbfs import GBFSClient, _parse_ts, _pick_name, parse_station, parse_status


def test_station_information_snapshot_parses(station_information):
    stations = [parse_station(s) for s in station_information["data"]["stations"]]
    assert len(stations) == 88
    first = stations[0]
    assert first.station_id == "1"
    assert first.name == "PREŠERNOV TRG-PETKOVŠKOVO NABREŽJE"
    assert first.capacity == 20
    assert 45.9 < first.lat < 46.2
    assert 14.4 < first.lon < 14.7
    assert all(s.capacity > 0 for s in stations)


def test_localised_name_prefers_english():
    raw = [{"text": "SLOVENSKO", "language": "sl"}, {"text": "ENGLISH", "language": "en"}]
    assert _pick_name(raw) == "ENGLISH"
    assert _pick_name([{"text": "ONLY", "language": "sl"}]) == "ONLY"
    assert _pick_name("plain string") == "plain string"
    assert _pick_name(None) == ""


def test_status_snapshot_parses(station_status):
    feed_ts = datetime(2026, 8, 26, 16, 43, tzinfo=UTC)
    obs = [parse_status(s, feed_ts) for s in station_status["data"]["stations"]]
    assert len(obs) == 88
    assert all(o.ts == feed_ts for o in obs), "feed-level last_updated is the observation time"
    assert all(o.num_bikes >= 0 and o.num_docks >= 0 for o in obs)
    assert sum(o.num_bikes for o in obs) > 0


def test_parse_ts_accepts_v3_string_and_v2_epoch():
    fallback = datetime(2000, 1, 1, tzinfo=UTC)
    assert _parse_ts("2026-08-26T16:43:19.802Z", fallback).year == 2026
    assert _parse_ts(1756224199, fallback).tzinfo is UTC
    assert _parse_ts("not-a-date", fallback) == fallback
    assert _parse_ts(None, fallback) == fallback


def test_client_resolves_feeds_and_fetches(discovery, station_information, station_status):
    routes = {
        "https://disco/gbfs.json": discovery,
        "https://api.cyclocity.fr/contracts/ljubljana/gbfs/v3/station_information.json": (
            station_information
        ),
        "https://api.cyclocity.fr/contracts/ljubljana/gbfs/v3/station_status.json": station_status,
    }
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json=routes[str(request.url)])

    client = GBFSClient(
        "https://disco/gbfs.json", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert set(client.feeds()) == {"station_information", "station_status"}
    assert len(client.stations()) == 88
    observations, feed_ts = client.statuses()
    assert len(observations) == 88
    assert feed_ts.year == 2026
    # Discovery is fetched once and cached.
    assert calls.count("https://disco/gbfs.json") == 1


def test_missing_feed_raises(discovery):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=discovery)

    client = GBFSClient(
        "https://disco/gbfs.json", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(KeyError, match="free_bike_status"):
        client._feed_url("free_bike_status")
