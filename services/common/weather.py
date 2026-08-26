"""Open-Meteo client (keyless) for hourly temperature, precipitation and wind.

Two endpoints are used:
  * ``archive-api`` for historical hours (lags real time by a few days);
  * ``api`` (forecast) with ``past_days`` for the recent tail and the near future.

Docs: https://open-meteo.com/en/docs and https://open-meteo.com/en/docs/historical-weather-api
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from services.common.models import WeatherPoint

log = logging.getLogger(__name__)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HOURLY_VARS = "temperature_2m,precipitation,wind_speed_10m"


def _rows(payload: dict[str, Any]) -> list[WeatherPoint]:
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    temps = hourly.get("temperature_2m") or []
    precip = hourly.get("precipitation") or []
    wind = hourly.get("wind_speed_10m") or []
    out: list[WeatherPoint] = []
    for i, stamp in enumerate(times):
        t = temps[i] if i < len(temps) else None
        p = precip[i] if i < len(precip) else None
        w = wind[i] if i < len(wind) else None
        if t is None:
            continue
        out.append(
            WeatherPoint(
                ts=datetime.fromisoformat(stamp).replace(tzinfo=UTC),
                temp_c=float(t),
                precip_mm=float(p or 0.0),
                wind_kmh=float(w or 0.0),
            )
        )
    return out


class WeatherClient:
    def __init__(
        self,
        lat: float,
        lon: float,
        timeout_s: float = 20.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.lat = lat
        self.lon = lon
        self._client = client or httpx.Client(timeout=timeout_s)

    def archive(self, start: date, end: date) -> list[WeatherPoint]:
        payload = self._get(
            ARCHIVE_URL,
            {
                "latitude": self.lat,
                "longitude": self.lon,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "hourly": HOURLY_VARS,
                "timezone": "UTC",
            },
        )
        return _rows(payload)

    def recent(self, past_days: int = 7, forecast_days: int = 2) -> list[WeatherPoint]:
        payload = self._get(
            FORECAST_URL,
            {
                "latitude": self.lat,
                "longitude": self.lon,
                "hourly": HOURLY_VARS,
                "past_days": min(past_days, 92),
                "forecast_days": forecast_days,
                "timezone": "UTC",
            },
        )
        return _rows(payload)

    def range(self, start: datetime, end: datetime) -> list[WeatherPoint]:
        """Hourly weather covering [start, end], stitching archive + forecast.

        The archive endpoint trails real time, so anything it omits is backfilled
        from the forecast endpoint's ``past_days`` window. Results are merged on
        the timestamp, with archive values winning.
        """
        merged: dict[datetime, WeatherPoint] = {}
        try:
            for point in self.archive(start.date(), end.date()):
                merged[point.ts] = point
        except httpx.HTTPError as exc:
            log.warning("open-meteo archive unavailable (%s); using forecast only", exc)

        gap_days = max(1, (datetime.now(tz=UTC) - start).days + 1)
        try:
            for point in self.recent(past_days=gap_days, forecast_days=2):
                merged.setdefault(point.ts, point)
        except httpx.HTTPError as exc:
            log.warning("open-meteo forecast unavailable (%s)", exc)

        lo = start - timedelta(hours=1)
        return sorted(
            (p for p in merged.values() if lo <= p.ts <= end + timedelta(hours=1)),
            key=lambda p: p.ts,
        )

    def _get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        resp = self._client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    def close(self) -> None:
        self._client.close()
