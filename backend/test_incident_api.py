from datetime import datetime, timedelta, timezone

from models import Incident, Monitor

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def seed(session_factory):
    """Two monitors with three incidents:
    monitor 1: one resolved (older), one open (newer)
    monitor 2: one open
    """
    with session_factory() as db:
        db.add_all(
            [
                Monitor(name="A", url="https://a.example", interval_seconds=60),
                Monitor(name="B", url="https://b.example", interval_seconds=60),
            ]
        )
        db.commit()
        db.add_all(
            [
                Incident(
                    monitor_id=1,
                    started_at=NOW - timedelta(hours=2),
                    resolved_at=NOW - timedelta(hours=1),
                ),
                Incident(monitor_id=1, started_at=NOW - timedelta(minutes=10)),
                Incident(monitor_id=2, started_at=NOW - timedelta(minutes=5)),
            ]
        )
        db.commit()


def test_incidents_start_empty(client):
    # GET /incidents -> 200 and [].
    response = client.get("/incidents")

    assert response.status_code == 200
    assert response.json() == []


def test_incidents_listed_newest_first(client, session_factory):
    # seed(session_factory), GET /incidents.
    # Expect 3 results, ordered by started_at descending.
    seed(session_factory)

    response = client.get("/incidents")

    assert response.status_code == 200
    assert [item["monitor_id"] for item in response.json()] == [2, 1, 1]
    assert response.json()[0]["started_at"] > response.json()[1]["started_at"]


def test_open_only_filters_resolved_incidents(client, session_factory):
    # GET /incidents?open_only=true -> 2 results, all with resolved_at null.
    seed(session_factory)

    response = client.get("/incidents?open_only=true")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert all(item["resolved_at"] is None for item in body)


def test_limit_is_respected(client, session_factory):
    # GET /incidents?limit=1 -> exactly 1 result.
    seed(session_factory)

    response = client.get("/incidents?limit=1")

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_limit_must_be_positive(client):
    # GET /incidents?limit=0 -> 422.
    response = client.get("/incidents?limit=0")

    assert response.status_code == 422


def test_incidents_for_one_monitor(client, session_factory):
    # GET /monitors/1/incidents -> 2 results. /monitors/2/incidents -> 1.
    seed(session_factory)

    monitor_one_response = client.get("/monitors/1/incidents")
    monitor_two_response = client.get("/monitors/2/incidents")

    assert monitor_one_response.status_code == 200
    assert monitor_two_response.status_code == 200
    assert len(monitor_one_response.json()) == 2
    assert len(monitor_two_response.json()) == 1
    assert all(item["monitor_id"] == 1 for item in monitor_one_response.json())
    assert all(item["monitor_id"] == 2 for item in monitor_two_response.json())


def test_incidents_for_missing_monitor_returns_404(client):
    # GET /monitors/999/incidents -> 404.
    response = client.get("/monitors/999/incidents")

    assert response.status_code == 404
    assert response.json()["detail"] == "Monitor not found"


def test_deleting_a_monitor_deletes_its_incidents(client, session_factory):
    # seed, DELETE /monitors/1 -> 204.
    # Open a session with session_factory() and assert no Incident rows
    # remain for monitor_id 1, while monitor 2's incident is untouched.
    seed(session_factory)

    response = client.delete("/monitors/1")

    assert response.status_code == 204
    with session_factory() as db:
        assert db.query(Incident).filter_by(monitor_id=1).count() == 0
        assert db.query(Incident).filter_by(monitor_id=2).count() == 1
