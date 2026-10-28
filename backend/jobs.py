import json
import os
from functools import lru_cache

import redis

QUEUE_NAME = "checks:queue"


@lru_cache
def get_redis() -> redis.Redis:
    """
    Return a shared Redis client.

    Spec:
    - Read the address from the REDIS_URL environment variable.
      Default to "redis://localhost:6379/0" if it isn't set.
    - Build the client with redis.Redis.from_url(url, decode_responses=True)
      so values come back as normal strings instead of bytes.
    - The @lru_cache decorator above makes repeated calls return the
      same client object, so no need to cache it yourself.
    """
    url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    return redis.Redis.from_url(url, decode_responses=True)



def enqueue_check(monitor_id: int) -> None:
    """
    Add a "check this monitor" job to the BACK of the line.

    Spec:
    - Build a JSON string like {"monitor_id": 3} using json.dumps.
      (A dict instead of a bare number lets us add fields later,
      such as a timestamp, without breaking old jobs.)
    - Push it onto QUEUE_NAME with RPUSH.
    - Return nothing.
    """
    redis = get_redis()
    job = json.dumps({"monitor_id": monitor_id})
    redis.rpush(QUEUE_NAME, job)
    return None


def dequeue_check(timeout: int = 5) -> int | None:
    """
    Take a job from the FRONT of the line and return its monitor_id.

    Spec:
    - Use BLPOP on QUEUE_NAME with the given timeout (in seconds).
      BLPOP waits for a job to arrive instead of returning instantly.
    - WARNING: a timeout of 0 means "wait forever" in Redis. Raise
      ValueError if timeout is less than 1.
    - BLPOP returns None on timeout, or a pair (queue_name, value).
      On timeout, return None.
    - Otherwise parse the value with json.loads and return the
      "monitor_id" as an int.
    - If the value isn't valid JSON, or has no "monitor_id", throw the
      job away and return None. A broken message must never crash a worker.
    """
    redis = get_redis()
    if timeout < 1:
        raise ValueError("Timeout must be at least 1 second.")
    result = redis.blpop(QUEUE_NAME, timeout=timeout)
    if result is None:
        return None
    value = result[1]
    try:
        job = json.loads(value)
        monitor_id = job.get("monitor_id")
        if monitor_id is None:
            return None
        return int(monitor_id)
    except (json.JSONDecodeError, ValueError):
        return None


def queue_length() -> int:
    """
    Return how many jobs are waiting.

    Spec:
    - Use LLEN on QUEUE_NAME and return it as an int.
    """
    redis = get_redis()
    return redis.llen(QUEUE_NAME)