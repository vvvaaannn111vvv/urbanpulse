"""Live-update hub behind ``GET /ws``.

One background task polls the store on a fixed interval, diffs the result against
the previous snapshot and fans the changed stations out to every subscriber. That
keeps the read amplification at one query per interval regardless of how many
dashboards are open, and works identically whether the writer is the Kafka
consumer (stack mode) or the in-process consumer (dev mode).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from services.common.models import utcnow
from services.common.storage.base import Store

log = logging.getLogger("urbanpulse.ws")

QUEUE_MAXSIZE = 32


class LiveHub:
    def __init__(self, store: Store, interval_s: float = 5.0) -> None:
        self.store = store
        self.interval_s = interval_s
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._task: asyncio.Task[None] | None = None
        self._latest: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        await self._refresh()
        self._task = asyncio.create_task(self._loop(), name="urbanpulse-live-hub")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    # ------------------------------------------------------------ subscribers
    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    @property
    def client_count(self) -> int:
        return len(self._subscribers)

    def snapshot_message(self) -> dict[str, Any]:
        return {
            "type": "snapshot",
            "ts": utcnow().isoformat(),
            "stations": list(self._latest.values()),
        }

    # ------------------------------------------------------------------- loop
    async def _refresh(self) -> list[dict[str, Any]]:
        """Reload the latest snapshots off-thread; return the rows that changed."""
        snapshots = await asyncio.to_thread(self.store.latest_snapshots)
        changed: list[dict[str, Any]] = []
        for snap in snapshots:
            row = snap.model_dump(mode="json")
            previous = self._latest.get(snap.station_id)
            if previous != row:
                changed.append(row)
            self._latest[snap.station_id] = row
        return changed

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.interval_s)
            try:
                changed = await self._refresh()
            except Exception:  # noqa: BLE001 - one bad poll must not kill the hub
                log.exception("live hub refresh failed")
                continue
            if not changed or not self._subscribers:
                continue
            message = {"type": "update", "ts": utcnow().isoformat(), "stations": changed}
            for queue in list(self._subscribers):
                try:
                    queue.put_nowait(message)
                except asyncio.QueueFull:
                    # A slow client is dropped rather than allowed to stall the hub.
                    log.warning("dropping update for a slow websocket client")
