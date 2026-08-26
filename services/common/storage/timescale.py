"""TimescaleDB implementation of :class:`Store` (the docker-compose backend).

Requires the ``stack`` extra: ``pip install -e ".[stack]"``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from services.common.models import (
    HistoryPoint,
    Observation,
    Station,
    StationSnapshot,
    WeatherPoint,
)
from services.common.storage.base import Store

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations" / "timescale"


def _aware(ts: datetime) -> datetime:
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts


def _occupancy(bikes: int, capacity: int) -> float:
    if capacity <= 0:
        return 0.0
    return max(0.0, min(1.0, bikes / capacity))


_SNAPSHOT_COLS = "s.station_id, s.name, s.lat, s.lon, s.capacity, ss.num_bikes, ss.num_docks, ss.ts"


class TimescaleStore(Store):
    name = "timescale"

    def __init__(self, dsn: str, min_size: int = 1, max_size: int = 8) -> None:
        try:
            from psycopg_pool import ConnectionPool
        except ModuleNotFoundError as exc:  # pragma: no cover - stack extra only
            raise RuntimeError(
                "TimescaleDB backend needs the 'stack' extra: pip install -e '.[stack]'"
            ) from exc
        self.dsn = dsn
        self._pool = ConnectionPool(dsn, min_size=min_size, max_size=max_size, open=True)
        self._pool.wait(timeout=30)

    # ------------------------------------------------------------------ schema
    def migrate(self) -> list[str]:
        applied: list[str] = []
        with self._pool.connection() as conn:
            conn.autocommit = True
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
            for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if sql_file.name in done:
                    continue
                # No parameters -> psycopg uses the simple protocol, so a file of
                # semicolon-separated statements runs as one unit.
                conn.execute(sql_file.read_text())
                conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (sql_file.name,))
                applied.append(sql_file.name)
        return applied

    # ------------------------------------------------------------------ writes
    def upsert_stations(self, stations: Sequence[Station]) -> int:
        rows = [(s.station_id, s.name, s.lat, s.lon, s.address, s.capacity) for s in stations]
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO stations (station_id, name, lat, lon, address, capacity) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (station_id) DO UPDATE SET name = EXCLUDED.name, "
                "lat = EXCLUDED.lat, lon = EXCLUDED.lon, address = EXCLUDED.address, "
                "capacity = EXCLUDED.capacity",
                rows,
            )
        return len(rows)

    def insert_observations(self, observations: Sequence[Observation]) -> int:
        rows = [
            (
                o.station_id,
                _aware(o.ts),
                o.num_bikes,
                o.num_docks,
                o.bikes_disabled,
                o.docks_disabled,
                o.is_renting,
                o.is_returning,
            )
            for o in observations
        ]
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO station_status (station_id, ts, num_bikes, num_docks, "
                "bikes_disabled, docks_disabled, is_renting, is_returning) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                rows,
            )
        return len(rows)

    def upsert_weather(self, points: Sequence[WeatherPoint]) -> int:
        rows = [(_aware(p.ts), p.temp_c, p.precip_mm, p.wind_kmh) for p in points]
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO weather (ts, temp_c, precip_mm, wind_kmh) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (ts) DO UPDATE SET temp_c = EXCLUDED.temp_c, "
                "precip_mm = EXCLUDED.precip_mm, wind_kmh = EXCLUDED.wind_kmh",
                rows,
            )
        return len(rows)

    # ------------------------------------------------------------------- reads
    def list_stations(self) -> list[Station]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT station_id, name, lat, lon, address, capacity "
                "FROM stations ORDER BY station_id"
            ).fetchall()
        return [
            Station(station_id=r[0], name=r[1], lat=r[2], lon=r[3], address=r[4], capacity=r[5])
            for r in rows
        ]

    def latest_snapshots(self) -> list[StationSnapshot]:
        sql = f"""
            SELECT {_SNAPSHOT_COLS}
            FROM stations s
            JOIN LATERAL (
                SELECT num_bikes, num_docks, ts FROM station_status
                WHERE station_id = s.station_id ORDER BY ts DESC LIMIT 1
            ) ss ON TRUE
            ORDER BY s.station_id
        """
        with self._pool.connection() as conn:
            rows = conn.execute(sql).fetchall()
        return [self._snapshot(r) for r in rows]

    def snapshot_at(self, ts: datetime) -> list[StationSnapshot]:
        sql = f"""
            SELECT {_SNAPSHOT_COLS}
            FROM stations s
            JOIN LATERAL (
                SELECT num_bikes, num_docks, ts FROM station_status
                WHERE station_id = s.station_id AND ts <= %s ORDER BY ts DESC LIMIT 1
            ) ss ON TRUE
            ORDER BY s.station_id
        """
        with self._pool.connection() as conn:
            rows = conn.execute(sql, (_aware(ts),)).fetchall()
        return [self._snapshot(r) for r in rows]

    @staticmethod
    def _snapshot(r: Sequence[Any]) -> StationSnapshot:
        return StationSnapshot(
            station_id=r[0],
            name=r[1],
            lat=r[2],
            lon=r[3],
            capacity=r[4],
            num_bikes=r[5],
            num_docks=r[6],
            ts=_aware(r[7]),
            occupancy=_occupancy(r[5], r[4]),
        )

    def history(self, station_id: str, since: datetime, until: datetime) -> list[HistoryPoint]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT bucket, avg_bikes, min_bikes, max_bikes, avg_docks, samples "
                "FROM station_status_15m WHERE station_id = %s AND bucket >= %s AND bucket <= %s "
                "ORDER BY bucket",
                (station_id, _aware(since), _aware(until)),
            ).fetchall()
        return [
            HistoryPoint(
                bucket=_aware(r[0]),
                avg_bikes=float(r[1]),
                min_bikes=int(r[2]),
                max_bikes=int(r[3]),
                avg_docks=float(r[4]),
                samples=int(r[5]),
            )
            for r in rows
        ]

    def observation_span(self) -> tuple[datetime | None, datetime | None]:
        with self._pool.connection() as conn:
            row = conn.execute("SELECT MIN(ts), MAX(ts) FROM station_status").fetchone()
        if row is None or row[0] is None:
            return (None, None)
        return (_aware(row[0]), _aware(row[1]))

    def observation_count(self) -> int:
        with self._pool.connection() as conn:
            row = conn.execute("SELECT COUNT(*) FROM station_status").fetchone()
        return int(row[0]) if row else 0

    def training_frame(self) -> pd.DataFrame:
        sql = """
            SELECT a.bucket, a.station_id, a.avg_bikes, a.avg_docks, s.capacity,
                   w.temp_c, w.precip_mm, w.wind_kmh
            FROM station_status_15m a
            JOIN stations s ON s.station_id = a.station_id
            LEFT JOIN weather w ON w.ts = date_trunc('hour', a.bucket)
            ORDER BY a.station_id, a.bucket
        """
        cols = [
            "bucket",
            "station_id",
            "avg_bikes",
            "avg_docks",
            "capacity",
            "temp_c",
            "precip_mm",
            "wind_kmh",
        ]
        with self._pool.connection() as conn:
            rows = conn.execute(sql).fetchall()
        df = pd.DataFrame(rows, columns=cols)
        df["bucket"] = pd.to_datetime(df["bucket"], utc=True)
        return df

    def close(self) -> None:
        self._pool.close()
