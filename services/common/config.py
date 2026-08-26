"""Central configuration, read from the environment (see .env.example)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

Backend = Literal["sqlite", "timescale"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="URBANPULSE_", env_file=".env", extra="ignore")

    # Which storage + message-bus implementation to use.
    #   sqlite    -> SQLite file + in-process queue (works with no Docker)
    #   timescale -> TimescaleDB + Kafka (docker compose stack)
    backend: Backend = "sqlite"

    sqlite_path: str = "data/urbanpulse.sqlite"
    pg_dsn: str = "postgresql://urbanpulse:urbanpulse@localhost:5432/urbanpulse"

    kafka_bootstrap: str = "localhost:9092"
    kafka_topic: str = "station_status"
    kafka_group: str = "urbanpulse-consumer"

    redis_url: str = ""
    cache_ttl_stations: int = 15
    cache_ttl_history: int = 60
    cache_ttl_predict: int = 60
    cache_ttl_replay: int = 300

    gbfs_discovery: str = "https://api.cyclocity.fr/contracts/ljubljana/gbfs/v3/gbfs.json"
    poll_interval_s: float = 60.0

    lat: float = 46.05
    lon: float = 14.51

    model_dir: str = "models"
    ws_push_interval_s: float = 5.0

    http_timeout_s: float = 20.0


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
