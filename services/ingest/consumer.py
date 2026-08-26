"""Bus consumer: drain observations and persist them to the time-series store.

Messages are micro-batched so one poll cycle becomes one multi-row insert.
"""

from __future__ import annotations

import logging
import threading
import time

from services.common.bus.base import Bus
from services.common.models import Observation
from services.common.storage.base import Store

log = logging.getLogger("urbanpulse.consumer")


class ObservationConsumer:
    def __init__(
        self,
        bus: Bus,
        store: Store,
        batch_size: int = 200,
        max_batch_age_s: float = 2.0,
    ) -> None:
        self.bus = bus
        self.store = store
        self.batch_size = batch_size
        self.max_batch_age_s = max_batch_age_s
        self._stop = threading.Event()
        self.written = 0

    def _flush(self, batch: list[Observation]) -> None:
        if not batch:
            return
        self.store.insert_observations(batch)
        self.written += len(batch)
        log.info("persisted %d observations (total %d)", len(batch), self.written)
        batch.clear()

    def run(self, max_messages: int | None = None) -> None:
        batch: list[Observation] = []
        last_flush = time.monotonic()
        seen = 0
        for payload in self.bus.consume(timeout_s=0.5):
            try:
                batch.append(Observation.model_validate(payload))
            except Exception:  # noqa: BLE001 - drop poison messages, keep consuming
                log.exception("skipping malformed message: %r", payload)
                continue
            seen += 1
            if len(batch) >= self.batch_size or (
                time.monotonic() - last_flush > self.max_batch_age_s
            ):
                self._flush(batch)
                last_flush = time.monotonic()
            if max_messages is not None and seen >= max_messages:
                break
            if self._stop.is_set():
                break
        self._flush(batch)

    def stop(self) -> None:
        self._stop.set()
        # Also unblock the bus iterator, which may be parked on a poll timeout.
        self.bus.close()
