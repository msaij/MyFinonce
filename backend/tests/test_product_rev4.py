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
        "holdout_portfolios",
        "bl_ui",
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


def _picks():
    return [
        {"scheme_code": 1, "scheme_name": "Fund A", "fund_house": "AMC1", "category": "Large Cap", "sleeve": "Equity Core", "weight_pct": 60, "amount": 60000},
        {"scheme_code": 2, "scheme_name": "Fund B", "fund_house": "AMC2", "category": "Debt", "sleeve": "Debt", "weight_pct": 40, "amount": 40000},
    ]


def _rules_ok(*_a, **_k):
    return {"risk_tier": "Growth", "budget": 100000, "weights": {1: 0.6, 2: 0.4}, "picks": _picks()}


def _mvo_ok(*_a, **_k):
    return {"risk_tier": "Growth", "budget": 100000, "weights": {1: 0.5, 2: 0.5}, "picks": _picks()}


def _bt_factory(captured):
    def _bt(weights, start, end, *_a, **_k):
        captured.append((start, end))
        days = []
        d = start
        while d <= end:
            days.append(d)
            d += datetime.timedelta(days=7)
            if len(days) > 80:
                break
        if not days:
            days = [end]
        df = pd.DataFrame({
            "nav_date": days,
            "portfolio_value": [100000 + i for i in range(len(days))],
            "total_invested": [100000] * len(days),
        })
        return {
            "df_result": df,
            "final_value": 110000,
            "total_invested": 100000,
            "n_contributions": 1,
            "money_weighted_xirr_pct": 10.0,
            "twr_metrics": {"cagr_pct": 10.0, "sharpe_ratio": 1.0, "max_drawdown_pct": -5.0, "vol_annualized_pct": 12.0},
        }
    return _bt


def _nav_hist(codes, start_date=None, end_date=None):
    start_date = start_date or datetime.date(2020, 1, 1)
    end_date = end_date or datetime.date(2024, 12, 31)
    rows = []
    d = start_date
    i = 0
    while d <= end_date:
        for c in codes:
            rows.append({"scheme_code": c, "nav_date": d, "nav": 100.0 + i + c})
        d += datetime.timedelta(days=1)
        i += 1
        if i > 400:
            break
    return pd.DataFrame(rows)


def test_suggest_old_body_is_in_sample(pg_db, monkeypatch):
    monkeypatch.setattr(settings, "enable_sync_daemon", False)
    captured = []
    monkeypatch.setattr("app.portfolio_advisor.build_rules_based_portfolio", _rules_ok)
    monkeypatch.setattr("app.portfolio_advisor.build_mvo_portfolio", _mvo_ok)
    monkeypatch.setattr("app.portfolio_advisor.backtest_portfolio", _bt_factory(captured))
    monkeypatch.setattr("app.routers.portfolio_advisor.db.get_nav_history_dataframe", _nav_hist)
    with TestClient(app) as client:
        resp = client.post("/api/portfolio-advisor/suggest", json={
            "risk_tier": "Growth",
            "budget": 100000,
            "mode": "Lump Sum",
            "start_date": "2022-01-01",
            "end_date": "2025-01-01",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert "bl_result" not in body
        assert body["windows"]["sample"] == "is"
        assert body["mvo_backtest"] is not None and body["mvo_backtest"].get("sample") == "is"
        if body.get("hrp_backtest") and not body["hrp_backtest"].get("error"):
            assert body["hrp_backtest"].get("sample") == "is"


def test_holdout_backtest_dates_after_construction_end(pg_db, monkeypatch):
    monkeypatch.setattr(settings, "enable_sync_daemon", False)
    captured = []
    monkeypatch.setattr("app.portfolio_advisor.build_rules_based_portfolio", _rules_ok)
    monkeypatch.setattr("app.portfolio_advisor.build_mvo_portfolio", _mvo_ok)
    monkeypatch.setattr("app.portfolio_advisor.backtest_portfolio", _bt_factory(captured))
    monkeypatch.setattr("app.routers.portfolio_advisor.db.get_nav_history_dataframe", _nav_hist)
    c_end = datetime.date(2024, 1, 1)
    with TestClient(app) as client:
        resp = client.post("/api/portfolio-advisor/suggest", json={
            "risk_tier": "Growth",
            "budget": 100000,
            "mode": "Lump Sum",
            "start_date": "2021-01-01",
            "end_date": "2025-01-01",
            "holdout": True,
            "construction_start": "2021-01-01",
            "construction_end": "2024-01-01",
            "test_start": "2024-01-01",
            "test_end": "2025-01-01",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["windows"]["sample"] == "oos"
        eff = datetime.date.fromisoformat(body["windows"]["test_start_effective"])
        assert eff > c_end
        for start, _end in captured:
            assert start > c_end
        for key in ("rules_backtest", "mvo_backtest", "hrp_backtest"):
            bt = body.get(key) or {}
            if bt.get("error") or not bt.get("df_result"):
                continue
            for row in bt["df_result"]:
                nav_d = str(row["nav_date"])[:10]
                assert datetime.date.fromisoformat(nav_d) > c_end


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


def test_international_sleeve_name_tokens(pg_db):
    from app.portfolio_advisor import _sleeve_where_clause

    db.refresh_summary_table()
    con = connection.get_connection()
    rows = [
        (101, "Mirae Asset NYSE FANG+ ETF FoF Overseas Direct Growth", "Mirae", "FoF Overseas", "Direct", "Growth"),
        (102, "Motilal Oswal Nasdaq 100 ETF", "Motilal", "Other ETFs", None, "Growth"),
        (103, "Mirae Asset S&P 500 Top 50 ETF", "Mirae", "Index Funds", "", "Growth"),
        (104, "Nippon India ETF Nifty 50 BeES", "Nippon", "Other ETFs", None, "Growth"),
        (105, "HDFC Gold ETF", "HDFC", "Other ETFs", None, "Growth"),
    ]
    for r in rows:
        con.execute(
            "INSERT INTO summary_table (scheme_code, scheme_name, fund_house, category, plan_type, option_type, is_active) "
            "VALUES (%s, %s, %s, %s, %s, %s, TRUE)",
            r,
        )
    where, params = _sleeve_where_clause("International Equity")
    plan_clause = (
        "AND ("
        " (s.category LIKE '%%FoF Overseas%%' AND s.option_type = 'Growth' AND s.plan_type = 'Direct')"
        " OR ("
        "  (s.category LIKE '%%Other ETFs%%' OR s.category LIKE '%%Index Funds%%')"
        "  AND (s.option_type IS NULL OR s.option_type = '' OR s.option_type = 'Growth')"
        " )"
        ")"
    )
    names = {r[0] for r in con.execute(
        f"SELECT scheme_name FROM summary_table s WHERE {where} {plan_clause}", params
    ).fetchall()}
    con.close()
    assert any("FoF Overseas" in n for n in names)
    assert any("Nasdaq" in n for n in names)
    assert any("S&P 500" in n for n in names)
    assert not any("Nifty 50" in n for n in names)
    assert not any("Gold" in n for n in names)


def test_data_quality_endpoint(pg_db, monkeypatch):
    monkeypatch.setattr(settings, "enable_sync_daemon", False)
    with TestClient(app) as client:
        resp = client.get("/api/meta/data-quality")
        assert resp.status_code == 200
        body = resp.json()
        assert "ter_official_coverage_ratio" in body
        assert "factor_proxy_codes" in body
