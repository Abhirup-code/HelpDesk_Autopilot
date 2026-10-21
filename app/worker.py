"""Background worker: runs scheduled offboardings every minute.

Start with `python -m app.worker`. Docker Compose runs it as the `worker` service.
"""

import logging
import os
import time

from sqlalchemy.orm import Session, sessionmaker

from app.automation import Automator, today_utc
from app.db import init_db, make_engine, make_session_factory
from app.directory import Directory, directory_from_env

log = logging.getLogger("worker")


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
    interval = int(os.environ.get("WORKER_INTERVAL_SECONDS", "60"))
    log.info("worker started, checking every %s seconds", interval)
    while True:
        try:
            count = run_once(factory, directory, base_url)
            if count:
                log.info("processed %s offboarding(s)", count)
        except Exception:
            log.exception("worker run failed, will retry")
        time.sleep(interval)


if __name__ == "__main__":  # pragma: no cover
    main()
