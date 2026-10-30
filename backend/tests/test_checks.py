from app import main

VALID = {"name": "example site", "url": "https://example.com"}

UP = {"status": "up", "status_code": 200, "response_time_ms": 42, "error": None}
DOWN = {
    "status": "down",
    "status_code": 500,
    "response_time_ms": 80,
    "error": "http 500",
}

def make_monitor(client, **overrides):
    return client.post("/monitors", json={**VALID, **overrides}).json()

def test_run_check_saves_up_result(client, monkeypatch):
    monkeypatch.setattr(main, "check_url", lambda url: UP)
    monitor = make_monitor(client)

    response = client.post(f"/monitors/{monitor['id']}/check")
    assert response.status_code == 201
    body = response.json()
    assert body["monitor_id"] == monitor["id"]
    assert body["status"] == "up"
    assert body["status_code"] == 200
    assert body["response_time_ms"] == 42
    assert body["error"] is None

    saved = client.get(f"/monitors/{monitor['id']}/check").json()
    assert len(saved) == 1


def test_run_check_on_private_address_is_down(client):
    monitor = make_monitor(client, url="http://localhost:8000")
    response = client.post(f"/monitors/{monitor['id']}/check")
    assert response.status_code == 201
    assert response.json()["status"] == "down"
    assert "blocked" in response.json()["error"]


def test_run_check_missing_monitor_returns_404(client):
    assert client.post("/monitors/999/check").status_code == 404


def test_checks_are_listed_newest_first(client, monkeypatch):
    monitor = make_monitor(client)
    monkeypatch.setattr(main, "check_url", lambda url: UP)
    client.post(f"/monitors/{monitor['id']}/check")
    monkeypatch.setattr(main, "check_url", lambda url: DOWN)
    client.post(f"/monitors/{monitor['id']}/check")

    checks = client.get(f"/monitors/{monitor['id']}/checks").json()
    assert [c["status"] for c in checks] == ["down", "up"]


def test_checks_limit(client, monkeypatch):
    monkeypatch.setattr(main, "check_url", lambda url: UP)
    monitor = make_monitor(client)
    for _ in range(3):
        client.post(f"/monitors/{monitor['id']}/check")

    checks = client.get(f"/monitors/{monitor['id']}/checks?limit=2").json()
    assert len(checks) == 2


def test_checks_for_missing_monitor_returns_404(client):
    assert client.get("/monitors/999/checks").status_code == 404


def test_delete_monitor_also_deletes_checks(client, monkeypatch):
    monkeypatch.setattr(main, "check_url", lambda url: UP)
    monitor = make_monitor(client)
    client.post(f"/monitors/{monitor['id']}/check")

    assert client.delete(f"/monitors/{monitor['id']}").status_code == 204
    assert client.get(f"/monitors/{monitor['id']}/checks").status_code == 404