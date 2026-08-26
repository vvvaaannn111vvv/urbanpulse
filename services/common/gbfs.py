"""GBFS v3 client for BicikeLJ (Ljubljana), served by JCDecaux Cyclocity.

The feed needs no API key. Discovery lists the sub-feeds; we only use
``station_information`` (static metadata) and ``station_status`` (live counts).

Spec: https://gbfs.org/specification/reference/ (v3.0)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from services.common.models import Observation, Station, utcnow

log = logging.getLogger(__name__)

USER_AGENT = "urbanpulse/0.1 (+https://github.com/vvvaaannn111vvv/urbanpulse)"


def _pick_name(raw: Any) -> str:
    """GBFS v3 localises names as ``[{"text": ..., "language": ...}, ...]``."""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list) and raw:
        for entry in raw:
            if isinstance(entry, dict) and entry.get("language") == "en":
                return str(entry.get("text", ""))
        first = raw[0]
        if isinstance(first, dict):
            return str(first.get("text", ""))
    return ""


def _parse_ts(raw: Any, fallback: datetime) -> datetime:
    """v3 timestamps are RFC3339 strings; v2 used epoch ints. Accept both."""
    if isinstance(raw, int | float):
        return datetime.fromtimestamp(float(raw), tz=UTC)
    if isinstance(raw, str) and raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            log.debug("unparseable GBFS timestamp %r", raw)
    return fallback


class GBFSClient:
    def __init__(
        self,
        discovery_url: str,
        timeout_s: float = 20.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.discovery_url = discovery_url
        self._client = client or httpx.Client(timeout=timeout_s, headers={"User-Agent": USER_AGENT})
        self._feeds: dict[str, str] | None = None

    # ------------------------------------------------------------------ feeds
    def feeds(self) -> dict[str, str]:
        """Map feed name -> URL, from the discovery document (cached)."""
        if self._feeds is None:
            doc = self._get(self.discovery_url)
            entries = doc["data"]["feeds"]
            self._feeds = {f["name"]: f["url"] for f in entries}
        return self._feeds

    def _feed_url(self, name: str) -> str:
        feeds = self.feeds()
        if name not in feeds:
            raise KeyError(f"GBFS feed {name!r} not published; have {sorted(feeds)}")
        return feeds[name]

    def _get(self, url: str) -> dict[str, Any]:
        resp = self._client.get(url)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------- data
    def stations(self) -> list[Station]:
        doc = self._get(self._feed_url("station_information"))
        return [parse_station(s) for s in doc["data"]["stations"]]

    def statuses(self) -> tuple[list[Observation], datetime]:
        """Return current observations plus the feed's ``last_updated`` stamp."""
        doc = self._get(self._feed_url("station_status"))
        feed_ts = _parse_ts(doc.get("last_updated"), utcnow())
        obs = [parse_status(s, feed_ts) for s in doc["data"]["stations"]]
        return obs, feed_ts

    def close(self) -> None:
        self._client.close()


def parse_station(raw: dict[str, Any]) -> Station:
    return Station(
        station_id=str(raw["station_id"]),
        name=_pick_name(raw.get("name")),
        lat=float(raw["lat"]),
        lon=float(raw["lon"]),
        address=str(raw.get("address") or ""),
        capacity=int(raw.get("capacity") or 0),
    )


def parse_status(raw: dict[str, Any], feed_ts: datetime) -> Observation:
    """Build an Observation.

    ``last_reported`` is per-station and can be hours stale for a quiet station,
    so the feed-level ``last_updated`` is used as the observation time and the
    station stamp is kept only for reference.
    """
    return Observation(
        station_id=str(raw["station_id"]),
        ts=feed_ts,
        num_bikes=int(raw.get("num_vehicles_available") or raw.get("num_bikes_available") or 0),
        num_docks=int(raw.get("num_docks_available") or 0),
        bikes_disabled=int(raw.get("num_vehicles_disabled") or raw.get("num_bikes_disabled") or 0),
        docks_disabled=int(raw.get("num_docks_disabled") or 0),
        is_renting=bool(raw.get("is_renting", True)),
        is_returning=bool(raw.get("is_returning", True)),
    )
