-- Plain view standing in for the TimescaleDB continuous aggregate. Same columns,
-- same 15-minute bucket boundaries, computed on read instead of materialised.

DROP VIEW IF EXISTS station_status_15m;

CREATE VIEW station_status_15m AS
SELECT
    (ts - (ts % 900))   AS bucket,
    station_id          AS station_id,
    AVG(num_bikes)      AS avg_bikes,
    MIN(num_bikes)      AS min_bikes,
    MAX(num_bikes)      AS max_bikes,
    AVG(num_docks)      AS avg_docks,
    COUNT(*)            AS samples
FROM station_status
GROUP BY bucket, station_id;
