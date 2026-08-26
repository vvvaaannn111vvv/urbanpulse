CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS stations (
    station_id TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    lat        DOUBLE PRECISION NOT NULL,
    lon        DOUBLE PRECISION NOT NULL,
    address    TEXT NOT NULL DEFAULT '',
    capacity   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS station_status (
    station_id      TEXT        NOT NULL,
    ts              TIMESTAMPTZ NOT NULL,
    num_bikes       INTEGER     NOT NULL,
    num_docks       INTEGER     NOT NULL,
    bikes_disabled  INTEGER     NOT NULL DEFAULT 0,
    docks_disabled  INTEGER     NOT NULL DEFAULT 0,
    is_renting      BOOLEAN     NOT NULL DEFAULT TRUE,
    is_returning    BOOLEAN     NOT NULL DEFAULT TRUE,
    PRIMARY KEY (station_id, ts)
);

-- The hypertable is what makes this a time-series store: Timescale chunks
-- station_status by time so that recent-window scans touch one chunk.
SELECT create_hypertable('station_status', 'ts', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS station_status_ts_idx ON station_status (ts DESC);

CREATE TABLE IF NOT EXISTS weather (
    ts         TIMESTAMPTZ PRIMARY KEY,
    temp_c     DOUBLE PRECISION NOT NULL,
    precip_mm  DOUBLE PRECISION NOT NULL,
    wind_kmh   DOUBLE PRECISION NOT NULL
);
