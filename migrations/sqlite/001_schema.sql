-- SQLite mirror of the TimescaleDB schema (see migrations/timescale/).
-- Timestamps are stored as INTEGER epoch seconds (UTC) for cheap bucket arithmetic.

CREATE TABLE IF NOT EXISTS stations (
    station_id TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    lat        REAL NOT NULL,
    lon        REAL NOT NULL,
    address    TEXT NOT NULL DEFAULT '',
    capacity   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS station_status (
    station_id      TEXT    NOT NULL,
    ts              INTEGER NOT NULL,
    num_bikes       INTEGER NOT NULL,
    num_docks       INTEGER NOT NULL,
    bikes_disabled  INTEGER NOT NULL DEFAULT 0,
    docks_disabled  INTEGER NOT NULL DEFAULT 0,
    is_renting      INTEGER NOT NULL DEFAULT 1,
    is_returning    INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (station_id, ts)
);

CREATE INDEX IF NOT EXISTS station_status_ts_idx ON station_status (ts DESC);

CREATE TABLE IF NOT EXISTS weather (
    ts         INTEGER PRIMARY KEY,
    temp_c     REAL NOT NULL,
    precip_mm  REAL NOT NULL,
    wind_kmh   REAL NOT NULL
);
