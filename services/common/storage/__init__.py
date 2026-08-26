"""Storage factory: picks the backend from ``URBANPULSE_BACKEND``."""

from __future__ import annotations

from services.common.config import Settings, get_settings
from services.common.storage.base import Store
from services.common.storage.sqlite import SQLiteStore

__all__ = ["Store", "SQLiteStore", "make_store", "get_store"]

_singleton: Store | None = None


def make_store(settings: Settings | None = None) -> Store:
    """Build a fresh store for the configured backend and apply migrations."""
    settings = settings or get_settings()
    if settings.backend == "timescale":
        from services.common.storage.timescale import TimescaleStore

        store: Store = TimescaleStore(settings.pg_dsn)
    else:
        store = SQLiteStore(settings.sqlite_path)
    store.migrate()
    return store


def get_store(settings: Settings | None = None) -> Store:
    """Process-wide store singleton (used by the API and the ingest consumer)."""
    global _singleton
    if _singleton is None:
        _singleton = make_store(settings)
    return _singleton


def reset_store() -> None:
    """Drop the singleton — used by tests."""
    global _singleton
    if _singleton is not None:
        _singleton.close()
    _singleton = None
