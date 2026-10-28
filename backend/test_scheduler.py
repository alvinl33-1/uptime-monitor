from datetime import datetime, timedelta, timezone

import fakeredis
import pytest

import jobs
import scheduler
from models import Check, Monitor

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(jobs, "get_redis", lambda: fake)
    return fake


def add_monitor(db, interval=60):
    monitor = Monitor(name="Test", url="https://example.com", interval_seconds=interval)
    db.add(monitor)
    db.commit()
    return monitor


def add_check(db, monitor, seconds_ago):
    db.add(
        Check(
            monitor_id=monitor.id,
            status="up",
            status_code=200,
            response_time_ms=10,
            checked_at=NOW - timedelta(seconds=seconds_ago),
        )
    )
    db.commit()


def test_never_checked_monitor_is_due(db_session):
    # add_monitor, then assert it appears in
    # scheduler.find_due_monitors(db_session, NOW).
    monitor = add_monitor(db_session)
    due_monitors = scheduler.find_due_monitors(db_session, NOW)
    assert monitor in due_monitors


def test_recently_checked_monitor_is_not_due(db_session):
    # interval 60, last check 10 seconds ago -> NOT in the due list.
    monitor = add_monitor(db_session)
    add_check(db_session, monitor, 10)
    due_monitors = scheduler.find_due_monitors(db_session, NOW)
    assert monitor not in due_monitors


def test_monitor_checked_longer_ago_than_interval_is_due(db_session):
    # interval 60, last check 61 seconds ago -> due.
    # Also try exactly 60 seconds ago and decide: should it be due?
    # (The spec says "at least", so yes.)
    monitor = add_monitor(db_session)
    add_check(db_session, monitor, 61)
    due_monitors = scheduler.find_due_monitors(db_session, NOW)
    assert monitor in due_monitors


def test_only_the_latest_check_counts(db_session):
    # Add one old check (500 seconds ago) AND one recent check (5 seconds ago).
    # Interval 60. The monitor should NOT be due.
    monitor = add_monitor(db_session)
    add_check(db_session, monitor, 500)
    add_check(db_session, monitor, 5)
    due_monitors = scheduler.find_due_monitors(db_session, NOW)
    assert monitor not in due_monitors


def test_claim_succeeds_once_then_fails():
    # scheduler.claim(1, 60) is True the first time and False the second.
    assert scheduler.claim(1, 60) is True
    assert scheduler.claim(1, 60) is False


def test_run_once_enqueues_due_monitors(db_session):
    # Add two never-checked monitors. Call scheduler.run_once(db_session, NOW).
    # Assert it returned 2 and jobs.queue_length() == 2.
    add_monitor(db_session)
    add_monitor(db_session)
    count = scheduler.run_once(db_session, NOW)
    assert count == 2
    assert jobs.queue_length() == 2


def test_run_once_twice_does_not_duplicate(db_session):
    # Add one monitor. Call run_once twice in a row.
    # First returns 1, second returns 0, queue_length() == 1.
    add_monitor(db_session)
    count1 = scheduler.run_once(db_session, NOW)
    count2 = scheduler.run_once(db_session, NOW)
    assert count1 == 1
    assert count2 == 0
    assert jobs.queue_length() == 1


def test_run_once_with_no_monitors_returns_zero(db_session):
    # Empty database. run_once returns 0 and the queue stays empty.
    count = scheduler.run_once(db_session, NOW)
    assert count == 0
    assert jobs.queue_length() == 0