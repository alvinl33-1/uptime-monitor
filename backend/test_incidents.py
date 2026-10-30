from datetime import datetime, timedelta, timezone

import pytest

import incidents
from models import Check, Incident, Monitor

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def add_monitor(db, name="Test"):
    monitor = Monitor(name=name, url="https://example.com", interval_seconds=60)
    db.add(monitor)
    db.commit()
    return monitor


def add_incident(db, monitor, started_at, resolved_at=None):
    incident = Incident(
        monitor_id=monitor.id,
        started_at=started_at,
        resolved_at=resolved_at,
    )
    db.add(incident)
    db.commit()
    db.refresh(incident)
    return incident


def record(db, monitor, status, seconds_ago):
    """Save a check, then run the incident logic, like the worker does."""
    db.add(
        Check(
            monitor_id=monitor.id,
            status=status,
            status_code=200 if status == "up" else 500,
            response_time_ms=10,
            checked_at=NOW - timedelta(seconds=seconds_ago),
        )
    )
    db.commit()
    return incidents.evaluate(db, monitor.id)


# GOTCHA: SQLite (used in tests) returns datetimes WITHOUT a timezone.
# When comparing a saved time to NOW - timedelta(...), strip the timezone
# from both sides with .replace(tzinfo=None).


def test_no_checks_means_no_event(db_session):
    # monitor with no checks: incidents.evaluate(db_session, monitor.id)
    # returns (None, None).
    monitor = add_monitor(db_session)
    assert incidents.evaluate(db_session, monitor.id) == (None, None)
    assert db_session.query(Incident).count() == 0


def test_two_failures_do_not_open_an_incident(db_session):
    # record down at 120s ago, record down at 60s ago.
    # Both results are (None, None) and Incident count is 0.
    monitor = add_monitor(db_session)
    assert record(db_session, monitor, "down", 120) == (None, None)
    assert record(db_session, monitor, "down", 60) == (None, None)
    assert db_session.query(Incident).count() == 0


def test_three_failures_open_an_incident(db_session):
    # down at 120, 60, 0 seconds ago. The third result's event is "opened".
    # Assert started_at is the time of the FIRST failure (120 seconds ago),
    # and resolved_at is None.
    monitor = add_monitor(db_session)
    record(db_session, monitor, "down", 120)
    record(db_session, monitor, "down", 60)
    event, incident = record(db_session, monitor, "down", 0)
    assert event == "opened"
    assert incident is not None
    assert incident.started_at.replace(tzinfo=None) == (
        NOW - timedelta(seconds=120)
    ).replace(tzinfo=None)
    assert incident.resolved_at is None


def test_more_failures_do_not_open_a_second_incident(db_session):
    # Open an incident with three failures, then record a 4th and 5th.
    # Both return (None, None) and the Incident count stays 1.
    monitor = add_monitor(db_session)
    for seconds_ago in (120, 60, 0):
        record(db_session, monitor, "down", seconds_ago)
    assert record(db_session, monitor, "down", -60) == (None, None)
    assert record(db_session, monitor, "down", -120) == (None, None)
    assert db_session.query(Incident).count() == 1


def test_a_success_resolves_the_incident(db_session):
    # Three failures, then record "up". Event is "resolved".
    # resolved_at equals the time of the up check, and no open incident remains
    # (incidents.get_open_incident returns None).
    monitor = add_monitor(db_session)
    for seconds_ago in (120, 60, 0):
        record(db_session, monitor, "down", seconds_ago)

    event, incident = record(db_session, monitor, "up", -60)

    assert event == "resolved"
    assert incident is not None
    assert incident.resolved_at.replace(tzinfo=None) == (
        NOW + timedelta(seconds=60)
    ).replace(tzinfo=None)
    assert incidents.get_open_incident(db_session, monitor.id) is None


def test_success_with_no_incident_does_nothing(db_session):
    # record "up" on a healthy monitor -> (None, None), zero incidents.
    monitor = add_monitor(db_session)
    assert record(db_session, monitor, "up", 0) == (None, None)
    assert db_session.query(Incident).count() == 0


def test_failures_after_recovery_start_counting_again(db_session):
    # down x3 (opens), up (resolves), then down x2 -> no new incident.
    # A third down opens a SECOND incident. Total incident count is 2.
    monitor = add_monitor(db_session)
    for seconds_ago in (300, 240, 180):
        record(db_session, monitor, "down", seconds_ago)
    record(db_session, monitor, "up", 120)
    assert incidents.get_open_incident(db_session, monitor.id) is None

    assert record(db_session, monitor, "down", 60) == (None, None)
    event, second_incident = record(db_session, monitor, "down", 30)
    assert event is None
    assert second_incident is None
    event, second_incident = record(db_session, monitor, "down", 0)
    assert event == "opened"
    assert second_incident is not None
    assert db_session.query(Incident).count() == 2


def test_failures_must_be_consecutive(db_session):
    # down, down, up, down -> never opens an incident.
    monitor = add_monitor(db_session)
    assert record(db_session, monitor, "down", 180) == (None, None)
    assert record(db_session, monitor, "down", 120) == (None, None)
    assert record(db_session, monitor, "up", 60) == (None, None)
    assert record(db_session, monitor, "down", 0) == (None, None)
    assert db_session.query(Incident).count() == 0


def test_monitors_are_independent(db_session):
    # Two monitors. Three failures on A. B stays up.
    # Only A gets an incident.
    monitor_a = add_monitor(db_session, "A")
    monitor_b = add_monitor(db_session, "B")
    for seconds_ago in (120, 60, 0):
        record(db_session, monitor_a, "down", seconds_ago)
    record(db_session, monitor_b, "up", 0)

    incidents_for_a = (
        db_session.query(Incident).filter_by(monitor_id=monitor_a.id).all()
    )
    incidents_for_b = (
        db_session.query(Incident).filter_by(monitor_id=monitor_b.id).all()
    )
    assert len(incidents_for_a) == 1
    assert incidents_for_b == []


def test_race_is_stopped_by_the_database(db_session, monkeypatch):
    # Open a real incident with three failures.
    # Then pretend another worker is racing: patch
    #   incidents.get_open_incident to a function that always returns None.
    # Record a 4th failure. evaluate will try to open a second incident,
    # and the unique index should reject it.
    # Assert the result is (None, None) and the Incident count is still 1.
    monitor = add_monitor(db_session)
    for seconds_ago in (120, 60, 0):
        record(db_session, monitor, "down", seconds_ago)

    monkeypatch.setattr(incidents, "get_open_incident", lambda db, monitor_id: None)
    assert record(db_session, monitor, "down", -60) == (None, None)
    assert db_session.query(Incident).count() == 1


def test_list_incidents_is_newest_first_and_applies_limit(client, session_factory):
    with session_factory() as db:
        monitor = add_monitor(db)
        started_at = NOW.replace(tzinfo=None)
        resolved_at = started_at + timedelta(seconds=1)
        add_incident(
            db,
            monitor,
            started_at - timedelta(seconds=120),
            resolved_at=resolved_at,
        )
        newest = add_incident(db, monitor, started_at, resolved_at=resolved_at)
        newest_tie = add_incident(
            db,
            monitor,
            started_at,
            resolved_at=resolved_at,
        )
        expected_ids = [newest_tie.id, newest.id]

    response = client.get("/incidents?limit=2")

    assert response.status_code == 200
    body = response.json()
    assert [incident["id"] for incident in body] == expected_ids


def test_list_incidents_open_only(client, session_factory):
    with session_factory() as db:
        monitor = add_monitor(db)
        add_incident(
            db,
            monitor,
            NOW.replace(tzinfo=None) - timedelta(seconds=60),
            resolved_at=NOW.replace(tzinfo=None),
        )
        open_incident = add_incident(db, monitor, NOW.replace(tzinfo=None))

    response = client.get("/incidents?open_only=true")

    assert response.status_code == 200
    assert [incident["id"] for incident in response.json()] == [open_incident.id]


def test_list_monitor_incidents_filters_by_monitor_and_validates_monitor(
    client,
    session_factory,
):
    with session_factory() as db:
        monitor = add_monitor(db)
        other_monitor = add_monitor(db, "Other")
        incident = add_incident(db, monitor, NOW.replace(tzinfo=None))
        add_incident(db, other_monitor, NOW.replace(tzinfo=None))
        monitor_id = monitor.id
        incident_id = incident.id

    response = client.get(f"/monitors/{monitor_id}/incidents")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [incident_id]

    missing_response = client.get("/monitors/999/incidents")
    assert missing_response.status_code == 404
    assert missing_response.json()["detail"] == "Monitor not found"
