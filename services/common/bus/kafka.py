"""Kafka implementation of :class:`Bus` (docker-compose stack).

Requires the ``stack`` extra: ``pip install -e ".[stack]"``.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterator
from typing import Any

from services.common.bus.base import Bus

log = logging.getLogger(__name__)


class KafkaBus(Bus):
    name = "kafka"

    def __init__(
        self,
        bootstrap: str,
        topic: str = "station_status",
        group: str = "urbanpulse-consumer",
        role: str = "producer",
    ) -> None:
        try:
            from confluent_kafka import Consumer, Producer
        except ModuleNotFoundError as exc:  # pragma: no cover - stack extra only
            raise RuntimeError(
                "Kafka backend needs the 'stack' extra: pip install -e '.[stack]'"
            ) from exc
        self.topic = topic
        self._stop = threading.Event()
        self._producer = None
        self._consumer = None
        if role in ("producer", "both"):
            self._producer = Producer(
                {
                    "bootstrap.servers": bootstrap,
                    "linger.ms": 50,
                    "acks": "all",
                    "enable.idempotence": True,
                }
            )
        if role in ("consumer", "both"):
            self._consumer = Consumer(
                {
                    "bootstrap.servers": bootstrap,
                    "group.id": group,
                    "auto.offset.reset": "earliest",
                    "enable.auto.commit": True,
                }
            )
            self._consumer.subscribe([topic])

    def produce(self, key: str, value: dict[str, Any]) -> None:
        assert self._producer is not None, "bus built with role='consumer'"
        self._producer.produce(
            self.topic,
            key=key.encode(),
            value=json.dumps(value, default=str).encode(),
        )
        self._producer.poll(0)

    def flush(self, timeout_s: float = 10.0) -> int:
        if self._producer is None:
            return 0
        return int(self._producer.flush(timeout_s))

    def consume(self, timeout_s: float = 1.0) -> Iterator[dict[str, Any]]:
        assert self._consumer is not None, "bus built with role='producer'"
        while not self._stop.is_set():
            msg = self._consumer.poll(timeout_s)
            if msg is None:
                continue
            if msg.error() is not None:
                log.warning("kafka consume error: %s", msg.error())
                continue
            yield json.loads(msg.value().decode())

    def stop(self) -> None:
        self._stop.set()

    def close(self) -> None:
        self.stop()
        if self._consumer is not None:
            self._consumer.close()
