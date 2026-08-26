"""Locust load profile for the UrbanPulse API.

Run against a standalone API (no Docker needed):

    make api            # terminal 1
    make loadtest       # terminal 2  -> results/locust_*.csv

The task weights approximate dashboard traffic: the map polls /stations most
often, drill-downs pull history and forecasts, and the replay slider fires
/replay for scattered past instants (which is what stresses the cache).
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from locust import HttpUser, between, events, task

STATION_IDS: list[str] = []
REPLAY_SPAN: tuple[datetime, datetime] | None = None


@events.test_start.add_listener
def _discover(environment, **_kwargs) -> None:
    """Read the real station list and observation window before the run starts."""
    global REPLAY_SPAN
    host = environment.host or "http://127.0.0.1:8000"
    import httpx

    with httpx.Client(base_url=host, timeout=30.0) as client:
        stations = client.get("/stations").json()
        STATION_IDS.extend(s["station_id"] for s in stations)
        span = client.get("/meta/span").json()
        if span["first"] and span["last"]:
            REPLAY_SPAN = (
                datetime.fromisoformat(span["first"]),
                datetime.fromisoformat(span["last"]),
            )
    print(f"[locust] discovered {len(STATION_IDS)} stations, replay span {REPLAY_SPAN}")


def random_station() -> str:
    return random.choice(STATION_IDS) if STATION_IDS else "1"  # noqa: S311 - load shaping


class DashboardUser(HttpUser):
    """One open dashboard tab."""

    wait_time = between(0.1, 0.5)

    @task(10)
    def map_refresh(self) -> None:
        self.client.get("/stations", name="/stations")

    @task(5)
    def station_history(self) -> None:
        sid = random_station()
        self.client.get(f"/stations/{sid}/history?hours=24", name="/stations/{id}/history")

    @task(5)
    def station_forecast(self) -> None:
        sid = random_station()
        self.client.get(f"/predict/{sid}", name="/predict/{id}")

    @task(2)
    def replay_scrub(self) -> None:
        if REPLAY_SPAN is None:
            return
        lo, hi = REPLAY_SPAN
        offset = random.random() * (hi - lo).total_seconds()  # noqa: S311 - load shaping
        ts = lo + timedelta(seconds=offset)
        self.client.get("/replay", params={"ts": ts.astimezone(UTC).isoformat()}, name="/replay")

    @task(1)
    def health(self) -> None:
        self.client.get("/health", name="/health")
