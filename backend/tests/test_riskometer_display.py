"""The SEBI riskometer reaches every page that lists funds: All Funds, the Screener (and its
CSV), and a fund's own page -- blank, never guessed, where AMFI publishes none."""

import datetime

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db import connection
from app.main import app
from tests.holdings_support import business_days, seed

d = datetime.date


@pytest.fixture()
def client(pg_db, monkeypatch):
    # No background sync in tests: it would run a real catch-up against the test database
    # and hold the sync tracker other tests rely on being free.
    monkeypatch.setattr(settings, "enable_sync_daemon", False)
    days = list(business_days(d(2026, 1, 1), d(2026, 3, 31)))
    seed([(1001, "Alpha Liquid Fund", "Alpha MF", "Debt Scheme - Liquid Fund", "Direct", "Growth"),
          (1002, "Beta Fixed Maturity Plan Series 7", "Beta MF", "Income", "Direct", "Growth")],
         {1001: [(day, 1000.0 + i) for i, day in enumerate(days)],
          1002: [(day, 10.0 + i / 100) for i, day in enumerate(days)]})
    con = connection.get_connection()
    try:
        con.execute("UPDATE schemes SET riskometer = 'Low to Moderate', riskometer_as_of = '2026-03-27' WHERE scheme_code = 1001")
    finally:
        con.close()
    with TestClient(app) as c:
        yield c


def _by_code(rows):
    return {int(r["scheme_code"]): r for r in rows}


def test_all_funds_carries_the_riskometer(client):
    rows = _by_code(client.get("/api/overview/all-funds").json())
    assert (rows[1001]["riskometer"], rows[1001]["riskometer_as_of"]) == ("Low to Moderate", "2026-03-27")
    assert rows[1002]["riskometer"] is None


def test_the_screener_and_its_csv_carry_it(client):
    rows = _by_code(client.get("/api/screener?limit=0").json())
    assert rows[1001]["riskometer"] == "Low to Moderate" and rows[1002]["riskometer"] is None
    csv = client.get("/api/screener/export.csv").text
    assert "riskometer" in csv.splitlines()[0] and "Low to Moderate" in csv


def test_data_management_counts_coverage_over_live_schemes(client):
    con = connection.get_connection()
    try:
        # A wound-up scheme: listed, but no longer publishing, so it must not dilute coverage.
        con.execute("INSERT INTO schemes (scheme_code, scheme_name, fund_house) VALUES (1003, 'Gamma Closed Fund', 'Gamma MF')")
    finally:
        con.close()
    live = client.get("/api/admin/status").json()["live_coverage"]
    assert (live["live_schemes"], live["listed_schemes"]) == (2, 3)
    assert (live["riskometer"], live["riskometer_as_of"]) == (1, "2026-03-27")


def test_a_funds_own_page_carries_it(client):
    profile = client.get("/api/schemes/1001/profile").json()
    assert profile["riskometer"] == "Low to Moderate" and profile["riskometer_as_of"] == "2026-03-27"
    assert client.get("/api/schemes/1002/profile").json()["riskometer"] is None
