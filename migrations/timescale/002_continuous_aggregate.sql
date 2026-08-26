-- The one continuous aggregate in the MVP: 15-minute availability per station.
-- Timescale keeps this materialised incrementally, so dashboard history reads
-- never re-scan raw observations.

CREATE MATERIALIZED VIEW IF NOT EXISTS station_status_15m
WITH (timescaledb.continuous) AS
SELECT
    time_bucket(INTERVAL '15 minutes', ts) AS bucket,
    station_id,
    AVG(num_bikes)::DOUBLE PRECISION AS avg_bikes,
    MIN(num_bikes)                   AS min_bikes,
    MAX(num_bikes)                   AS max_bikes,
    AVG(num_docks)::DOUBLE PRECISION AS avg_docks,
    COUNT(*)                         AS samples
FROM station_status
GROUP BY bucket, station_id
WITH NO DATA;

-- Real-time aggregation: reads UNION the materialised buckets with a live
-- aggregate over the not-yet-materialised tail, so the dashboard never lags
-- behind the refresh policy.
ALTER MATERIALIZED VIEW station_status_15m
    SET (timescaledb.materialized_only = false);

SELECT add_continuous_aggregate_policy(
    'station_status_15m',
    start_offset      => INTERVAL '7 days',
    end_offset        => INTERVAL '15 minutes',
    schedule_interval => INTERVAL '5 minutes',
    if_not_exists     => TRUE
);
