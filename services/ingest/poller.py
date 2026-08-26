"""GBFS poller: fetch station status on a fixed interval and produce to the bus.

One message per station, keyed by ``station_id`` so a partitioned Kafka topic
keeps per-station ordering.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import timedelta

from services.common.bus.base import Bus
from services.common.config import Settings
from services.common.gbfs import GBFSClient
from services.common.models import utcnow
from services.common.storage.base import Store
from services.common.weather import WeatherClient

log = logging.getLogger("urbanpulse.poller")

STATION_REFRESH_S = 3600.0
WEATHER_REFRESH_S = 1800.0


class Poller:
    def __init__(
        self,
        bus: Bus,
        store: Store,
        settings: Settings,
        gbfs: GBFSClient | None = None,
        weather: WeatherClient | None = None,
    ) -> None:
        self.bus = bus
        self.store = store
        self.settings = settings
        self.gbfs = gbfs or GBFSClient(settings.gbfs_discovery, settings.http_timeout_s)
        self.weather = weather or WeatherClient(settings.lat, settings.lon, settings.http_timeout_s)
        self._stop = threading.Event()
        self._last_stations = 0.0
        self._last_weather = 0.0
        self.polls = 0
        self.produced = 0

    # ---------------------------------------------------------------- one pass
    def refresh_metadata(self, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self._last_stations > STATION_REFRESH_S:
            stations = self.gbfs.stations()
            self.store.upsert_stations(stations)
            self._last_stations = now
            log.info("refreshed %d stations", len(stations))
        if force or now - self._last_weather > WEATHER_REFRESH_S:
            end = utcnow() + timedelta(hours=6)
            points = self.weather.recent(past_days=2, forecast_days=2)
            self.store.upsert_weather([p for p in points if p.ts <= end])
            self._last_weather = now
            log.info("refreshed %d weather hours", len(points))

    def poll_once(self) -> int:
        """One GBFS fetch -> N bus messages. Returns messages produced."""
        observations, feed_ts = self.gbfs.statuses()
        for obs in observations:
            self.bus.produce(obs.station_id, obs.model_dump(mode="json"))
        self.bus.flush(5.0)
        self.polls += 1
        self.produced += len(observations)
        log.info("polled %d stations at %s", len(observations), feed_ts.isoformat())
        return len(observations)

    # -------------------------------------------------------------------- loop
    def run(self, max_polls: int | None = None) -> None:
        self.refresh_metadata(force=True)
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.refresh_metadata()
                self.poll_once()
            except Exception:  # noqa: BLE001 - a poll failure must not kill the loop
                log.exception("poll failed; retrying next interval")
            if max_polls is not None and self.polls >= max_polls:
                return
            elapsed = time.monotonic() - started
            self._stop.wait(max(0.0, self.settings.poll_interval_s - elapsed))

    def stop(self) -> None:
        self._stop.set()
