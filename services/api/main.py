"""FastAPI service: station state, history, forecasts and a live WebSocket feed.

Path operations are plain ``def``, so FastAPI runs them in a thread pool and the
synchronous store/cache stay non-blocking. The WebSocket endpoint is async and is
fed by one background poller shared by every connected client.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from services.api.cache import Cache, make_cache
from services.api.ws import LiveHub
from services.common.config import Settings, get_settings
from services.common.models import BUCKET_MIN, utcnow
from services.common.storage import make_store
from services.common.storage.base import Store
from services.forecast.predict import Forecaster

log = logging.getLogger("urbanpulse.api")

MAX_HISTORY_HOURS = 24 * 30


class State:
    """Process-wide singletons, wired once at startup."""

    settings: Settings
    store: Store
    cache: Cache
    forecaster: Forecaster
    hub: LiveHub


state = State()


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s %(message)s"
    )
    state.settings = get_settings()
    state.store = make_store(state.settings)
    state.cache = make_cache(state.settings.redis_url)
    state.forecaster = Forecaster(state.settings.model_dir, state.store)
    state.hub = LiveHub(state.store, state.settings.ws_push_interval_s)
    await state.hub.start()
    log.info(
        "api ready: backend=%s cache=%s model=%s",
        state.settings.backend,
        state.cache.name,
        state.forecaster.model_name,
    )
    try:
        yield
    finally:
        await state.hub.stop()
        state.cache.close()
        state.store.close()


app = FastAPI(
    title="UrbanPulse API",
    version="0.1.0",
    summary="Real-time bike-share availability and short-horizon forecasts for Ljubljana",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # public read-only API; no credentials are ever accepted
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


def get_cache() -> Cache:
    return state.cache


def get_store_dep() -> Store:
    return state.store


# ------------------------------------------------------------------- endpoints
@app.get("/health", tags=["ops"])
def health() -> dict[str, Any]:
    lo, hi = state.store.observation_span()
    return {
        "status": "ok",
        "backend": state.store.name,
        "cache": state.cache.name,
        "model": state.forecaster.model_name,
        "model_ready": state.forecaster.ready,
        "observations": {
            "first": lo.isoformat() if lo else None,
            "last": hi.isoformat() if hi else None,
        },
        "ws_clients": state.hub.client_count,
    }


@app.get("/stations", tags=["stations"])
def stations(cache: Cache = Depends(get_cache)) -> list[dict[str, Any]]:
    """Every station with its most recent reading."""

    def load() -> list[dict[str, Any]]:
        return [s.model_dump(mode="json") for s in state.store.latest_snapshots()]

    return cache.fetch("stations:latest", state.settings.cache_ttl_stations, load)


@app.get("/stations/{station_id}/history", tags=["stations"])
def history(
    station_id: str,
    hours: int = Query(24, ge=1, le=MAX_HISTORY_HOURS),
    cache: Cache = Depends(get_cache),
) -> dict[str, Any]:
    """15-minute availability buckets from the continuous aggregate."""
    until = utcnow()
    since = until - timedelta(hours=hours)

    def load() -> dict[str, Any]:
        points = state.store.history(station_id, since, until)
        if not points:
            raise HTTPException(status_code=404, detail=f"no history for station {station_id}")
        return {
            "station_id": station_id,
            "bucket_minutes": BUCKET_MIN,
            "hours": hours,
            "points": [p.model_dump(mode="json") for p in points],
        }

    bucket_key = int(until.timestamp()) // (BUCKET_MIN * 60)
    return cache.fetch(
        f"history:{station_id}:{hours}:{bucket_key}", state.settings.cache_ttl_history, load
    )


@app.get("/predict/{station_id}", tags=["forecast"])
def predict(station_id: str, cache: Cache = Depends(get_cache)) -> dict[str, Any]:
    """Bikes available at +15, +30 and +60 minutes."""

    def load() -> dict[str, Any]:
        try:
            return state.forecaster.predict(station_id).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    bucket_key = int(utcnow().timestamp()) // (BUCKET_MIN * 60)
    return cache.fetch(f"predict:{station_id}:{bucket_key}", state.settings.cache_ttl_predict, load)


@app.get("/replay", tags=["stations"])
def replay(
    ts: datetime = Query(..., description="UTC instant to reconstruct"),
    cache: Cache = Depends(get_cache),
) -> dict[str, Any]:
    """System-wide snapshot as it stood at ``ts`` — powers the dashboard time slider."""
    at = ts if ts.tzinfo else ts.replace(tzinfo=UTC)

    def load() -> dict[str, Any]:
        snaps = state.store.snapshot_at(at)
        return {
            "ts": at.isoformat(),
            "stations": [s.model_dump(mode="json") for s in snaps],
        }

    bucket_key = int(at.timestamp()) // (BUCKET_MIN * 60)
    return cache.fetch(f"replay:{bucket_key}", state.settings.cache_ttl_replay, load)


@app.get("/meta/span", tags=["ops"])
def span() -> dict[str, Any]:
    """Observation window available for replay."""
    lo, hi = state.store.observation_span()
    return {
        "first": lo.isoformat() if lo else None,
        "last": hi.isoformat() if hi else None,
        "count": state.store.observation_count(),
    }


@app.get("/metrics/cache", tags=["ops"])
def cache_metrics(cache: Cache = Depends(get_cache)) -> dict[str, Any]:
    """Live cache hit/miss counters, plus the configured TTLs."""
    return {
        **cache.stats(),
        "ttl_seconds": {
            "stations": state.settings.cache_ttl_stations,
            "history": state.settings.cache_ttl_history,
            "predict": state.settings.cache_ttl_predict,
            "replay": state.settings.cache_ttl_replay,
        },
    }


@app.post("/metrics/cache/reset", tags=["ops"])
def cache_metrics_reset(cache: Cache = Depends(get_cache)) -> dict[str, Any]:
    """Zero the counters so a measurement run starts from a known state."""
    cache.reset_stats()
    cache.clear()
    return cache.stats()


@app.websocket("/ws")
async def websocket_endpoint(socket: WebSocket) -> None:
    """Push changed station states every ``URBANPULSE_WS_PUSH_INTERVAL_S`` seconds."""
    await socket.accept()
    queue = state.hub.subscribe()
    try:
        await socket.send_text(json.dumps(state.hub.snapshot_message()))
        while True:
            message = await queue.get()
            await socket.send_text(json.dumps(message))
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except RuntimeError:  # socket closed under us
        pass
    finally:
        state.hub.unsubscribe(queue)
