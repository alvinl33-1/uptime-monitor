from datetime import datetime, timedelta, timezone
import json
import socket

import httpx
import pytest

from app import alerts, checker
from app.models import Incident, Monitor

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
URL = "https://hooks.example.com/alert"


@pytest.fixture(autouse=True)
def public_dns_and_url(monkeypatch):
    monkeypatch.setattr(checker, "resolve_ips", lambda host: ["8.8.8.8"])
    monkeypatch.setenv("ALERT_WEBHOOK_URL", URL)
    monkeypatch.delenv("ALERT_ALLOW_PRIVATE", raising=False)


def ok_transport(status=200, seen=None):
    def handler(request):
        if seen is not None:
            seen.append(request)
        return httpx.Response(status)

    return httpx.MockTransport(handler)


def add_incident(db, resolved=False, opened_alerted=False, started=NOW):
    monitor = Monitor(name="Site", url="https://site.example", interval_seconds=60)
    db.add(monitor)
    db.commit()
    incident = Incident(
        monitor_id=monitor.id,
        started_at=started,
        resolved_at=(started + timedelta(minutes=5)) if resolved else None,
        opened_alert_at=started if opened_alerted else None,
    )
    db.add(incident)
    db.commit()
    return monitor, incident


# ---- build_payload ----

def test_payload_for_opened_incident(db_session):
    # add_incident, build_payload("opened", ...).
    # Check alert_id == f"incident-{incident.id}-opened", event ==
    # "incident.opened", the monitor fields, and resolved_at is None.
    monitor, incident = add_incident(db_session)
    payload = alerts.build_payload("opened", monitor, incident)

    assert payload["alert_id"] == f"incident-{incident.id}-opened"
    assert payload["event"] == "incident.opened"
    assert payload["monitor"] == {
        "id": monitor.id,
        "name": monitor.name,
        "url": monitor.url,
    }
    assert payload["incident"]["id"] == incident.id
    assert payload["incident"]["started_at"] == incident.started_at.isoformat()
    assert payload["incident"]["resolved_at"] is None
    assert isinstance(payload["sent_at"], str)


def test_payload_for_resolved_incident(db_session):
    # Same for "resolved": event "incident.resolved" and resolved_at is a string.
    monitor, incident = add_incident(db_session, resolved=True)
    payload = alerts.build_payload("resolved", monitor, incident)

    assert payload["alert_id"] == f"incident-{incident.id}-resolved"
    assert payload["event"] == "incident.resolved"
    assert payload["incident"]["resolved_at"] == incident.resolved_at.isoformat()


# ---- send_webhook ----

def test_success_returns_true_and_sends_json_and_key():
    # seen = []. send_webhook(URL, payload, transport=ok_transport(200, seen)).
    # Assert True, one request, a POST, the Idempotency-Key header equals
    # the payload's alert_id, and the JSON body matches the payload.
    seen = []
    payload = {"alert_id": "test-alert", "value": 123}

    assert alerts.send_webhook(
        URL,
        payload,
        transport=ok_transport(200, seen),
    ) is True
    assert len(seen) == 1
    request = seen[0]
    assert request.method == "POST"
    assert request.headers["Idempotency-Key"] == "test-alert"
    assert json.loads(request.content) == payload


def test_error_status_returns_false():
    # 500 -> False.
    assert alerts.send_webhook(
        URL,
        {"alert_id": "test-alert"},
        transport=ok_transport(500),
    ) is False


def test_redirect_is_a_failure():
    # 302 -> False (we never follow redirects).
    assert alerts.send_webhook(
        URL,
        {"alert_id": "test-alert"},
        transport=ok_transport(302),
    ) is False


def test_timeout_returns_false_and_does_not_raise():
    # A transport whose handler raises httpx.ConnectTimeout -> False.
    def timeout_handler(request):
        raise httpx.ConnectTimeout("timed out")

    assert alerts.send_webhook(
        URL,
        {"alert_id": "test-alert"},
        transport=httpx.MockTransport(timeout_handler),
    ) is False


def test_non_http_scheme_is_rejected():
    # send_webhook("ftp://hooks.example.com/x", ...) -> False,
    # and the transport handler is never called (use seen).
    seen = []
    assert alerts.send_webhook(
        "ftp://hooks.example.com/x",
        {"alert_id": "test-alert"},
        transport=ok_transport(200, seen),
    ) is False
    assert seen == []


def test_private_address_is_blocked_by_default(monkeypatch):
    # monkeypatch.setattr(checker, "resolve_ips", lambda host: ["10.0.0.5"]).
    # send_webhook -> False and no request was made.
    monkeypatch.setattr(checker, "resolve_ips", lambda host: ["10.0.0.5"])
    seen = []
    assert alerts.send_webhook(
        URL,
        {"alert_id": "test-alert"},
        transport=ok_transport(200, seen),
    ) is False
    assert seen == []


def test_private_address_allowed_when_flag_is_true(monkeypatch):
    # Same private address, but monkeypatch.setenv("ALERT_ALLOW_PRIVATE", "true").
    # -> True.
    monkeypatch.setattr(checker, "resolve_ips", lambda host: ["10.0.0.5"])
    monkeypatch.setenv("ALERT_ALLOW_PRIVATE", "true")
    assert alerts.send_webhook(
        URL,
        {"alert_id": "test-alert"},
        transport=ok_transport(200),
    ) is True


def test_flag_must_be_exactly_true(monkeypatch):
    # For each of "1", "yes", "false", "": allow_private() is False.
    # For "TRUE": allow_private() is True.
    for value in ("1", "yes", "false", ""):
        monkeypatch.setenv("ALERT_ALLOW_PRIVATE", value)
        assert alerts.allow_private() is False

    monkeypatch.setenv("ALERT_ALLOW_PRIVATE", "TRUE")
    assert alerts.allow_private() is True


def test_dns_failure_returns_false(monkeypatch):
    # Make checker.resolve_ips raise socket.gaierror -> False.
    def fail_dns(host):
        raise socket.gaierror("DNS failed")

    monkeypatch.setattr(checker, "resolve_ips", fail_dns)
    assert alerts.send_webhook(
        URL,
        {"alert_id": "test-alert"},
        transport=ok_transport(200),
    ) is False


# ---- claim / release ----

def test_claim_succeeds_once(db_session):
    # claim_alert twice for the same incident and event: True then False.
    _, incident = add_incident(db_session)
    assert alerts.claim_alert(db_session, incident.id, "opened") is True
    assert alerts.claim_alert(db_session, incident.id, "opened") is False


def test_claims_for_different_events_are_independent(db_session):
    # Claim "opened" and then "resolved": both True.
    _, incident = add_incident(db_session)
    assert alerts.claim_alert(db_session, incident.id, "opened") is True
    assert alerts.claim_alert(db_session, incident.id, "resolved") is True


def test_release_allows_claiming_again(db_session):
    # claim, release, claim again -> True, True.
    _, incident = add_incident(db_session)
    assert alerts.claim_alert(db_session, incident.id, "opened") is True
    alerts.release_alert(db_session, incident.id, "opened")
    assert alerts.claim_alert(db_session, incident.id, "opened") is True


# ---- deliver ----

def test_deliver_without_a_webhook_url_does_nothing(db_session, monkeypatch):
    # monkeypatch.setenv("ALERT_WEBHOOK_URL", ""). deliver -> False,
    # and opened_alert_at is STILL None (nothing was claimed).
    _, incident = add_incident(db_session)
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "")
    assert alerts.deliver(
        db_session, "opened", incident, incident.monitor
    ) is False
    db_session.refresh(incident)
    assert incident.opened_alert_at is None


def test_deliver_sends_and_marks_the_incident(db_session):
    # deliver("opened", ..., transport=ok_transport()) -> True,
    # and after db_session.refresh(incident), opened_alert_at is set.
    monitor, incident = add_incident(db_session)
    assert alerts.deliver(
        db_session,
        "opened",
        incident,
        monitor,
        transport=ok_transport(),
    ) is True
    db_session.refresh(incident)
    assert incident.opened_alert_at is not None


def test_deliver_twice_sends_only_once(db_session):
    # seen = []. Call deliver twice. First True, second False,
    # and len(seen) == 1.
    monitor, incident = add_incident(db_session)
    seen = []
    assert alerts.deliver(
        db_session, "opened", incident, monitor,
        transport=ok_transport(200, seen),
    ) is True
    assert alerts.deliver(
        db_session, "opened", incident, monitor,
        transport=ok_transport(200, seen),
    ) is False
    assert len(seen) == 1


def test_failed_delivery_releases_the_claim(db_session):
    # deliver with ok_transport(500) -> False and opened_alert_at is None
    # again. Then deliver with ok_transport(200) -> True.
    monitor, incident = add_incident(db_session)
    assert alerts.deliver(
        db_session, "opened", incident, monitor,
        transport=ok_transport(500),
    ) is False
    db_session.refresh(incident)
    assert incident.opened_alert_at is None
    assert alerts.deliver(
        db_session, "opened", incident, monitor,
        transport=ok_transport(200),
    ) is True


def test_resolved_alert_requires_the_opened_alert(db_session):
    # Incident resolved but never alerted: deliver("resolved", ...) -> False,
    # nothing sent.
    monitor, incident = add_incident(db_session, resolved=True)
    assert alerts.deliver(
        db_session, "resolved", incident, monitor,
        transport=ok_transport(),
    ) is False
    db_session.refresh(incident)
    assert incident.resolved_alert_at is None


def test_resolved_alert_is_sent_after_opened(db_session):
    # Incident resolved with opened_alerted=True: deliver("resolved") -> True,
    # and resolved_alert_at is set.
    monitor, incident = add_incident(
        db_session, resolved=True, opened_alerted=True
    )
    assert alerts.deliver(
        db_session, "resolved", incident, monitor,
        transport=ok_transport(),
    ) is True
    db_session.refresh(incident)
    assert incident.resolved_alert_at is not None


# ---- retry_pending ----

def test_retry_sends_a_missed_opened_alert(db_session):
    # Open incident, never alerted. retry_pending(db_session, NOW, ok_transport())
    # returns 1 and opened_alert_at is set.
    monitor, incident = add_incident(db_session)
    assert alerts.retry_pending(
        db_session, NOW, ok_transport()
    ) == 1
    db_session.refresh(incident)
    assert incident.opened_alert_at is not None


def test_retry_does_not_resend_alerts_already_sent(db_session):
    # Open incident with opened_alerted=True -> retry_pending returns 0.
    add_incident(db_session, opened_alerted=True)
    assert alerts.retry_pending(
        db_session, NOW, ok_transport()
    ) == 0


def test_retry_sends_missed_resolved_alert(db_session):
    # resolved=True, opened_alerted=True -> returns 1.
    monitor, incident = add_incident(
        db_session, resolved=True, opened_alerted=True
    )
    assert alerts.retry_pending(
        db_session, NOW, ok_transport()
    ) == 1
    db_session.refresh(incident)
    assert incident.resolved_alert_at is not None


def test_retry_skips_incident_resolved_before_any_alert(db_session):
    # resolved=True, opened_alerted=False -> returns 0.
    add_incident(db_session, resolved=True, opened_alerted=False)
    assert alerts.retry_pending(
        db_session, NOW, ok_transport()
    ) == 0


def test_retry_ignores_incidents_older_than_the_window(db_session):
    # started = NOW - 25 hours, open and never alerted -> returns 0.
    add_incident(
        db_session,
        started=NOW - timedelta(hours=25),
    )
    assert alerts.retry_pending(
        db_session, NOW, ok_transport()
    ) == 0


def test_retry_with_no_url_returns_zero(db_session, monkeypatch):
    # ALERT_WEBHOOK_URL empty -> 0.
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "")
    assert alerts.retry_pending(db_session, NOW, ok_transport()) == 0


def test_retry_keeps_trying_after_a_failure(db_session):
    # ok_transport(500): retry returns 0 and the claim is released.
    # Then ok_transport(200): returns 1.
    add_incident(db_session)
    assert alerts.retry_pending(
        db_session, NOW, ok_transport(500)
    ) == 0
    assert alerts.retry_pending(
        db_session, NOW, ok_transport(200)
    ) == 1
