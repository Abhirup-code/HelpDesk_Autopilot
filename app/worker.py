"""Background worker: runs scheduled offboardings every minute.

Start with `python -m app.worker`. Docker Compose runs it as the `worker` service.
`python -m app.worker --check` is its health check: it passes only if the worker
finished a run recently, so a stuck or crashing worker shows up as unhealthy.
"""

import logging
import os
import sys
import time
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from app.automation import Automator, today_utc
from app.db import init_db, make_engine, make_session_factory
from app.directory import Directory, directory_from_env

log = logging.getLogger("worker")

HEARTBEAT = Path(os.environ.get("WORKER_HEARTBEAT_FILE", "/tmp/worker-heartbeat"))  # noqa: S108
INTERVAL = int(os.environ.get("WORKER_INTERVAL_SECONDS", "60"))


def beat(path: Path = HEARTBEAT) -> None:
    """Record that a run just finished."""
    path.touch()


def is_healthy(path: Path = HEARTBEAT, interval: int = INTERVAL, now: float | None = None) -> bool:
    """Healthy if the last finished run is no older than three intervals."""
    try:
        age = (now or time.time()) - path.stat().st_mtime
    except FileNotFoundError:
        return False
    return age <= 3 * interval


def run_once(factory: sessionmaker[Session], directory: Directory, base_url: str) -> int:
    with factory() as session:
        return Automator(session, directory, base_url).run_due_offboardings(today_utc())


def main() -> None:  # pragma: no cover - long-running loop, run_once is tested
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    engine = make_engine()
    init_db(engine)
    factory = make_session_factory(engine)
    directory = directory_from_env()
    base_url = os.environ.get("PUBLIC_URL", "http://127.0.0.1:8000")
    log.info("worker started, checking every %s seconds", INTERVAL)
    while True:
        try:
            count = run_once(factory, directory, base_url)
            beat()
            if count:
                log.info("processed %s offboarding(s)", count)
        except Exception:
            log.exception("worker run failed, will retry")
        time.sleep(INTERVAL)


if __name__ == "__main__":  # pragma: no cover
    if "--check" in sys.argv:
        sys.exit(0 if is_healthy() else 1)
    main()
