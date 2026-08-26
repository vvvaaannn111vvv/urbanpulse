#!/usr/bin/env python
"""Backfill a realistic synthetic history so a fresh clone is immediately usable.

A clone has no observation history — GBFS only publishes the present moment — and
the forecaster needs weeks of it. This script therefore generates a plausible
availability series per station:

  * a diurnal shape that differs between central stations (fill during the day as
    commuters ride in) and outer stations (fill overnight),
  * a flatter, later weekend profile,
  * a weather gain driven by REAL hourly Open-Meteo data for Ljubljana, so the
    weather features carry genuine signal,
  * an AR(1) noise term for realistic short-run autocorrelation,
  * saturation at 0 and at capacity.

Station metadata (ids, coordinates, capacities) is REAL, pulled live from GBFS.
Weather is REAL. Only the bike counts are synthetic. Nothing produced here is
presented as a measurement of the real system — see the README's Status section.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import numpy as np

from services.common.config import get_settings
from services.common.features import holiday_set
from services.common.gbfs import GBFSClient, parse_station
from services.common.models import Observation, Station, WeatherPoint
from services.common.storage import make_store
from services.common.storage.base import Store
from services.common.weather import WeatherClient

log = logging.getLogger("urbanpulse.seed")

REPO_ROOT = Path(__file__).resolve().parents[1]
STATION_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "gbfs_station_information.json"

# Ljubljana city centre (Prešernov trg).
CENTRE_LAT, CENTRE_LON = 46.0511, 14.5060
CENTRAL_RADIUS_KM = 0.9


# --------------------------------------------------------------------- helpers
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def load_stations(offline: bool, discovery: str, timeout: float) -> list[Station]:
    if not offline:
        try:
            client = GBFSClient(discovery, timeout)
            stations = client.stations()
            client.close()
            log.info("fetched %d live stations from GBFS", len(stations))
            return stations
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            log.warning("GBFS unavailable (%s); falling back to the committed snapshot", exc)
    raw = json.loads(STATION_FIXTURE.read_text())
    stations = [parse_station(s) for s in raw["data"]["stations"]]
    log.info("loaded %d stations from the committed GBFS snapshot", len(stations))
    return stations


def load_weather(
    store: Store, start: datetime, end: datetime, settings_lat: float, settings_lon: float
) -> list[WeatherPoint]:
    client = WeatherClient(settings_lat, settings_lon)
    try:
        points = client.range(start, end)
    finally:
        client.close()
    if not points:
        raise RuntimeError("Open-Meteo returned no hourly weather; cannot seed")
    store.upsert_weather(points)
    log.info("stored %d hourly weather points (%s .. %s)", len(points), points[0].ts, points[-1].ts)
    return points


# ------------------------------------------------------------------- generator
def _daytime_plateau(hours: np.ndarray) -> np.ndarray:
    """~0 overnight, ~1 between roughly 07:00 and 18:30, smooth at the edges."""
    return 0.5 * (1 + np.tanh((hours - 6.8) / 1.1)) - 0.5 * (1 + np.tanh((hours - 18.3) / 1.4))


def _weekend_shape(hours: np.ndarray) -> np.ndarray:
    """Single leisure bump centred in the early afternoon."""
    return np.exp(-((hours - 14.0) ** 2) / (2 * 3.4**2))


def diurnal_profile(hours: np.ndarray, is_free_day: np.ndarray, central: bool) -> np.ndarray:
    """Occupancy shape in [0, 1] for one station type."""
    work = _daytime_plateau(hours)
    if central:
        # Sharp arrival spike as the morning commute lands.
        work = work + 0.22 * np.exp(-((hours - 8.2) ** 2) / (2 * 0.75**2))
        work = work - 0.10 * np.exp(-((hours - 17.4) ** 2) / (2 * 0.9**2))
    else:
        work = 1.0 - work
        work = work + 0.18 * np.exp(-((hours - 18.6) ** 2) / (2 * 1.0**2))
        work = work - 0.12 * np.exp(-((hours - 7.9) ** 2) / (2 * 0.7**2))
    free = _weekend_shape(hours) * (1.0 if central else 0.55)
    shape = np.where(is_free_day, 0.62 * free + 0.18, work)
    return np.clip(shape, 0.0, 1.35)


def weather_gain(temp_c: np.ndarray, precip_mm: np.ndarray, wind_kmh: np.ndarray) -> np.ndarray:
    """Multiplier on the diurnal amplitude: rain, cold and wind suppress trips."""
    rain = np.clip(1.0 - 0.55 * np.minimum(precip_mm, 3.0) / 3.0, 0.35, 1.0)
    warmth = np.clip(0.52 + 0.030 * temp_c, 0.45, 1.15)
    breeze = np.clip(1.0 - 0.012 * np.maximum(wind_kmh - 15.0, 0.0), 0.72, 1.0)
    return rain * warmth * breeze


def generate(
    stations: list[Station],
    weather: list[WeatherPoint],
    start: datetime,
    end: datetime,
    step_min: int,
    rng: np.random.Generator,
) -> tuple[np.ndarray, list[Station], np.ndarray]:
    """Return (bikes[n_stations, n_steps], stations, timestamps)."""
    steps = int((end - start).total_seconds() // (step_min * 60)) + 1
    times = np.array([start + timedelta(minutes=step_min * i) for i in range(steps)])

    hours = np.array([t.hour + t.minute / 60.0 for t in times])
    dows = np.array([t.weekday() for t in times])
    hols = holiday_set(sorted({t.year for t in times}))
    is_free = np.array([(d >= 5) or (t.date() in hols) for d, t in zip(dows, times, strict=True)])

    # Hourly weather -> per-step arrays (each hour covers 60/step_min steps).
    wx = {p.ts.replace(minute=0, second=0, microsecond=0): p for p in weather}
    keys = sorted(wx)
    temp = np.empty(steps)
    precip = np.empty(steps)
    wind = np.empty(steps)
    last = wx[keys[0]]
    for i, t in enumerate(times):
        last = wx.get(t.replace(minute=0, second=0, microsecond=0), last)
        temp[i], precip[i], wind[i] = last.temp_c, last.precip_mm, last.wind_kmh
    gain = weather_gain(temp, precip, wind)

    rho = 0.94 ** (step_min / 5.0)
    bikes = np.zeros((len(stations), steps), dtype=np.int32)

    for si, st in enumerate(stations):
        dist = haversine_km(st.lat, st.lon, CENTRE_LAT, CENTRE_LON)
        central = dist < CENTRAL_RADIUS_KM
        capacity = max(st.capacity, 8)

        base = float(rng.uniform(0.16, 0.34)) if central else float(rng.uniform(0.24, 0.44))
        amp = float(rng.uniform(0.28, 0.50)) if central else float(rng.uniform(0.22, 0.42))
        # Outer stations are damped further out; central ones are busier.
        amp *= float(np.clip(1.25 - 0.10 * dist, 0.55, 1.30))

        shape = diurnal_profile(hours, is_free, central)
        level = base + amp * shape * gain

        noise = np.empty(steps)
        e = 0.0
        # innovation scale chosen so the AR(1) process has stationary sd ~= 0.030
        sigma = 0.030 * math.sqrt(1 - rho**2)
        draws = rng.normal(0.0, sigma, steps)
        for i in range(steps):
            e = rho * e + draws[i]
            noise[i] = e
        # Slow week-to-week drift.
        drift = 0.04 * np.sin(2 * np.pi * np.arange(steps) / (steps / 3.0) + rng.uniform(0, 6.28))

        occ = np.clip(level + noise + drift, 0.0, 1.0)
        bikes[si] = np.rint(occ * capacity).astype(np.int32)

    return bikes, stations, times


# ------------------------------------------------------------------------ main
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=28, help="history length in days")
    parser.add_argument("--step-min", type=int, default=5, help="observation interval, minutes")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--offline", action="store_true", help="use the committed GBFS snapshot")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper(), format="%(levelname)-7s %(message)s")
    settings = get_settings()
    store = make_store(settings)
    rng = np.random.default_rng(args.seed)

    end = datetime.now(tz=UTC).replace(second=0, microsecond=0)
    end -= timedelta(minutes=end.minute % args.step_min)
    start = end - timedelta(days=args.days)

    stations = load_stations(args.offline, settings.gbfs_discovery, settings.http_timeout_s)
    store.upsert_stations(stations)
    weather = load_weather(store, start, end, settings.lat, settings.lon)

    bikes, stations, times = generate(stations, weather, start, end, args.step_min, rng)
    log.info("generating %d stations x %d steps", bikes.shape[0], bikes.shape[1])

    written = 0
    batch: list[Observation] = []
    for si, st in enumerate(stations):
        capacity = max(st.capacity, 8)
        for ti, ts in enumerate(times):
            n = int(bikes[si, ti])
            batch.append(
                Observation(
                    station_id=st.station_id,
                    ts=ts,
                    num_bikes=n,
                    num_docks=max(capacity - n, 0),
                )
            )
            if len(batch) >= 50_000:
                written += store.insert_observations(batch)
                batch.clear()
                log.info("  ... %d observations", written)
    if batch:
        written += store.insert_observations(batch)

    lo, hi = store.observation_span()
    log.info("seeded %d observations spanning %s .. %s", written, lo, hi)
    store.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
