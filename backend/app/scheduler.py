import logging
import signal
import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from . import jobs
from .db import SessionLocal
from .models import Check, Monitor

log = logging.getLogger("scheduler")

POLL_SECONDS = 5


def find_due_monitors(db: Session, now: datetime) -> list[Monitor]:
    """
    Return every monitor that is due for a check.

    Spec:
    - Loop over all monitors.
    - For each, find the checked_at of its most recent check
      (query Check, filter by monitor_id, order by checked_at descending,
      take the first row).
    - A monitor is due if:
        * it has no checks at all, OR
        * (now - last_checked_at) is at least interval_seconds.
    - GOTCHA: SQLite (used in tests) returns datetimes WITHOUT a timezone,
      while Postgres returns them WITH one. Comparing the two kinds raises
      a TypeError. If last_checked_at.tzinfo is None, treat it as UTC
      before comparing: last_checked_at.replace(tzinfo=timezone.utc).
    - Return the due Monitor objects, in id order.
    - One query per monitor is fine for now. (Optional later upgrade:
      do it all in one query with a grouped subquery.)
    """
    due_monitors = []
    for monitor in db.query(Monitor).all():
        last_check = db.query(Check).filter_by(monitor_id=monitor.id).order_by(Check.checked_at.desc()).first()
        if last_check is None:
            due_monitors.append(monitor)
        else:
            last_checked_at = last_check.checked_at
            if last_checked_at.tzinfo is None:
                last_checked_at = last_checked_at.replace(tzinfo=timezone.utc)
            if (now - last_checked_at).total_seconds() >= monitor.interval_seconds:
                due_monitors.append(monitor)
    return due_monitors


def claim(monitor_id: int, ttl_seconds: int) -> bool:
    """
    Try to mark a monitor as "already scheduled". Return True if we got it.

    Spec:
    - Get the client with jobs.get_redis(). Call it as jobs.get_redis(),
      NOT "from jobs import get_redis", so tests can swap in a fake.
    - Use the key f"scheduled:{monitor_id}".
    - Call set(key, "1", nx=True, ex=ttl_seconds).
        nx=True means "only set if the key doesn't exist yet".
        ex=ttl_seconds makes the key delete itself after that many seconds.
    - Redis returns True if it set the key, or None if it already existed.
      Return a real bool: True if set, False otherwise.
    - Why it matters: if the scheduler crashes or a job is lost, the key
      expires and the monitor gets scheduled again. It heals itself.
    """
    redis = jobs.get_redis()
    key = f"scheduled:{monitor_id}"
    result = redis.set(key, "1", nx=True, ex=ttl_seconds)
    return bool(result)


def run_once(db: Session, now: datetime | None = None) -> int:
    """
    Do one scheduling pass. Return how many jobs were enqueued.

    Spec:
    - If now is None, use datetime.now(timezone.utc).
    - Call find_due_monitors(db, now).
    - For each due monitor:
        * call claim(monitor.id, monitor.interval_seconds)
        * only if claim returned True, call jobs.enqueue_check(monitor.id)
          and count it.
    - If the count is above zero, log one INFO line, e.g. "enqueued 3 checks".
    - Return the count.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    due_monitors = find_due_monitors(db, now)
    count = 0
    for monitor in due_monitors:
        if claim(monitor.id, monitor.interval_seconds):
            jobs.enqueue_check(monitor.id)
            count += 1
    if count > 0:
        log.info(f"enqueued {count} checks")
    return count


def run_forever(poll_seconds: int = POLL_SECONDS) -> None:
    """
    Run run_once in a loop until told to stop.

    Spec:
    - Create a variable (for example a list or a small dict) that a signal
      handler can flip to mean "stop". A plain bool won't work because the
      inner function would only change its own copy.
    - Register a handler for signal.SIGTERM and signal.SIGINT that flips it.
      Kubernetes sends SIGTERM when it shuts a container down, so this lets
      the scheduler exit cleanly instead of being killed.
    - Loop until stop is requested:
        * open a session: with SessionLocal() as db:
        * call run_once(db)
        * wrap that in try/except Exception and call log.exception(...) so
          one bad pass (for example Redis briefly down) never kills the loop
        * then wait poll_seconds. Do it as a loop of time.sleep(1) so a stop
          request is noticed within about a second, not after the full wait.
    - Log "scheduler started" before the loop and "scheduler stopped" after.
    """
    stop = {"requested": False}

    def handle_signal(signum, frame):
        stop["requested"] = True

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    log.info("scheduler started")
    while not stop["requested"]:
        try:
            with SessionLocal() as db:
                run_once(db)
        except Exception:
            log.exception("error in run_once")
        for _ in range(poll_seconds):
            if stop["requested"]:
                break
            time.sleep(1)
    log.info("scheduler stopped")


def main() -> None:
    """
    Entry point.

    Spec:
    - Configure logging with logging.basicConfig(level=logging.INFO,
      format="%(asctime)s %(name)s %(message)s"). Logs go to the screen,
      which Docker collects.
    - If SessionLocal.kw["bind"] is None, DATABASE_URL was never set.
      Log an error and exit with a non-zero code (raise SystemExit(1)).
    - Otherwise call run_forever().
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if SessionLocal.kw["bind"] is None:
        log.error("DATABASE_URL was never set")
        raise SystemExit(1)
    run_forever()


if __name__ == "__main__":
    main()