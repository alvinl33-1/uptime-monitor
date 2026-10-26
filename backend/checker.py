import ipaddress
import socket
import time
from urllib.parse import urlparse
import httpx

USER_AGENT = "UptimeMonitor/0.1"

def resolve_ips(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None)
    return sorted({info[4][0] for info in infos})


def is_public_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if addr.version == 6 and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return addr.is_global and not addr.is_multicast


def _result(status, status_code, started, error=None):
    return {
        "status": status,
        "status_code": status_code,
        "response_time_ms": int((time.monotonic() - started) * 1000),
        "error": error,
    }


def check_url(url: str, timeout: float = 5.0, transport=None) -> dict:
    started = time.monotonic()

    host = urlparse(url).hostname
    if not host:
        return _result("down", None, started, "invalid url")

    try:
        ips = resolve_ips(host)
    except socket.gaierror:
        return _result("down", None, started, "dns lookup failed")

    if not ips or not all(is_public_ip(ip) for ip in ips):
        return _result("down", None, started, "blocked: address is not public")

    try:
        with httpx.Client(
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            with client.stream("GET", url) as response:
                code = response.status_code
    except httpx.TimeoutException:
        return _result("down", None, started, "timeout")
    except httpx.HTTPError:
        return _result("down", None, started, "connection failed")

    status = "up" if code < 400 else "down"
    error = None if status == "up" else f"http {code}"
    return _result(status, code, started, error)