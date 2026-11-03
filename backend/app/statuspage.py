import json
import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from . import incidents, jobs
from .models import Check, Incident, Monitor

log = logging.getLogger("statuspage")

CACHE_KEY = "statuspage:v1"
DEFAULT_CACHE_SECONDS = 15


def cache_seconds() -> int:
    """
    How long a cached status stays valid.

    Spec:
    - Read STATUS_CACHE_SECONDS with os.getenv on every call.
    - If it isn't set, isn't a whole number, or is less than 1, return
      DEFAULT_CACHE_SECONDS. Never raise.
    """
    if (s := os.getenv("STATUS_CACHE_SECONDS")) is None:
        return DEFAULT_CACHE_SECONDS
    try:
        n = int(s)
        if n < 1:
            return DEFAULT_CACHE_SECONDS
        return n
    except ValueError:
        return DEFAULT_CACHE_SECONDS


def monitor_state(has_open_incident: bool, latest_status: str | None) -> str:
    """
    Decide one monitor's public state. A pure function: no database.

    Spec, checked in this order:
    - has_open_incident is True        -> "down"
    - latest_status is None            -> "unknown"   (never checked)
    - latest_status == "down"          -> "degraded"  (failing, but not yet
                                          an incident)
    - anything else                    -> "up"
    """
    if has_open_incident:
        return "down"
    if latest_status is None:
        return "unknown"
    if latest_status == "down":
        return "degraded"
    return "up"


def overall_state(states: list[str]) -> str:
    """
    Combine every monitor's state into one headline word. Also pure.

    Spec, checked in this order:
    - empty list, or every state is "unknown"       -> "no_data"
    - every state is "down"                         -> "major_outage"
    - at least one "down"                           -> "partial_outage"
    - at least one "degraded"                       -> "degraded"
    - otherwise                                     -> "operational"
    """
    if not states or all(s == "unknown" for s in states):
        return "no_data"
    if all(s == "down" for s in states):
        return "major_outage"
    if any(s == "down" for s in states):
        return "partial_outage"
    if any(s == "degraded" for s in states):
        return "degraded"
    return "operational"


def uptime_by_monitor(db: Session, since: datetime) -> dict[int, float]:
    """
    Percentage of checks that were "up" since a given time, per monitor.

    Spec:
    - ONE grouped query, not one query per monitor:
        select Check.monitor_id,
               func.count(Check.id),
               func.sum(case((Check.status == "up", 1), else_=0))
        where Check.checked_at >= since
        group by Check.monitor_id
    - Return {monitor_id: round(up_count / total * 100, 2)}.
    - Monitors with no checks in the window are simply absent from the dict.
    - Do the time comparison in the SQL filter, not in Python.
    """
    result = (
        db.execute(
            select(
            Check.monitor_id,
            func.count(Check.id).label("total"),
            func.sum(case((Check.status == "up", 1), else_=0)).label("up_count"),
            )
            .where(Check.checked_at >= since)
            .group_by(Check.monitor_id)
        )
    )
    return {row.monitor_id: round(row.up_count / row.total * 100, 2) for row in result}


def build_status(db: Session, now: datetime | None = None) -> dict:
    """
    Build the full status document from the database. No caching here.

    Spec:
    - If now is None, use datetime.now(timezone.utc).
    - uptimes = uptime_by_monitor(db, now - timedelta(hours=24)).
    - For each monitor, in id order:
        * latest check: query Check for this monitor, ordered by checked_at
          descending then id descending, first row (or None)
        * open incident: incidents.get_open_incident(db, monitor.id)
          (call it through the module so tests can patch it)
        * state = monitor_state(open incident is not None,
                                latest check's status, or None)
        * entry = {"id", "name", "status": state,
                   "uptime_24h": uptimes.get(monitor.id),   # None if absent
                   "response_time_ms": latest's value or None,
                   "last_checked_at": latest.checked_at.isoformat() or None}
      One query per monitor is fine here. The cache makes it cheap.
    - open_incidents: every Incident with resolved_at None, newest first
      (started_at descending, then id descending), each as
      {"id", "monitor_id", "monitor_name", "started_at" (isoformat)}.
      You can get the name through incident.monitor.
    - Return {"overall": overall_state(all states),
              "generated_at": now.isoformat(),
              "monitors": [...], "open_incidents": [...]}.
    - SECURITY: never include monitor.url anywhere in the result.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    uptimes = uptime_by_monitor(db, now - timedelta(hours=24))
    monitors = []
    for monitor in db.scalars(select(Monitor).order_by(Monitor.id)):
        latest = (
            db.scalars(
                select(Check)
                .where(Check.monitor_id == monitor.id)
            .order_by(Check.checked_at.desc(), Check.id.desc())
                .limit(1)
            )
            .first()
        )
        open_incident = incidents.get_open_incident(db, monitor.id)
        state = monitor_state(open_incident is not None, latest.status if latest else None)
        monitors.append({
            "id": monitor.id,
            "name": monitor.name,
            "status": state,
            "uptime_24h": uptimes.get(monitor.id),
            "response_time_ms": latest.response_time_ms if latest else None,
            "last_checked_at": latest.checked_at.isoformat() if latest else None,
        })

    open_incidents = []
    for incident in db.scalars(
        select(Incident)
        .where(Incident.resolved_at.is_(None))
        .order_by(Incident.started_at.desc(), Incident.id.desc())
    ):
        open_incidents.append({
            "id": incident.id,
            "monitor_id": incident.monitor_id,
            "monitor_name": incident.monitor.name,
            "started_at": incident.started_at.isoformat()
        })

    return {
        "overall": overall_state([m["status"] for m in monitors]),
        "generated_at": now.isoformat(),
        "monitors": monitors,
        "open_incidents": open_incidents,
    }


def get_status(db: Session, now: datetime | None = None) -> tuple[dict, str]:
    """
    Return (status_document, cache_state). cache_state is "HIT", "MISS",
    or "BYPASS".

    Spec:
    1. Try to read CACHE_KEY with jobs.get_redis().get(CACHE_KEY)
       (call through the jobs module so tests can swap it).
       - If it returns text, parse it with json.loads and return
         (data, "HIT"). If parsing fails (corrupt value), log a warning and
         treat it as a miss.
       - If the Redis call itself raises, log a warning and return
         (build_status(db, now), "BYPASS"). A Redis outage must never take
         the status page down. Don't try to write the cache in that case.
    2. On a miss: data = build_status(db, now). Save it with
       set(CACHE_KEY, json.dumps(data), ex=cache_seconds()).
       If the save raises, log a warning and carry on.
    3. Return (data, "MISS").
    """
    try:
        cached = jobs.get_redis().get(CACHE_KEY)
        if cached is not None:
            try:
                data = json.loads(cached)
                return data, "HIT"
            except json.JSONDecodeError:
                log.warning("Corrupt cache value for %s", CACHE_KEY)
    except Exception:
        log.warning("Redis error when reading %s", CACHE_KEY, exc_info=True)
        return build_status(db, now), "BYPASS"
    
    data = build_status(db, now)
    try:
        jobs.get_redis().set(
            CACHE_KEY,
            json.dumps(data),
            ex=cache_seconds(),
        )
    except Exception:
        log.warning("Redis error when writing %s", CACHE_KEY, exc_info=True)
    return data, "MISS"


def invalidate() -> None:
    """
    Delete the cached status so the next request rebuilds it.
    Call this whenever something changes that visitors should see at once.

    Spec:
    - delete CACHE_KEY through jobs.get_redis().
    - NEVER raise. If anything fails, log a warning and return.
      Cache cleanup must not break the action that triggered it.
    """
    try:
        jobs.get_redis().delete(CACHE_KEY)
    except Exception:
        log.warning("Redis error when deleting %s", CACHE_KEY, exc_info=True)
        return
    
