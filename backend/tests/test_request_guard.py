"""Tests for app/core/request_guard.py through the real app's middleware stack.

Requests go to /api/__guard_probe__, which no route serves: a 404 means the guard let the
request through to the router, a 400/403 that the guard refused it. Nothing here touches a
real route that writes."""

import logging

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.request_guard import APP_ORIGINS
from app.main import app

PROBE = "/api/__guard_probe__"
PASSED = 404


def _client(base_url: str = "http://testserver") -> TestClient:
    # Not entered as a context manager: no startup event, so no database or daemons.
    return TestClient(app, base_url=base_url)


@pytest.mark.parametrize("base_url", [
    "http://localhost:8000", "http://127.0.0.1:8000", "http://testserver", "http://LOCALHOST:3000",
])
def test_the_apps_own_hosts_pass(base_url):
    assert _client(base_url).get(PROBE).status_code == PASSED


def test_the_compose_name_alone_is_refused():
    """Only Next's proxy says Host: backend, and it always adds X-Forwarded-Host. A browser
    that reached 127.0.0.1:8000 through a rebound `backend` name (LLMNR answers it on a LAN)
    sends neither."""
    res = _client("http://backend:8000").get(PROBE)
    assert res.status_code == 400 and res.json()["detail"] == "Invalid host header"


@pytest.mark.parametrize("base_url", [
    "http://x.attacker.example:3000", "http://localhost.attacker.example:8000", "http://[::1]:8000",
    "http://10.0.0.5:8000",
])
def test_a_foreign_host_is_refused(base_url):
    """DNS rebinding: the attacker's page is same-origin to the browser, but its Host is its
    own name."""
    res = _client(base_url).get(PROBE)
    assert res.status_code == 400 and res.json()["detail"] == "Invalid host header"


def test_the_host_allow_list_is_configuration():
    assert {"localhost", "127.0.0.1", "backend"} <= set(settings.allowed_hosts.split(","))


@pytest.mark.parametrize("forwarded,status", [
    ("localhost:3000", PASSED),
    ("127.0.0.1:3000", PASSED),
    ("x.attacker.example:3000", 400),
    # Every hop must be ours, not just the first.
    ("localhost:3000, x.attacker.example", 400),
    # The browser's side of the proxy is never the compose name: http://backend:3000 in a
    # browser is a name someone else resolved.
    ("backend:3000", 400),
])
def test_the_host_the_browser_used_is_checked_behind_the_proxy(forwarded, status):
    """Next's proxy always sends Host: backend:8000 and puts the browser's Host in
    X-Forwarded-Host; that is the one that tells a rebinding page apart."""
    res = _client("http://backend:8000").get(PROBE, headers={"X-Forwarded-Host": forwarded})
    assert res.status_code == status


def test_a_cross_site_get_without_an_origin_is_refused():
    """What an <img src="http://localhost:8000/..."> on another site sends."""
    res = _client("http://localhost:8000").get(PROBE, headers={"Sec-Fetch-Site": "cross-site"})
    assert res.status_code == 403 and res.json()["detail"] == "Cross-site request refused"


@pytest.mark.parametrize("origin", APP_ORIGINS)
def test_a_cross_site_get_from_the_app_passes(origin):
    """The Data Management log stream: http://127.0.0.1:3000 -> http://localhost:8000."""
    res = _client("http://localhost:8000").get(PROBE, headers={"Sec-Fetch-Site": "cross-site", "Origin": origin})
    assert res.status_code == PASSED


@pytest.mark.parametrize("site", ["same-origin", "same-site", "none"])
def test_non_cross_site_gets_pass(site):
    assert _client("http://localhost:8000").get(PROBE, headers={"Sec-Fetch-Site": site}).status_code == PASSED


@pytest.mark.parametrize("origin", ["https://evil.example", "null", "http://localhost:5173"])
def test_a_write_from_a_foreign_origin_is_refused(origin):
    """A cross-site <form method=post> to a query-string-only route, e.g. generating SIP
    instalments, needs no preflight, so CORS never sees it."""
    res = _client("http://localhost:8000").post(PROBE, headers={"Origin": origin})
    assert res.status_code == 403


@pytest.mark.parametrize("headers", [{"Origin": APP_ORIGINS[0]}, {"Origin": APP_ORIGINS[1]},
                                     {"Origin": "http://localhost:8000"}, {}])
def test_a_write_from_the_app_or_from_no_browser_passes(headers):
    assert _client("http://localhost:8000").post(PROBE, headers=headers).status_code == PASSED


def test_a_cors_preflight_from_the_app_still_gets_its_allow_origin():
    res = _client("http://localhost:8000").options("/api/meta/status", headers={
        "Origin": "http://127.0.0.1:3000", "Access-Control-Request-Method": "GET", "Sec-Fetch-Site": "cross-site",
    })
    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"


def test_a_refusal_is_logged(caplog):
    with caplog.at_level(logging.WARNING, logger="security"):
        _client("http://x.attacker.example").get(PROBE)
    assert any(r.name == "security" and "x.attacker.example" in r.getMessage() for r in caplog.records)


def test_dev_ps1s_own_status_call_passes(pg_db, monkeypatch):
    """scripts\\dev.ps1 asks this before every restart; a 4xx now makes it refuse to restart."""
    monkeypatch.setattr(settings, "enable_sync_daemon", False)
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        assert c.get("/api/admin/status").status_code == 200
        assert c.get("/api/health").status_code == 200
