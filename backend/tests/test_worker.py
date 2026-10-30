import fakeredis

from app import jobs, worker
from app.models import Monitor


def test_process_next_job_saves_check(
    db_session,
    monkeypatch,
):
    monitor = Monitor(
        name="Example",
        url="https://example.com",
        interval_seconds=60,
    )
    db_session.add(monitor)
    db_session.commit()
    db_session.refresh(monitor)

    fake = fakeredis.FakeRedis(decode_responses=True)
    fake.rpush(jobs.QUEUE_NAME, '{"monitor_id": %d}' % monitor.id)
    monkeypatch.setattr(jobs, "get_redis", lambda: fake)
    monkeypatch.setattr(
        worker,
        "check_url",
        lambda url: {
            "status": "up",
            "status_code": 200,
            "response_time_ms": 42,
            "error": None,
        },
    )

    assert worker.process_next_job(lambda: db_session, timeout=1) is True

    check = db_session.query(worker.Check).one()
    assert check.monitor_id == monitor.id
    assert check.status == "up"
    assert check.status_code == 200


def test_process_next_job_skips_deleted_monitor(db_session, monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    fake.rpush(jobs.QUEUE_NAME, '{"monitor_id": 999}')
    monkeypatch.setattr(jobs, "get_redis", lambda: fake)

    assert worker.process_next_job(lambda: db_session, timeout=1) is False
    assert db_session.query(worker.Check).count() == 0


def test_process_next_job_returns_none_when_queue_is_empty(monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(jobs, "get_redis", lambda: fake)

    assert worker.process_next_job(lambda: None, timeout=1) is None
