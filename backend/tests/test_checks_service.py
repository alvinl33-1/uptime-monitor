from app import checks
from app.models import Incident, Monitor


DOWN = {
    "status": "down",
    "status_code": 500,
    "response_time_ms": 80,
    "error": "http 500",
}
UP = {
    "status": "up",
    "status_code": 200,
    "response_time_ms": 42,
    "error": None,
}


def add_monitor(db):
    monitor = Monitor(
        name="Example",
        url="https://example.com",
        interval_seconds=60,
    )
    db.add(monitor)
    db.commit()
    db.refresh(monitor)
    return monitor


def test_record_check_opens_incident_and_delivers_alert(db_session, monkeypatch):
    monitor = add_monitor(db_session)
    delivered = []

    def fake_deliver(db, event, incident, delivered_monitor, transport=None):
        delivered.append((event, incident.id, delivered_monitor.id))
        return True

    monkeypatch.setattr(checks.alerts, "deliver", fake_deliver)

    for _ in range(2):
        checks.record_check(db_session, monitor, DOWN)
    checks.record_check(db_session, monitor, DOWN)

    assert db_session.query(Incident).count() == 1
    assert delivered == [("opened", 1, monitor.id)]


def test_record_check_resolves_incident_and_delivers_resolution(db_session, monkeypatch):
    monitor = add_monitor(db_session)
    delivered = []

    def fake_deliver(db, event, incident, delivered_monitor, transport=None):
        delivered.append(event)
        return True

    monkeypatch.setattr(checks.alerts, "deliver", fake_deliver)

    for _ in range(3):
        checks.record_check(db_session, monitor, DOWN)
    checks.record_check(db_session, monitor, UP)

    assert delivered == ["opened", "resolved"]
    assert db_session.query(Incident).filter(Incident.resolved_at.is_(None)).count() == 0


def test_alert_failure_does_not_lose_saved_check(db_session, monkeypatch):
    monitor = add_monitor(db_session)

    def failing_deliver(*args, **kwargs):
        raise RuntimeError("webhook unavailable")

    monkeypatch.setattr(checks.alerts, "deliver", failing_deliver)

    for _ in range(3):
        checks.record_check(db_session, monitor, DOWN)

    assert db_session.query(checks.Check).count() == 3
    assert db_session.query(Incident).count() == 1
