import logging
import signal
from collections.abc import Callable

from . import jobs
from .checker import check_url
from .db import SessionLocal
from .models import Check, Monitor

log = logging.getLogger("worker")


def process_monitor(monitor_id: int, db) -> bool:
    """Check one monitor and save its result.

    Return True when a monitor was found and its check was saved. Deleted
    monitors are ignored because their queued jobs may outlive the monitor.
    """
    monitor = db.get(Monitor, monitor_id)
    if monitor is None:
        log.warning("monitor %s no longer exists; skipping job", monitor_id)
        return False

    result = check_url(monitor.url)
    check = Check(monitor_id=monitor.id, **result)
    db.add(check)
    db.commit()
    log.info(
        "checked monitor %s: status=%s status_code=%s",
        monitor.id,
        result["status"],
        result["status_code"],
    )
    return True


def process_next_job(
    db_factory: Callable[[], object] | None = None,
    timeout: int = 5,
) -> bool | None:
    """Process one queued job.

    Return None when no job arrives before the timeout, otherwise return the
    result of processing the job.
    """
    monitor_id = jobs.dequeue_check(timeout=timeout)
    if monitor_id is None:
        return None

    factory = db_factory or SessionLocal
    if factory is None:
        raise RuntimeError("DATABASE_URL is not configured")

    with factory() as db:
        return process_monitor(monitor_id, db)


def run_forever(timeout: int = 5) -> None:
    """Consume queued checks until SIGTERM or SIGINT is received."""
    stop = {"requested": False}

    def handle_signal(signum, frame):
        stop["requested"] = True
        log.info("shutdown requested")

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    log.info("worker started")
    while not stop["requested"]:
        try:
            process_next_job(timeout=timeout)
        except Exception:
            log.exception("error processing check job")
    log.info("worker stopped")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(message)s",
    )
    if SessionLocal is None:
        log.error("DATABASE_URL was never set")
        raise SystemExit(1)
    run_forever()


if __name__ == "__main__":
    main()
