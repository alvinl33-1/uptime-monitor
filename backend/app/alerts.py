import logging
import os
import socket
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
from sqlalchemy import update
from sqlalchemy.orm import Session

from . import checker
from .models import Incident, Monitor, utcnow

log = logging.getLogger("alerts")

# Which Incident column records that an event's alert was claimed/sent.
ALERT_COLUMNS = {"opened": "opened_alert_at", "resolved": "resolved_alert_at"}

RETRY_WINDOW = timedelta(hours=24)


def webhook_url() -> str:
    """
    Return the configured webhook address, or "" if alerts are off.

    Spec:
    - Read ALERT_WEBHOOK_URL with os.getenv, default "", and strip spaces.
    - Read it on every call (not once at import), so tests can change it
      with monkeypatch.setenv.
    """
    url = os.getenv("ALERT_WEBHOOK_URL", "").strip()
    return url


def allow_private() -> bool:
    """
    True only if ALERT_ALLOW_PRIVATE is exactly "true" (case-insensitive).
    Anything else, including unset, means False. Read it on every call.
    """
    return os.getenv("ALERT_ALLOW_PRIVATE", "").lower() == "true"


def build_payload(event: str, monitor: Monitor, incident: Incident) -> dict:
    """
    Build the JSON body for an alert.

    Spec - return a dict with these keys:
      "alert_id":  f"incident-{incident.id}-{event}"  (a stable id so a
                   receiver can spot a repeat)
      "event":     "incident.opened" or "incident.resolved"
                   (build it from the event argument)
      "monitor":   {"id": ..., "name": ..., "url": ...}
      "incident":  {"id": ..., "started_at": ..., "resolved_at": ...}
                   datetimes as .isoformat() strings, or None when empty
      "sent_at":   current UTC time as an isoformat string
    """
    return {
        "alert_id": f"incident-{incident.id}-{event}",
        "event": f"incident.{event}",
        "monitor": {
            "id": monitor.id,
            "name": monitor.name,
            "url": monitor.url,
        },
        "incident": {
            "id": incident.id,
            "started_at": incident.started_at.isoformat() if incident.started_at else None,
            "resolved_at": incident.resolved_at.isoformat() if incident.resolved_at else None,
        },
        "sent_at": utcnow().isoformat(),
    }


def send_webhook(url: str, payload: dict, timeout: float = 5.0, transport=None) -> bool:
    """
    POST the payload to the webhook. Return True only on success.
    NEVER raise: every problem becomes False plus a log line.

    Spec:
    1. Parse the URL. If the scheme isn't "http" or "https", or there is no
       hostname, log a warning and return False.
    2. Unless allow_private() is True: resolve the host with
       checker.resolve_ips(host) (call it through the checker module so
       tests can patch it) and refuse unless there is at least one address
       and checker.is_public_ip is True for ALL of them.
       Also return False if DNS lookup fails (socket.gaierror).
       Log which rule blocked it.
    3. Send with httpx.Client(timeout=timeout, follow_redirects=False,
       transport=transport):
         POST json=payload with headers
           {"Idempotency-Key": payload["alert_id"],
            "User-Agent": checker.USER_AGENT}
    4. Success means a status code from 200 to 299 inclusive. A redirect or
       an error status is a failure, so log the status and return False.
    5. Catch httpx.HTTPError (covers timeouts and connection failures),
       log it, return False.
    6. Never log the full webhook URL. It may contain a secret token. Log
       only the hostname.
    """
    parsed = urlparse(url)
    host = parsed.hostname
    if parsed.scheme not in ("http", "https") or not host:
        log.warning("webhook URL has invalid scheme or no hostname")
        return False
    if not allow_private():
        try:
            ips = checker.resolve_ips(host)
        except socket.gaierror as exc:
            log.warning("webhook DNS lookup failed for host %s: %s", host, exc)
            return False
        if not ips or not all(checker.is_public_ip(ip) for ip in ips):
            log.warning("webhook host %s resolves to a non-public address", host)
            return False

    try:
        with httpx.Client(
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
        ) as client:
            response = client.post(
                url,
                json=payload,
                headers={
                    "Idempotency-Key": payload["alert_id"],
                    "User-Agent": checker.USER_AGENT,
                },
            )
    except httpx.HTTPError as exc:
        log.warning("webhook request failed for host %s: %s", host, exc)
        return False

    if not 200 <= response.status_code < 300:
        log.warning(
            "webhook request to host %s returned status %s",
            host,
            response.status_code,
        )
        return False
    return True


def claim_alert(db: Session, incident_id: int, event: str) -> bool:
    """
    Atomically claim the right to send one alert. True if WE got it.

    Spec:
    - column = the Incident column for this event (ALERT_COLUMNS).
    - Run ONE statement that sets the column to utcnow() only where it is
      currently NULL:
        update(Incident)
          .where(Incident.id == incident_id,
                 getattr(Incident, column).is_(None))
          .values({column: utcnow()})
    - Commit, then return True if result.rowcount == 1, else False.
    - Why: with several workers, exactly one UPDATE can find the column
      empty. The database decides the winner, not your code.
    """
    column = ALERT_COLUMNS[event]
    attribute = getattr(Incident, column)
    statement = (
        update(Incident)
        .where(Incident.id == incident_id, attribute.is_(None))
        .values({column: utcnow()})
    )

    result = db.execute(statement)
    db.commit()
    return result.rowcount == 1


def release_alert(db: Session, incident_id: int, event: str) -> None:
    """
    Give the claim back after a failed send so a retry can try again.

    Spec:
    - Set that event's column back to None for this incident, commit.
    """
    column = ALERT_COLUMNS[event]
    db.execute(
        update(Incident)
        .where(Incident.id == incident_id)
        .values({column: None})
    )
    db.commit()


def deliver(db: Session, event: str, incident: Incident, monitor: Monitor,
            transport=None) -> bool:
    """
    Send the alert for one event, at most once. True only if it was sent.

    Spec, in this order:
    1. If webhook_url() is empty, return False. Do NOT claim anything,
       so alerts sent later (after configuring a URL) aren't skipped.
    2. If event == "resolved" and incident.opened_alert_at is None, return
       False. Nobody was told about the problem, so don't announce the fix.
    3. If claim_alert(...) is False, return False (someone else has it,
       or it was already sent).
    4. db.refresh(incident), then build the payload and call send_webhook.
    5. If sending failed: release_alert(...), return False.
    6. Log an INFO line like "alert sent: incident 3 opened" and return True.
    """
    url = webhook_url()
    if not url:
        return False
    if event == "resolved" and incident.opened_alert_at is None:
        return False
    if not claim_alert(db, incident.id, event):
        return False
    db.refresh(incident)
    payload = build_payload(event, monitor, incident)
    if not send_webhook(url, payload, transport=transport):
        release_alert(db, incident.id, event)
        return False
    log.info("alert sent: incident %s %s", incident.id, event)
    return True


def retry_pending(db: Session, now: datetime | None = None, transport=None) -> int:
    """
    Re-send alerts that failed earlier. Return how many were sent.

    Spec:
    - If now is None, use datetime.now(timezone.utc).
    - If webhook_url() is empty, return 0 right away.
    - Find incidents needing an "opened" alert:
        resolved_at IS NULL, opened_alert_at IS NULL,
        started_at >= now - RETRY_WINDOW
    - Find incidents needing a "resolved" alert:
        resolved_at IS NOT NULL, opened_alert_at IS NOT NULL,
        resolved_alert_at IS NULL, resolved_at >= now - RETRY_WINDOW
    - GOTCHA: SQLite returns naive datetimes, Postgres aware ones. Do the
      time-window comparison in the SQL filter, not in Python, to avoid
      mixing them.
    - For each, load its monitor (incident.monitor) and call deliver(...).
      Count the ones that return True.
    - An incident that was resolved before anyone was alerted about it
      matches neither rule, so it never alerts. That is intended.
    - The window stops the scheduler from hammering a dead webhook forever.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if not webhook_url():
        return 0
    cutoff = now - RETRY_WINDOW
    count = 0

    opened_incidents = (
        db.query(Incident)
        .filter(
            Incident.resolved_at.is_(None),
            Incident.opened_alert_at.is_(None),
            Incident.started_at >= cutoff,
        )
        .all()
    )
    for incident in opened_incidents:
        if deliver(db, "opened", incident, incident.monitor, transport=transport):
            count += 1

    resolved_incidents = (
        db.query(Incident)
        .filter(
            Incident.resolved_at.is_not(None),
            Incident.opened_alert_at.is_not(None),
            Incident.resolved_alert_at.is_(None),
            Incident.resolved_at >= cutoff,
        )
        .all()
    )
    for incident in resolved_incidents:
        if deliver(db, "resolved", incident, incident.monitor, transport=transport):
            count += 1

    return count
