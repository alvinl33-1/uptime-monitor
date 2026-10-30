import fakeredis
import pytest

from app import jobs


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(jobs, "get_redis", lambda: fake)
    return fake


def test_enqueue_increases_length():
    # Enqueue two jobs. Assert queue_length() == 2.
    jobs.enqueue_check(1)
    jobs.enqueue_check(2)
    assert jobs.queue_length() == 2


def test_jobs_come_out_in_order():
    # Enqueue monitor ids 1, 2, 3.
    # Assert dequeue_check(timeout=1) returns 1, then 2, then 3.
    jobs.enqueue_check(1)
    jobs.enqueue_check(2)
    jobs.enqueue_check(3)
    assert jobs.dequeue_check(timeout=1) == 1
    assert jobs.dequeue_check(timeout=1) == 2
    assert jobs.dequeue_check(timeout=1) == 3


def test_dequeue_removes_the_job():
    # Enqueue one job, dequeue it, assert queue_length() == 0.
    jobs.enqueue_check(1)
    assert jobs.dequeue_check(timeout=1) == 1
    assert jobs.queue_length() == 0


def test_dequeue_on_empty_queue_returns_none():
    # Assert dequeue_check(timeout=1) is None. (Takes about a second.)
    assert jobs.dequeue_check(timeout=1) is None


def test_zero_timeout_is_rejected():
    # Use pytest.raises(ValueError) around dequeue_check(timeout=0).
    with pytest.raises(ValueError):
        jobs.dequeue_check(timeout=0)


def test_garbage_job_is_discarded(fake_redis):
    # Push the string "not json" straight onto jobs.QUEUE_NAME
    # using fake_redis.rpush(...).
    # Assert dequeue_check(timeout=1) is None and queue_length() == 0.
    fake_redis.rpush(jobs.QUEUE_NAME, "not json")
    assert jobs.dequeue_check(timeout=1) is None
    assert jobs.queue_length() == 0


def test_job_without_monitor_id_is_discarded(fake_redis):
    # Push the string '{"hello": "world"}' onto the queue.
    # Same assertions as the test above.
    fake_redis.rpush(jobs.QUEUE_NAME, '{"hello": "world"}')
    assert jobs.dequeue_check(timeout=1) is None
    assert jobs.queue_length() == 0
