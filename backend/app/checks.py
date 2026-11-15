import logging
from collections.abc import Mapping

from sqlalchemy.orm import Session

from . import alerts, incidents, statuspage
from .models import Check, Monitor

log = logging.getLogger("checks")


def record_check(
    db: Session,
    monitor: Monitor,
    result: Mapping[str, object],
    alert_transport=None,
) -> Check:
    """Persist a check and apply the incident/alert state transition.

    Both scheduled checks and manual checks must use this path. A check is
    committed before incident evaluation so the evaluator can use the new
    result as part of its consecutive-failure window.
    """
    check = Check(monitor_id=monitor.id, **result)
    db.add(check)
    db.commit()
    db.refresh(check)

    event, incident = incidents.evaluate(db, monitor.id)
    if event is not None and incident is not None:
        try:
            alerts.deliver(
                db,
                event,
                incident,
                monitor,
                transport=alert_transport,
            )
        except Exception:
            # A webhook failure must not turn a saved check into a failed job.
            log.exception(
                "alert processing failed for monitor %s incident %s",
                monitor.id,
                incident.id,
            )

    # Checks and incident transitions both affect the public status document.
    statuspage.invalidate()
    return check
