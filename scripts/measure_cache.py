#!/usr/bin/env python
"""Measure the API's real cache hit rate and write it to results/cache_hitrate.json.

The traffic mix mirrors ``locustfile.py``: repeated map refreshes, drill-downs on
a small set of stations and a few replay scrubs. Counters come from the API's own
/metrics/cache endpoint, so the number is measured, not modelled.

    python scripts/measure_cache.py --host http://127.0.0.1:8000 --requests 600
"""

from __future__ import annotations

import argparse
import json
import random
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx

RESULTS = Path(__file__).resolve().parents[1] / "results" / "cache_hitrate.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="http://127.0.0.1:8000")
    parser.add_argument("--requests", type=int, default=600)
    parser.add_argument("--stations", type=int, default=12, help="distinct stations to drill into")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)

    rng = random.Random(args.seed)
    with httpx.Client(base_url=args.host, timeout=30.0) as client:
        client.post("/metrics/cache/reset").raise_for_status()
        ttls = client.get("/metrics/cache").json()["ttl_seconds"]

        all_ids = [s["station_id"] for s in client.get("/stations").json()]
        hot = all_ids[: args.stations]
        span = client.get("/meta/span").json()
        lo = datetime.fromisoformat(span["first"])
        hi = datetime.fromisoformat(span["last"])
        client.post("/metrics/cache/reset").raise_for_status()

        started = time.perf_counter()
        # Weights match the locust task mix: 10 map / 5 history / 5 predict / 2 replay.
        choices = ["stations"] * 10 + ["history"] * 5 + ["predict"] * 5 + ["replay"] * 2
        for _ in range(args.requests):
            kind = rng.choice(choices)
            if kind == "stations":
                client.get("/stations")
            elif kind == "history":
                client.get(f"/stations/{rng.choice(hot)}/history", params={"hours": 24})
            elif kind == "predict":
                client.get(f"/predict/{rng.choice(hot)}")
            else:
                offset = rng.random() * (hi - lo).total_seconds()
                # Snap to a 15-minute bucket: that is the cache key granularity.
                ts = lo + timedelta(seconds=offset - offset % 900)
                client.get("/replay", params={"ts": ts.isoformat()})
        elapsed = time.perf_counter() - started

        stats = client.get("/metrics/cache").json()

    payload = {
        "measured_at": datetime.now().astimezone().isoformat(),
        "host": args.host,
        "requests_issued": args.requests,
        "distinct_stations": len(hot),
        "wall_seconds": round(elapsed, 3),
        "requests_per_second": round(args.requests / elapsed, 1),
        "cache_backend": stats["backend"],
        "hits": stats["hits"],
        "misses": stats["misses"],
        "hit_rate": stats["hit_rate"],
        "ttl_seconds": ttls,
        "traffic_mix": "10 stations / 5 history / 5 predict / 2 replay (same weights as locustfile)",
    }
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
