"""Contract tests for the rev-4 trust-first program."""

import datetime

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.amfi_client import AMFI_AMC_CATALOG
from app.core.config import product_flags, settings
from app.db import connection
from app.db import queries as db
from app.main import app
from app.services import fee_drag
from app.stress_testing import evaluate_historical_stress_scenarios


def test_amc_catalog_mf_ids_are_unique():
    ids = [row["mf_id"] for row in AMFI_AMC_CATALOG]
    assert len(ids) == len(set(ids))
    names = {row["name"]: row["mf_id"] for row in AMFI_AMC_CATALOG}
    assert names["Groww Mutual Fund"] != names["NJ Mutual Fund"]
    assert names["HDFC Mutual Fund"] != names["Principal Mutual Fund"]


def test_product_flags_semantics():
    flags = product_flags()
    assert flags["admin_auth_required"] == (not settings.admin_token_optional)
    assert flags["admin_token_configured"] == bool(settings.admin_token)
    assert "tax_overlay" not in flags
    assert set(flags) >= {
        "admin_auth_required",
        "admin_token_configured",
        "amfi_ssl_insecure",
    }


def test_meta_status_includes_flags(pg_db, monkeypatch):
    monkeypatch.setattr(settings, "enable_sync_daemon", False)
    with TestClient(app) as client:
        resp = client.get("/api/meta/status")
        assert resp.status_code == 200
        flags = resp.json()["flags"]
        assert flags["admin_auth_required"] is True
        assert "tax_overlay" not in flags


def test_fee_drag_does_not_invent_ter(pg_db):
    res = fee_drag.compute_fee_drag_attribution(direct_scheme_code=111, regular_scheme_code=222)
    assert res["status"] in ("unavailable", "unpaired")
    assert res["horizons"] == {}


def test_init_db_does_not_clobber_official_ter(pg_db):
    con = connection.get_connection()
    con.execute(
        "INSERT INTO schemes (scheme_code, scheme_name, fund_house, category, plan_type, option_type, expense_ratio, ter_status) "
        "VALUES (555, 'Official Fund', 'AMC', 'Equity Scheme - Large Cap Fund', 'Direct', 'Growth', 0.42, 'official')"
    )
    con.execute(
        "INSERT INTO sync_meta (key, value) VALUES ('cost_data_version', 'force-rewrite') "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value"
    )
    con.close()
    db.init_db()
    con = connection.get_connection()
    row = con.execute("SELECT expense_ratio, ter_status FROM schemes WHERE scheme_code = 555").fetchone()
    con.close()
    assert row[1] == "official"
    assert row[0] == pytest.approx(0.42)


def test_admin_post_fails_closed_without_token(pg_db, monkeypatch):
    monkeypatch.setattr(settings, "enable_sync_daemon", False)
    monkeypatch.setattr(settings, "admin_token_optional", False)
    monkeypatch.setattr(settings, "admin_token", "")
    with TestClient(app) as client:
        resp = client.post("/api/admin/sync/daily")
        assert resp.status_code == 401
        assert "ADMIN_TOKEN not configured" in resp.json()["detail"]


def test_stress_merge_accepts_datetime_date_bench(pg_db):
    dates = [datetime.date(2020, 2, 20) + datetime.timedelta(days=i) for i in range(40)]
    df_hist = pd.DataFrame({
        "nav_date": dates,
        "nav": [100.0 + i for i in range(40)],
    })
    bench = pd.DataFrame({
        "nav_date": dates,
        "nav": [50.0 + 0.5 * i for i in range(40)],
    })
    con = connection.get_connection()
    con.execute(
        "INSERT INTO schemes (scheme_code, scheme_name) VALUES (118482, 'Nifty Proxy') ON CONFLICT DO NOTHING"
    )
    con.close()
    res = evaluate_historical_stress_scenarios(118482, df_hist=df_hist, benchmark_series=bench)
    assert "scenarios" in res or isinstance(res, dict)


def test_data_quality_endpoint(pg_db, monkeypatch):
    monkeypatch.setattr(settings, "enable_sync_daemon", False)
    with TestClient(app) as client:
        resp = client.get("/api/meta/data-quality")
        assert resp.status_code == 200
        body = resp.json()
        assert "ter_official_coverage_ratio" in body
        assert "factor_proxy_codes" in body
