VALID = {"name": "Example site", "url": "https://example.com"}


def test_health_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_monitor(client):
    response = client.post("/monitors", json=VALID)
    assert response.status_code == 201
    body = response.json()
    assert body["id"] == 1
    assert body["name"] == "Example site"
    assert body["url"] == "https://example.com"
    assert body["interval_seconds"] == 60


def test_list_starts_empty(client):
    response = client.get("/monitors")
    assert response.status_code == 200
    assert response.json() == []


def test_list_shows_created_monitors(client):
    client.post("/monitors", json=VALID)
    client.post("/monitors", json={"name": "Second", "url": "http://example.org"})
    response = client.get("/monitors")
    assert response.status_code == 200
    assert [m["name"] for m in response.json()] == ["Example site", "Second"]


def test_get_one_monitor(client):
    created = client.post("/monitors", json=VALID).json()
    response = client.get(f"/monitors/{created['id']}")
    assert response.status_code == 200
    assert response.json()["url"] == "https://example.com"


def test_get_missing_monitor_returns_404(client):
    response = client.get("/monitors/999")
    assert response.status_code == 404


def test_delete_monitor(client):
    created = client.post("/monitors", json=VALID).json()
    response = client.delete(f"/monitors/{created['id']}")
    assert response.status_code == 204
    assert client.get(f"/monitors/{created['id']}").status_code == 404


def test_delete_missing_monitor_returns_404(client):
    response = client.delete("/monitors/999")
    assert response.status_code == 404


def test_rejects_url_without_http(client):
    response = client.post("/monitors", json={"name": "Bad", "url": "not-a-url"})
    assert response.status_code == 422


def test_rejects_empty_name(client):
    response = client.post("/monitors", json={"name": "", "url": "https://example.com"})
    assert response.status_code == 422


def test_rejects_interval_that_is_too_short(client):
    response = client.post("/monitors", json={**VALID, "interval_seconds": 5})
    assert response.status_code == 422
