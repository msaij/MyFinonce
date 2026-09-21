"""Router-level test for app/routers/quant.py -- specifically the one piece of
logic that lives only in the router, not in services/quant_service.py itself:
stripping the bulky regression_points DataFrame-turned-records out of the
benchmark.metrics response, and mapping a service-level {"error": ...} dict to
a 422 HTTP response. Full metric/figure correctness is covered at the service
level in test_quant_service.py; this only proves the HTTP boundary behaves.
"""

import datetime

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db import connection
from app.db import queries as db
from app.main import app

N_DAYS = 120
START = datetime.date(2025, 1, 1)
DATES = [START + datetime.timedelta(days=i) for i in range(N_DAYS)]


@pytest.fixture()
def client(pg_db, monkeypatch):
    # TestClient(app) triggers the real startup event, including
    # amfi_sync.ensure_sync_daemon_running() -- force this off regardless of the
    # container's real ENABLE_SYNC_DAEMON env var (defaults True as of Phase 9), or
    # every test run spins up a real background daemon thread that makes real
    # network calls to live AMFI servers (confirmed the hard way: a 21,124-row TER
    # portal fetch fired mid test-suite-run once the default flipped, dramatically
    # slowing every test in this file down while it happened).
    monkeypatch.setattr(settings, "enable_sync_daemon", False)

    with TestClient(app) as c:  # triggers the startup event (db.init_db(); daemon forced off above)
        con = connection.get_connection()
        con.execute(
            "INSERT INTO schemes (scheme_code, scheme_name, fund_house, category, plan_type, option_type, expense_ratio, ter_status) VALUES "
            "(111, 'Test Large Cap Fund - Direct Plan - Growth', 'AMC1', 'Equity Scheme - Large Cap Fund', 'Direct', 'Growth', 1.1, 'official'),"
            "(222, 'Peer Large Cap Fund - Direct Plan - Growth', 'AMC2', 'Equity Scheme - Large Cap Fund', 'Direct', 'Growth', 1.3, 'official')"
        )
        rows = []
        for i, d in enumerate(DATES):
            rows.append((111, d.isoformat(), round(100.0 * (1.0 + 0.0006 * i), 4)))
            rows.append((222, d.isoformat(), round(50.0 * (1.0 + 0.0004 * i), 4)))
        con.executemany("INSERT INTO nav_history (scheme_code, nav_date, nav) VALUES (%s, %s, %s)", rows)
        con.close()
        db.refresh_summary_table()
        yield c


def test_quant_endpoint_strips_regression_points_from_the_response(client):
    resp = client.get(
        "/api/quant/111",
        params={"start_date": DATES[10].isoformat(), "end_date": DATES[-1].isoformat(), "bench_mode": "Custom Peer Mutual Fund", "custom_peer_code": 222},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["benchmark"]["available"] is True
    assert "regression_points" not in body["benchmark"]["metrics"]
    assert "beta" in body["benchmark"]["metrics"]
    assert body["figures"]["capm_regression"]["data"]  # the figure itself still has the points, just not raw


def test_quant_endpoint_maps_service_error_to_422(client):
    resp = client.get("/api/quant/999999", params={"start_date": DATES[0].isoformat(), "end_date": DATES[-1].isoformat()})
    assert resp.status_code == 422


def test_monte_carlo_endpoint_happy_path(client):
    resp = client.get("/api/quant/111/monte-carlo", params={"start_date": DATES[10].isoformat(), "end_date": DATES[-1].isoformat()})
    assert resp.status_code == 200
    body = resp.json()
    assert body["figure"]["data"]
    assert 0.0 <= body["prob_profit_pct"] <= 100.0
