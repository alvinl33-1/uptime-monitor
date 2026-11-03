import json
from datetime import datetime, timedelta, timezone

import pytest

from app import jobs, statuspage
from app.models import Check, Incident, Monitor

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
SECRET_URL = "https://secret-internal.example"


def add_monitor(db, name="Site"):
    monitor = Monitor(name=name, url=SECRET_URL, interval_seconds=60)
    db.add(monitor)
    db.commit()
    return monitor


def add_check(db, monitor, status, seconds_ago=10):
    db.add(
        Check(
            monitor_id=monitor.id,
            status=status,
            status_code=200 if status == "up" else 500,
            response_time_ms=120,
            checked_at=NOW - timedelta(seconds=seconds_ago),
        )
    )
    db.commit()


def open_incident(db, monitor):
    db.add(Incident(monitor_id=monitor.id, started_at=NOW - timedelta(minutes=5)))
    db.commit()


# ---- monitor_state (pure) ----

def test_open_incident_means_down():
    # monitor_state(True, "down") and monitor_state(True, "up") are both "down".
    assert statuspage.monitor_state(True, "down") == "down"
    assert statuspage.monitor_state(True, "up") == "down"


def test_never_checked_means_unknown():
    # monitor_state(False, None) == "unknown"
    assert statuspage.monitor_state(False, None) == "unknown"


def test_failing_without_incident_means_degraded():
    # monitor_state(False, "down") == "degraded"
    assert statuspage.monitor_state(False, "down") == "degraded"


def test_healthy_means_up():
    # monitor_state(False, "up") == "up"
    assert statuspage.monitor_state(False, "up") == "up"


# ---- overall_state (pure) ----

def test_overall_with_no_monitors_is_no_data():
    # overall_state([]) and overall_state(["unknown", "unknown"]) are "no_data".
    assert statuspage.overall_state([]) == "no_data"
    assert statuspage.overall_state(["unknown", "unknown"]) == "no_data"


def test_overall_all_down_is_major_outage():
    assert statuspage.overall_state(["down", "down"]) == "major_outage"


def test_overall_some_down_is_partial_outage():
    # ["up", "down", "degraded"] -> "partial_outage"
    assert statuspage.overall_state(["up", "down", "degraded"]) == "partial_outage"


def test_overall_degraded_without_down():
    # ["up", "degraded"] -> "degraded"
    assert statuspage.overall_state(["up", "degraded"]) == "degraded"


def test_overall_all_up_is_operational():
    # ["up", "up"] and ["up", "unknown"] -> "operational"
    assert statuspage.overall_state(["up", "up"]) == "operational"
    assert statuspage.overall_state(["up", "unknown"]) == "operational"

# ---- uptime_by_monitor ----

def test_uptime_is_percentage_of_up_checks(db_session):
    # 3 up checks and 1 down check -> 75.0
    db = db_session
    monitor = add_monitor(db)
    add_check(db, monitor, "up", seconds_ago=10)
    add_check(db, monitor, "up", seconds_ago=20)
    add_check(db, monitor, "up", seconds_ago=30)
    add_check(db, monitor, "down", seconds_ago=40)
    result = statuspage.uptime_by_monitor(db, since=NOW - timedelta(hours=1))
    assert result == {monitor.id: 75.0}


def test_uptime_ignores_checks_older_than_the_window(db_session):
    # since = NOW - 24h. One old DOWN check (30 hours ago) and one recent up:
    # result is 100.0.
    db = db_session
    monitor = add_monitor(db)
    add_check(db, monitor, "down", seconds_ago=30 * 3600)  # 30 hours ago
    add_check(db, monitor, "up", seconds_ago=10)  # 10 seconds ago
    result = statuspage.uptime_by_monitor(db, since=NOW - timedelta(hours=24))
    assert result == {monitor.id: 100.0}


def test_uptime_is_per_monitor(db_session):
    # Monitor A all up, monitor B all down -> {A: 100.0, B: 0.0}.
    db = db_session
    monitor_a = add_monitor(db, name="A")
    monitor_b = add_monitor(db, name="B")
    add_check(db, monitor_a, "up", seconds_ago=10)
    add_check(db, monitor_b, "down", seconds_ago=10)
    result = statuspage.uptime_by_monitor(db, since=NOW - timedelta(hours=1))
    assert result == {monitor_a.id: 100.0, monitor_b.id: 0.0}


def test_monitor_without_checks_is_absent_from_uptime(db_session):
    # A monitor with no checks has no key in the returned dict.
    monitor = add_monitor(db_session)
    assert statuspage.uptime_by_monitor(
        db_session,
        since=NOW - timedelta(hours=1),
    ) == {}


# ---- build_status ----

def test_empty_database_gives_no_data(db_session):
    # overall "no_data", monitors == [], open_incidents == [].
    result = statuspage.build_status(db_session, NOW)
    assert result["overall"] == "no_data"
    assert result["monitors"] == []
    assert result["open_incidents"] == []
    assert result["generated_at"] == NOW.isoformat()


def test_never_checked_monitor_is_unknown_with_null_fields(db_session):
    # status "unknown"; uptime_24h, response_time_ms, last_checked_at all None.
    monitor = add_monitor(db_session)
    result = statuspage.build_status(db_session, NOW)
    entry = result["monitors"][0]
    assert entry["id"] == monitor.id
    assert entry["status"] == "unknown"
    assert entry["uptime_24h"] is None
    assert entry["response_time_ms"] is None
    assert entry["last_checked_at"] is None


def test_healthy_monitor_is_up_with_details(db_session):
    # One up check -> status "up", uptime_24h 100.0, response_time_ms 120,
    # last_checked_at is a string.
    monitor = add_monitor(db_session)
    add_check(db_session, monitor, "up")
    result = statuspage.build_status(db_session, NOW)
    entry = result["monitors"][0]
    assert entry["status"] == "up"
    assert entry["uptime_24h"] == 100.0
    assert entry["response_time_ms"] == 120
    assert isinstance(entry["last_checked_at"], str)


def test_single_failure_is_degraded_not_down(db_session):
    # One down check, no incident -> "degraded", and open_incidents is empty.
    monitor = add_monitor(db_session)
    add_check(db_session, monitor, "down")
    result = statuspage.build_status(db_session, NOW)
    assert result["monitors"][0]["status"] == "degraded"
    assert result["open_incidents"] == []


def test_open_incident_is_down_and_listed(db_session):
    # down check + open_incident -> status "down", overall "major_outage",
    # and open_incidents has one entry with monitor_name set.
    monitor = add_monitor(db_session)
    add_check(db_session, monitor, "down")
    open_incident(db_session, monitor)
    result = statuspage.build_status(db_session, NOW)
    assert result["overall"] == "major_outage"
    assert result["monitors"][0]["status"] == "down"
    assert len(result["open_incidents"]) == 1
    assert result["open_incidents"][0]["monitor_name"] == monitor.name


def test_resolved_incident_is_not_listed(db_session):
    # An Incident with resolved_at set must not appear in open_incidents.
    monitor = add_monitor(db_session)
    incident = Incident(
        monitor_id=monitor.id,
        started_at=NOW - timedelta(minutes=5),
        resolved_at=NOW,
    )
    db_session.add(incident)
    db_session.commit()
    result = statuspage.build_status(db_session, NOW)
    assert result["open_incidents"] == []


def test_monitor_urls_never_appear(db_session):
    # Build a status with a monitor, a check, and an incident, then assert
    # SECRET_URL not in json.dumps(result), and no "url" key in any monitor.
    monitor = add_monitor(db_session)
    add_check(db_session, monitor, "down")
    open_incident(db_session, monitor)
    result = statuspage.build_status(db_session, NOW)
    serialized = json.dumps(result)
    assert SECRET_URL not in serialized
    assert all("url" not in entry for entry in result["monitors"])


def test_monitors_are_listed_in_id_order(db_session):
    first = add_monitor(db_session, name="first")
    second = add_monitor(db_session, name="second")
    result = statuspage.build_status(db_session, NOW)
    assert [entry["id"] for entry in result["monitors"]] == [first.id, second.id]


# ---- caching ----

def test_first_call_misses_and_second_call_hits(db_session):
    # get_status twice: states "MISS" then "HIT", and the data is equal.
    first, first_state = statuspage.get_status(db_session, NOW)
    second, second_state = statuspage.get_status(db_session, NOW)
    assert first_state == "MISS"
    assert second_state == "HIT"
    assert first == second


def test_a_hit_does_not_rebuild(db_session, monkeypatch):
    # Count calls by wrapping statuspage.build_status with monkeypatch.
    # After get_status twice, it was called exactly once.
    calls = 0
    original = statuspage.build_status

    def counted_build(db, now=None):
        nonlocal calls
        calls += 1
        return original(db, now)

    monkeypatch.setattr(statuspage, "build_status", counted_build)
    statuspage.get_status(db_session, NOW)
    statuspage.get_status(db_session, NOW)
    assert calls == 1


def test_cached_entry_has_an_expiry(db_session, fake_redis):
    # After one get_status, fake_redis.ttl(statuspage.CACHE_KEY) is between
    # 1 and 15.
    statuspage.get_status(db_session, NOW)
    ttl = fake_redis.ttl(statuspage.CACHE_KEY)
    assert 1 <= ttl <= 15


def test_invalidate_forces_a_rebuild(db_session):
    # get_status (MISS), invalidate(), get_status (MISS again).
    _, first_state = statuspage.get_status(db_session, NOW)
    statuspage.invalidate()
    _, second_state = statuspage.get_status(db_session, NOW)
    assert first_state == "MISS"
    assert second_state == "MISS"


def test_redis_down_bypasses_the_cache(db_session, monkeypatch):
    # monkeypatch jobs.get_redis to a function that raises ConnectionError.
    # get_status still returns data, with state "BYPASS".
    # Also assert statuspage.invalidate() does NOT raise.
    def redis_down():
        raise ConnectionError("Redis unavailable")

    monkeypatch.setattr(jobs, "get_redis", redis_down)
    data, state = statuspage.get_status(db_session, NOW)
    assert state == "BYPASS"
    assert data["overall"] == "no_data"
    statuspage.invalidate()


def test_corrupt_cache_value_is_treated_as_a_miss(db_session, fake_redis):
    # fake_redis.set(statuspage.CACHE_KEY, "not json"), then get_status
    # returns a MISS with real data.
    fake_redis.set(statuspage.CACHE_KEY, "not json")
    data, state = statuspage.get_status(db_session, NOW)
    assert state == "MISS"
    assert data["overall"] == "no_data"


def test_cache_seconds_setting(monkeypatch):
    # "30" -> 30. Unset, "abc", "0", and "-5" all -> 15.
    monkeypatch.delenv("STATUS_CACHE_SECONDS", raising=False)
    assert statuspage.cache_seconds() == 15
    for value in ("abc", "0", "-5"):
        monkeypatch.setenv("STATUS_CACHE_SECONDS", value)
        assert statuspage.cache_seconds() == 15
    monkeypatch.setenv("STATUS_CACHE_SECONDS", "30")
    assert statuspage.cache_seconds() == 30


# ---- endpoint ----

def test_status_endpoint_is_public_and_has_cache_headers(client):
    # GET /status -> 200, X-Cache "MISS". Again -> "HIT".
    # Cache-Control is "public, max-age=10".
    first = client.get("/status")
    second = client.get("/status")
    assert first.status_code == 200
    assert first.headers["X-Cache"] == "MISS"
    assert second.headers["X-Cache"] == "HIT"
    assert second.headers["Cache-Control"] == "public, max-age=10"


def test_status_endpoint_never_exposes_urls(client):
    # Create a monitor with POST /monitors, GET /status.
    # "secret" text (use SECRET_URL) must not appear in response.text.
    response = client.post(
        "/monitors",
        json={"name": "secret", "url": SECRET_URL, "interval_seconds": 30},
    )
    assert response.status_code == 201
    status = client.get("/status")
    assert SECRET_URL not in status.text


def test_creating_a_monitor_refreshes_the_status(client):
    # GET /status (MISS), GET /status (HIT), POST /monitors,
    # GET /status -> MISS, and the new monitor is in the list.
    assert client.get("/status").headers["X-Cache"] == "MISS"
    assert client.get("/status").headers["X-Cache"] == "HIT"
    created = client.post(
        "/monitors",
        json={"name": "new", "url": "https://new.example", "interval_seconds": 30},
    )
    assert created.status_code == 201
    response = client.get("/status")
    assert response.headers["X-Cache"] == "MISS"
    assert response.json()["monitors"][0]["name"] == "new"


def test_deleting_a_monitor_refreshes_the_status(client):
    # Create one, GET /status, DELETE it, GET /status -> MISS and monitors == [].
    created = client.post(
        "/monitors",
        json={"name": "temporary", "url": "https://temporary.example", "interval_seconds": 30},
    )
    monitor_id = created.json()["id"]
    client.get("/status")
    deleted = client.delete(f"/monitors/{monitor_id}")
    assert deleted.status_code == 204
    response = client.get("/status")
    assert response.headers["X-Cache"] == "MISS"
    assert response.json()["monitors"] == []
