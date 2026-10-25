"""Tests for app/routers/schemes.py's dossier endpoint.

The scheme dossier is the one page that shows a single fund on its own, so the
profile payload carries two things the stored row does not: the app-wide display
label, and where the fund sits among its peers.
"""

import datetime

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db import connection
from app.db import queries as db
from app.main import app

DATES = [datetime.date(2025, 1, 1) + datetime.timedelta(days=i) for i in range(400)]


@pytest.fixture()
def client(pg_db, monkeypatch):
    # Same guard as the other router tests: never let a test run start the real
    # sync daemon and make live AMFI calls.
    monkeypatch.setattr(settings, "enable_sync_daemon", False)
    with TestClient(app) as c:
        con = connection.get_connection()
        con.execute(
            "INSERT INTO schemes (scheme_code, scheme_name, fund_house, category, plan_type, option_type, isin, expense_ratio, ter_status) VALUES "
            "(111, 'Alpha Liquid Fund', 'Alpha AMC', 'Debt Scheme - Liquid Fund', 'Direct', 'Growth', 'INF0001', 0.2, 'official'),"
            "(222, 'Beta Liquid Fund', 'Beta AMC', 'Debt Scheme - Liquid Fund', 'Direct', 'Growth', 'INF0002', 0.3, 'official'),"
            "(333, 'Gamma Liquid Fund', 'Gamma AMC', 'Debt Scheme - Liquid Fund', 'Direct', 'Growth', 'INF0003', 0.4, 'official')"
        )
        # Three funds in one category+plan, deliberately different growth rates, so the
        # percentile has something real to rank: 111 fastest, 333 slowest.
        rows = []
        for i, d in enumerate(DATES):
            rows.append((111, d.isoformat(), 100.0 + i * 0.30))
            rows.append((222, d.isoformat(), 100.0 + i * 0.20))
            rows.append((333, d.isoformat(), 100.0 + i * 0.10))
        con.executemany("INSERT INTO nav_history (scheme_code, nav_date, nav) VALUES (%s, %s, %s)", rows)
        con.close()
        db.refresh_summary_table()
        yield c


def test_profile_carries_the_app_wide_label(client):
    body = client.get("/api/schemes/111/profile").json()
    assert body["display_name"] == "Alpha Liquid Fund (Direct - Growth) [111]"
    assert body["scheme_name"] == "Alpha Liquid Fund", "the raw name stays untouched beside the label"


def test_profile_ranks_the_fund_against_its_own_category_and_plan(client):
    """A return is not good or bad on its own -- the dossier says where it sits."""
    best = client.get("/api/schemes/111/profile").json()["peer_rank"]["1y"]
    worst = client.get("/api/schemes/333/profile").json()["peer_rank"]["1y"]
    assert best["peers"] == worst["peers"] == 3
    assert best["percentile"] == 100.0 and worst["percentile"] == 0.0
    assert best["value"] > worst["value"]


def test_profile_reports_no_rank_rather_than_a_wrong_one_when_history_is_short(client):
    """Nothing in this fixture has 5 years of NAVs, so a 5-year percentile would be
    invented. The key must be present and empty, not silently absent."""
    body = client.get("/api/schemes/111/profile").json()
    assert set(body["peer_rank"]) == {"1y", "3y", "5y"}
    assert body["peer_rank"]["5y"] is None


def test_unknown_scheme_is_404(client):
    resp = client.get("/api/schemes/999999/profile")
    assert resp.status_code == 404 and "999999" in resp.json()["detail"]
