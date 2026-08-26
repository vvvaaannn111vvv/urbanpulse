"""SQLite implementation of :class:`Store` — the no-Docker development backend."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from services.common.models import (
    BUCKET_MIN,
    HistoryPoint,
    Observation,
    Station,
    StationSnapshot,
    WeatherPoint,
)
from services.common.storage.base import Store

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations" / "sqlite"


def _epoch(ts: datetime) -> int:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return int(ts.timestamp())


def _dt(epoch: float) -> datetime:
    return datetime.fromtimestamp(float(epoch), tz=UTC)


def _occupancy(bikes: int, capacity: int) -> float:
    if capacity <= 0:
        return 0.0
    return max(0.0, min(1.0, bikes / capacity))


class SQLiteStore(Store):
    name = "sqlite"

    def __init__(self, path: str) -> None:
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: FastAPI serves sync endpoints from a thread pool.
        # A single lock serialises access, which is plenty for a dev backend.
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ schema
    def migrate(self) -> list[str]:
        applied: list[str] = []
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(name TEXT PRIMARY KEY, applied_at INTEGER NOT NULL)"
            )
            done = {r["name"] for r in self._conn.execute("SELECT name FROM schema_migrations")}
            for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if sql_file.name in done:
                    continue
                self._conn.executescript(sql_file.read_text())
                self._conn.execute(
                    "INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)",
                    (sql_file.name, _epoch(datetime.now(tz=UTC))),
                )
                applied.append(sql_file.name)
            self._conn.commit()
        return applied

    # ------------------------------------------------------------------ writes
    def upsert_stations(self, stations: Sequence[Station]) -> int:
        rows = [(s.station_id, s.name, s.lat, s.lon, s.address, s.capacity) for s in stations]
        with self._lock:
            self._conn.executemany(
                "INSERT INTO stations (station_id, name, lat, lon, address, capacity) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(station_id) DO UPDATE SET "
                "name=excluded.name, lat=excluded.lat, lon=excluded.lon, "
                "address=excluded.address, capacity=excluded.capacity",
                rows,
            )
            self._conn.commit()
        return len(rows)

    def insert_observations(self, observations: Sequence[Observation]) -> int:
        rows = [
            (
                o.station_id,
                _epoch(o.ts),
                o.num_bikes,
                o.num_docks,
                o.bikes_disabled,
                o.docks_disabled,
                int(o.is_renting),
                int(o.is_returning),
            )
            for o in observations
        ]
        with self._lock:
            cur = self._conn.executemany(
                "INSERT OR IGNORE INTO station_status "
                "(station_id, ts, num_bikes, num_docks, bikes_disabled, docks_disabled,"
                " is_renting, is_returning) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            self._conn.commit()
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else len(rows)

    def upsert_weather(self, points: Sequence[WeatherPoint]) -> int:
        rows = [(_epoch(p.ts), p.temp_c, p.precip_mm, p.wind_kmh) for p in points]
        with self._lock:
            self._conn.executemany(
                "INSERT INTO weather (ts, temp_c, precip_mm, wind_kmh) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(ts) DO UPDATE SET temp_c=excluded.temp_c, "
                "precip_mm=excluded.precip_mm, wind_kmh=excluded.wind_kmh",
                rows,
            )
            self._conn.commit()
        return len(rows)

    # ------------------------------------------------------------------- reads
    def list_stations(self) -> list[Station]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT station_id, name, lat, lon, address, capacity "
                "FROM stations ORDER BY station_id"
            ).fetchall()
        return [Station(**dict(r)) for r in rows]

    def latest_snapshots(self) -> list[StationSnapshot]:
        sql = """
            SELECT s.station_id, s.name, s.lat, s.lon, s.capacity,
                   ss.num_bikes, ss.num_docks, ss.ts
            FROM stations s
            JOIN station_status ss ON ss.station_id = s.station_id
            JOIN (SELECT station_id, MAX(ts) AS ts FROM station_status GROUP BY station_id) m
              ON m.station_id = ss.station_id AND m.ts = ss.ts
            ORDER BY s.station_id
        """
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
        return [self._snapshot(r) for r in rows]

    def snapshot_at(self, ts: datetime) -> list[StationSnapshot]:
        sql = """
            SELECT s.station_id, s.name, s.lat, s.lon, s.capacity,
                   ss.num_bikes, ss.num_docks, ss.ts
            FROM stations s
            JOIN station_status ss ON ss.station_id = s.station_id
            JOIN (SELECT station_id, MAX(ts) AS ts FROM station_status
                  WHERE ts <= ? GROUP BY station_id) m
              ON m.station_id = ss.station_id AND m.ts = ss.ts
            ORDER BY s.station_id
        """
        with self._lock:
            rows = self._conn.execute(sql, (_epoch(ts),)).fetchall()
        return [self._snapshot(r) for r in rows]

    @staticmethod
    def _snapshot(r: sqlite3.Row) -> StationSnapshot:
        return StationSnapshot(
            station_id=r["station_id"],
            name=r["name"],
            lat=r["lat"],
            lon=r["lon"],
            capacity=r["capacity"],
            num_bikes=r["num_bikes"],
            num_docks=r["num_docks"],
            ts=_dt(r["ts"]),
            occupancy=_occupancy(r["num_bikes"], r["capacity"]),
        )

    def history(self, station_id: str, since: datetime, until: datetime) -> list[HistoryPoint]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT bucket, avg_bikes, min_bikes, max_bikes, avg_docks, samples "
                "FROM station_status_15m WHERE station_id = ? AND bucket >= ? AND bucket <= ? "
                "ORDER BY bucket",
                (station_id, _epoch(since), _epoch(until)),
            ).fetchall()
        return [
            HistoryPoint(
                bucket=_dt(r["bucket"]),
                avg_bikes=r["avg_bikes"],
                min_bikes=r["min_bikes"],
                max_bikes=r["max_bikes"],
                avg_docks=r["avg_docks"],
                samples=r["samples"],
            )
            for r in rows
        ]

    def observation_span(self) -> tuple[datetime | None, datetime | None]:
        with self._lock:
            row = self._conn.execute(
                "SELECT MIN(ts) AS lo, MAX(ts) AS hi FROM station_status"
            ).fetchone()
        if row is None or row["lo"] is None:
            return (None, None)
        return (_dt(row["lo"]), _dt(row["hi"]))

    def observation_count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM station_status").fetchone()
        return int(row["n"])

    def training_frame(self) -> pd.DataFrame:
        sql = """
            SELECT a.bucket        AS bucket,
                   a.station_id    AS station_id,
                   a.avg_bikes     AS avg_bikes,
                   a.avg_docks     AS avg_docks,
                   s.capacity      AS capacity,
                   w.temp_c        AS temp_c,
                   w.precip_mm     AS precip_mm,
                   w.wind_kmh      AS wind_kmh
            FROM station_status_15m a
            JOIN stations s ON s.station_id = a.station_id
            LEFT JOIN weather w ON w.ts = a.bucket - (a.bucket % 3600)
            ORDER BY a.station_id, a.bucket
        """  # noqa: S608 - no user input; BUCKET_MIN documented below
        assert BUCKET_MIN == 15  # the view above hard-codes 900s buckets
        with self._lock:
            df = pd.read_sql_query(sql, self._conn)
        df["bucket"] = pd.to_datetime(df["bucket"], unit="s", utc=True)
        return df

    def close(self) -> None:
        with self._lock:
            self._conn.close()
