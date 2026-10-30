from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import Check, Incident

FAILURE_THRESHOLD = 3


def get_open_incident(db: Session, monitor_id: int) -> Incident | None:
    """
    Return the monitor's open incident, or None.

    Spec:
    - Query Incident filtered by monitor_id AND resolved_at being None.
      (In SQLAlchemy: Incident.resolved_at.is_(None). Don't write
      "== None".)
    - Return .first().
    """
    return db.query(Incident).filter(Incident.monitor_id == monitor_id, Incident.resolved_at.is_(None)).first()


def latest_checks(db: Session, monitor_id: int, limit: int) -> list[Check]:
    """
    Return the monitor's most recent checks, NEWEST FIRST.

    Spec:
    - Filter Check by monitor_id.
    - Order by checked_at descending, then id descending. (The id breaks
      ties when two checks share a timestamp.)
    - Apply .limit(limit) and return .all().
    """
    check = db.query(Check)
    latest_check = check.filter(Check.monitor_id == monitor_id).order_by(Check.checked_at.desc(), Check.id.desc())
    return latest_check.limit(limit).all()


def evaluate(db: Session, monitor_id: int) -> tuple[str | None, Incident | None]:
    """
    Look at a monitor's recent checks and open or close an incident.
    Call this right AFTER a new check has been saved.

    Returns (event, incident) where event is "opened", "resolved", or None.

    Spec:
    1. checks = latest_checks(db, monitor_id, FAILURE_THRESHOLD).
       If there are none, return (None, None).
    2. latest = checks[0]. open_incident = get_open_incident(...).

    3. If latest.status == "up":
         - if there IS an open incident: set its resolved_at to
           latest.checked_at, commit, refresh, return ("resolved", incident).
         - otherwise return (None, None).

    4. Otherwise the latest check is a failure:
         - if there is already an open incident, return (None, None).
           Nothing new to report.
         - if len(checks) < FAILURE_THRESHOLD, or any of the checks has a
           status other than "down", return (None, None).
         - otherwise the monitor has failed FAILURE_THRESHOLD times in a row.
           Create Incident(monitor_id=monitor_id,
           started_at=checks[-1].checked_at). That is the time of the FIRST
           failure in the streak, because the list is newest-first.
         - db.add it and commit inside try/except IntegrityError.
           On IntegrityError: db.rollback() and return (None, None).
           That means another worker opened an incident a moment ago, and the
           database's unique index just protected you.
         - On success: db.refresh(incident) and return ("opened", incident).

    Why one function: all the rules live in one place, so they can be
    tested without any workers, Redis, or websites.
    """
    checks = latest_checks(db, monitor_id, FAILURE_THRESHOLD)
    if not checks:
        return (None, None)
    latest = checks[0]
    open_incident = get_open_incident(db, monitor_id)
    if latest.status == "up":
        if open_incident:
            open_incident.resolved_at = latest.checked_at
            db.commit()
            db.refresh(open_incident)
            return ("resolved", open_incident)
        else:
            return (None, None)
    else:
        if open_incident:
            return (None, None)
        if len(checks) < FAILURE_THRESHOLD or any(c.status != "down" for c in checks):
            return (None, None)
        incident = Incident(monitor_id=monitor_id, started_at=checks[-1].checked_at)
        db.add(incident)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return (None, None)
        db.refresh(incident)
        return ("opened", incident)