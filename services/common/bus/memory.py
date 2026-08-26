"""In-process queue standing in for Kafka when ``URBANPULSE_BACKEND=sqlite``.

Same semantics the pipeline relies on: ordered, at-least-once, decoupled producer
and consumer. It is process-local, so the poller and consumer must share a process
(``python -m services.ingest`` runs both in threads).
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Iterator
from typing import Any

from services.common.bus.base import Bus

_TOPICS: dict[str, queue.Queue[dict[str, Any]]] = {}
_TOPICS_LOCK = threading.Lock()


def _topic_queue(topic: str) -> queue.Queue[dict[str, Any]]:
    with _TOPICS_LOCK:
        if topic not in _TOPICS:
            _TOPICS[topic] = queue.Queue(maxsize=10_000)
        return _TOPICS[topic]


class MemoryBus(Bus):
    name = "memory"

    def __init__(self, topic: str = "station_status") -> None:
        self.topic = topic
        self._q = _topic_queue(topic)
        self._stop = threading.Event()

    def produce(self, key: str, value: dict[str, Any]) -> None:
        self._q.put({"key": key, "value": value})

    def flush(self, timeout_s: float = 10.0) -> int:
        return self._q.qsize()

    def consume(self, timeout_s: float = 1.0) -> Iterator[dict[str, Any]]:
        while not self._stop.is_set():
            try:
                msg = self._q.get(timeout=timeout_s)
            except queue.Empty:
                continue
            yield msg["value"]

    def stop(self) -> None:
        self._stop.set()

    def close(self) -> None:
        self.stop()

    def qsize(self) -> int:
        return self._q.qsize()


def reset_topics() -> None:
    """Clear all in-process topics — used by tests."""
    with _TOPICS_LOCK:
        _TOPICS.clear()
