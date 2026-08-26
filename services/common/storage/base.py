"""Storage interface shared by the SQLite dev backend and the TimescaleDB backend.

Every method is synchronous on purpose: FastAPI runs plain ``def`` path operations in
a thread pool, so both backends stay simple and identical in shape while the API
remains non-blocking.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime

import pandas as pd

from services.common.models import (
    HistoryPoint,
    Observation,
    Station,
    StationSnapshot,
    WeatherPoint,
)


class Store(ABC):
    """Persistence port. Implementations live in ``sqlite.py`` and ``timescale.py``."""

    #: Human-readable backend name, surfaced by /health.
    name: str = "abstract"

    @abstractmethod
    def migrate(self) -> list[str]:
        """Apply pending versioned SQL migrations. Returns the names applied."""

    @abstractmethod
    def upsert_stations(self, stations: Sequence[Station]) -> int:
        """Insert or update station metadata. Returns the row count written."""

    @abstractmethod
    def list_stations(self) -> list[Station]: ...

    @abstractmethod
    def insert_observations(self, observations: Sequence[Observation]) -> int:
        """Append status readings. Duplicates on (station_id, ts) are ignored."""

    @abstractmethod
    def upsert_weather(self, points: Sequence[WeatherPoint]) -> int: ...

    @abstractmethod
    def latest_snapshots(self) -> list[StationSnapshot]:
        """Most recent reading for every station."""

    @abstractmethod
    def snapshot_at(self, ts: datetime) -> list[StationSnapshot]:
        """Last reading at or before ``ts`` for every station (historical replay)."""

    @abstractmethod
    def history(self, station_id: str, since: datetime, until: datetime) -> list[HistoryPoint]:
        """15-minute aggregate buckets, read from the continuous aggregate / view."""

    @abstractmethod
    def observation_span(self) -> tuple[datetime | None, datetime | None]:
        """(first, last) observation timestamps, or (None, None) when empty."""

    @abstractmethod
    def observation_count(self) -> int: ...

    @abstractmethod
    def training_frame(self) -> pd.DataFrame:
        """All 15-minute buckets joined with station capacity and hourly weather.

        Columns: bucket, station_id, avg_bikes, avg_docks, capacity,
        temp_c, precip_mm, wind_kmh.
        """

    def close(self) -> None:  # pragma: no cover - trivial default
        return None
