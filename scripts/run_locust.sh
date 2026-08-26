#!/usr/bin/env bash
# Load-test a already-running standalone API and record the raw output.
#
#   make api          # terminal 1
#   make loadtest     # terminal 2
#
# Writes results/locust_stats.csv (raw locust output) and results/locust_run.json
# (the run parameters and the machine, so the numbers can be interpreted).
set -euo pipefail

HOST="${HOST:-http://127.0.0.1:8000}"
USERS="${USERS:-50}"
SPAWN_RATE="${SPAWN_RATE:-10}"
RUN_TIME="${RUN_TIME:-60s}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$ROOT/results"

if ! curl -fsS "$HOST/health" >/dev/null; then
  echo "API is not answering at $HOST — start it with 'make api' first." >&2
  exit 1
fi

locust -f "$ROOT/locustfile.py" \
  --headless -u "$USERS" -r "$SPAWN_RATE" -t "$RUN_TIME" \
  --host "$HOST" --csv "$ROOT/results/locust" --only-summary

python - "$ROOT" "$HOST" "$USERS" "$SPAWN_RATE" "$RUN_TIME" <<'PY'
import csv, json, platform, sys
from datetime import datetime

root, host, users, spawn, run_time = sys.argv[1:6]
with open(f"{root}/results/locust_stats.csv", newline="") as fh:
    rows = list(csv.DictReader(fh))
agg = next(r for r in rows if r["Name"] == "Aggregated")

payload = {
    "measured_at": datetime.now().astimezone().isoformat(),
    "host": host,
    "users": int(users),
    "spawn_rate": float(spawn),
    "run_time": run_time,
    "machine": f"{platform.system()} {platform.machine()} python{platform.python_version()}",
    "server": "uvicorn, 1 worker, sqlite backend, in-process cache",
    "requests": int(agg["Request Count"]),
    "failures": int(agg["Failure Count"]),
    "requests_per_second": round(float(agg["Requests/s"]), 2),
    "latency_ms": {
        "p50": float(agg["50%"]),
        "p95": float(agg["95%"]),
        "p99": float(agg["99%"]),
        "max": round(float(agg["Max Response Time"]), 1),
    },
    "per_endpoint": {
        r["Name"]: {
            "requests": int(r["Request Count"]),
            "rps": round(float(r["Requests/s"]), 2),
            "p50_ms": float(r["50%"]),
            "p95_ms": float(r["95%"]),
        }
        for r in rows
        if r["Name"] != "Aggregated" and r["Name"]
    },
}
with open(f"{root}/results/locust_run.json", "w") as fh:
    json.dump(payload, fh, indent=2)
    fh.write("\n")
print(json.dumps(payload, indent=2))
PY
