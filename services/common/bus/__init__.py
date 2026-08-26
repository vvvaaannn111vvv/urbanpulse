"""Bus factory: Kafka under the ``timescale`` backend, in-process queue otherwise."""

from __future__ import annotations

from services.common.bus.base import Bus
from services.common.bus.memory import MemoryBus
from services.common.config import Settings, get_settings

__all__ = ["Bus", "MemoryBus", "make_bus"]


def make_bus(role: str = "producer", settings: Settings | None = None) -> Bus:
    """Build a bus. ``role`` is one of ``producer``, ``consumer``, ``both``."""
    settings = settings or get_settings()
    if settings.backend == "timescale":
        from services.common.bus.kafka import KafkaBus

        return KafkaBus(
            bootstrap=settings.kafka_bootstrap,
            topic=settings.kafka_topic,
            group=settings.kafka_group,
            role=role,
        )
    return MemoryBus(topic=settings.kafka_topic)
