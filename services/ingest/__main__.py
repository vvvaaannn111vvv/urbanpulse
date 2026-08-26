"""Entry point for the ingestion service.

    python -m services.ingest                # poller + consumer in one process
    python -m services.ingest --role poller  # produce only
    python -m services.ingest --role consumer

Under ``URBANPULSE_BACKEND=sqlite`` the bus is an in-process queue, so both roles
must share a process (the default ``both``).
"""

from __future__ import annotations

import argparse
import logging
import signal
import threading
from types import FrameType

from services.common.bus import make_bus
from services.common.config import get_settings
from services.common.storage import make_store
from services.ingest.consumer import ObservationConsumer
from services.ingest.poller import Poller


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="services.ingest")
    parser.add_argument("--role", choices=("both", "poller", "consumer"), default="both")
    parser.add_argument("--max-polls", type=int, default=None, help="stop after N polls")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    )
    settings = get_settings()
    store = make_store(settings)
    log = logging.getLogger("urbanpulse.ingest")
    log.info(
        "backend=%s role=%s interval=%ss", settings.backend, args.role, settings.poll_interval_s
    )

    threads: list[threading.Thread] = []
    poller: Poller | None = None
    consumer: ObservationConsumer | None = None

    if args.role in ("both", "consumer"):
        consumer = ObservationConsumer(make_bus("consumer", settings), store)
        threads.append(threading.Thread(target=consumer.run, name="consumer", daemon=True))
    if args.role in ("both", "poller"):
        poller = Poller(make_bus("producer", settings), store, settings)
        threads.append(
            threading.Thread(
                target=poller.run, kwargs={"max_polls": args.max_polls}, name="poller", daemon=True
            )
        )

    stopping = threading.Event()

    def _shutdown(signum: int, frame: FrameType | None) -> None:
        log.info("signal %s received, shutting down", signum)
        stopping.set()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    for t in threads:
        t.start()
    try:
        while not stopping.is_set() and any(t.is_alive() for t in threads):
            stopping.wait(0.5)
            if poller is not None and args.max_polls is not None and poller.polls >= args.max_polls:
                stopping.set()
    finally:
        if poller is not None:
            poller.stop()
        if consumer is not None:
            consumer.stop()
        for t in threads:
            t.join(timeout=5.0)
        store.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
