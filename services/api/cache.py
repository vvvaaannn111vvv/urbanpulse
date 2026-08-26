"""Response cache with hit/miss accounting.

Redis when ``URBANPULSE_REDIS_URL`` is set, an in-process dict otherwise, so the
API runs identically with or without the container stack. Both variants count
hits and misses; ``GET /metrics/cache`` exposes the counters, and
``scripts/measure_cache.py`` turns them into the number quoted in the README.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

log = logging.getLogger("urbanpulse.cache")

KEY_PREFIX = "urbanpulse:"


class Cache(ABC):
    name: str = "abstract"

    def __init__(self) -> None:
        self._hits = 0
        self._misses = 0
        self._lock = threading.Lock()

    @abstractmethod
    def _get(self, key: str) -> str | None: ...

    @abstractmethod
    def _set(self, key: str, payload: str, ttl: int) -> None: ...

    @abstractmethod
    def clear(self) -> None: ...

    def fetch(self, key: str, ttl: int, loader: Callable[[], Any]) -> Any:
        """Return the cached value for ``key``, computing it with ``loader`` on miss."""
        raw = self._get(KEY_PREFIX + key)
        if raw is not None:
            with self._lock:
                self._hits += 1
            return json.loads(raw)
        with self._lock:
            self._misses += 1
        value = loader()
        try:
            self._set(KEY_PREFIX + key, json.dumps(value, default=str), ttl)
        except Exception:  # noqa: BLE001 - a cache write must never fail a request
            log.exception("cache write failed for %s", key)
        return value

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = self._hits + self._misses
            return {
                "backend": self.name,
                "hits": self._hits,
                "misses": self._misses,
                "requests": total,
                "hit_rate": round(self._hits / total, 4) if total else 0.0,
            }

    def reset_stats(self) -> None:
        with self._lock:
            self._hits = 0
            self._misses = 0

    def close(self) -> None:  # pragma: no cover - trivial default
        return None


class MemoryCache(Cache):
    name = "memory"

    def __init__(self, max_entries: int = 4096) -> None:
        super().__init__()
        self._data: dict[str, tuple[float, str]] = {}
        self._max = max_entries

    def _get(self, key: str) -> str | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        expires, payload = entry
        if expires < time.time():
            self._data.pop(key, None)
            return None
        return payload

    def _set(self, key: str, payload: str, ttl: int) -> None:
        if len(self._data) >= self._max:
            # Cheap eviction: drop everything already expired, else the oldest.
            now = time.time()
            stale = [k for k, (exp, _) in self._data.items() if exp < now]
            for k in stale or list(self._data)[:1]:
                self._data.pop(k, None)
        self._data[key] = (time.time() + ttl, payload)

    def clear(self) -> None:
        self._data.clear()


class RedisCache(Cache):
    name = "redis"

    def __init__(self, url: str) -> None:
        super().__init__()
        import redis

        self._client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=2.0)
        self._client.ping()

    def _get(self, key: str) -> str | None:
        try:
            value = self._client.get(key)
        except Exception:  # noqa: BLE001 - treat an unreachable Redis as a miss
            log.exception("redis GET failed")
            return None
        return value if value is None else str(value)

    def _set(self, key: str, payload: str, ttl: int) -> None:
        self._client.setex(key, ttl, payload)

    def clear(self) -> None:
        for key in self._client.scan_iter(match=f"{KEY_PREFIX}*"):
            self._client.delete(key)

    def close(self) -> None:
        self._client.close()


def make_cache(redis_url: str = "") -> Cache:
    """Redis when a URL is configured and reachable, otherwise the in-process cache."""
    if redis_url:
        try:
            cache = RedisCache(redis_url)
            log.info("cache backend: redis (%s)", redis_url)
            return cache
        except Exception as exc:  # noqa: BLE001 - degrade instead of failing to boot
            log.warning("redis unavailable (%s); falling back to in-process cache", exc)
    log.info("cache backend: in-process memory")
    return MemoryCache()
