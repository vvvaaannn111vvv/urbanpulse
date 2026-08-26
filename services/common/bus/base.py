"""Message-bus port: Kafka in the container stack, an in-process queue in dev mode."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any


class Bus(ABC):
    """Minimal produce/consume interface — one topic, JSON payloads."""

    name: str = "abstract"

    @abstractmethod
    def produce(self, key: str, value: dict[str, Any]) -> None:
        """Publish one message. May buffer; call :meth:`flush` to force delivery."""

    @abstractmethod
    def flush(self, timeout_s: float = 10.0) -> int:
        """Block until buffered messages are delivered. Returns messages still queued."""

    @abstractmethod
    def consume(self, timeout_s: float = 1.0) -> Iterator[dict[str, Any]]:
        """Yield decoded messages until the process is stopped."""

    def close(self) -> None:  # pragma: no cover - trivial default
        return None
