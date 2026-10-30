import socket

import httpx
import pytest

from app import checker


def fake_site(status):
    return httpx.MockTransport(lambda request: httpx.Response(status))


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.5",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "0.0.0.0",
        "::1",
        "fe80::1",
        "::ffff:127.0.0.1",
    ],
)
def test_non_public_addresses_are_rejected(ip):
    assert checker.is_public_ip(ip) is False


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"])
def test_public_addresses_are_accepted(ip):
    assert checker.is_public_ip(ip) is True


def test_localhost_is_blocked():
    result = checker.check_url("http://localhost:8000/health")
    assert result["status"] == "down"
    assert "blocked" in result["error"]


def test_cloud_metadata_address_is_blocked():
    result = checker.check_url("http://169.254.169.254/latest/meta-data")
    assert result["status"] == "down"
    assert "blocked" in result["error"]


def test_mixed_addresses_are_blocked(monkeypatch):
    monkeypatch.setattr(checker, "resolve_ips", lambda host: ["8.8.8.8", "10.0.0.1"])
    result = checker.check_url("https://example.com", transport=fake_site(200))
    assert result["status"] == "down"
    assert "blocked" in result["error"]


def test_dns_failure_is_down(monkeypatch):
    def fail(host):
        raise socket.gaierror("no such host")

    monkeypatch.setattr(checker, "resolve_ips", fail)
    result = checker.check_url("https://does-not-exist.example")
    assert result["status"] == "down"
    assert result["error"] == "dns lookup failed"


def test_200_is_up(monkeypatch):
    monkeypatch.setattr(checker, "resolve_ips", lambda host: ["8.8.8.8"])
    result = checker.check_url("https://example.com", transport=fake_site(200))
    assert result["status"] == "up"
    assert result["status_code"] == 200
    assert result["error"] is None


def test_500_is_down(monkeypatch):
    monkeypatch.setattr(checker, "resolve_ips", lambda host: ["8.8.8.8"])
    result = checker.check_url("https://example.com", transport=fake_site(500))
    assert result["status"] == "down"
    assert result["status_code"] == 500
    assert result["error"] == "http 500"


def test_redirect_is_not_followed(monkeypatch):
    monkeypatch.setattr(checker, "resolve_ips", lambda host: ["8.8.8.8"])
    result = checker.check_url("https://example.com", transport=fake_site(302))
    assert result["status"] == "up"
    assert result["status_code"] == 302


def test_timeout_is_down(monkeypatch):
    monkeypatch.setattr(checker, "resolve_ips", lambda host: ["8.8.8.8"])

    def too_slow(request):
        raise httpx.ConnectTimeout("too slow")

    result = checker.check_url(
        "https://example.com", transport=httpx.MockTransport(too_slow)
    )
    assert result["status"] == "down"
    assert result["error"] == "timeout"
