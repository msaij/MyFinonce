"""Tests for app/services/backtest.py -- in particular a regression test for
a real bug found while writing this service: portfolio_sim.run_backtest()'s
rebalance-date helpers require nav_wide.index to be a genuine
pandas.DatetimeIndex (they call .year/.month/.quarter, vectorized accessors
that don't exist on a plain object-dtype Index of datetime.date values).
db.get_nav_history_dataframe() returns nav_date as plain datetime.date
(via the sqlite3 DATE converter), so skipping the pd.to_datetime()
conversion in run_portfolio_backtest() would crash on any SIP or
rebalanced backtest -- but NOT on a plain Lump-Sum + no-rebalance one,
which never calls those helpers and would misleadingly "pass". These tests
specifically exercise SIP + Monthly rebalancing so a regression can't hide
behind the easy case.
"""

import datetime

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import quant_analytics
from app.core.config import settings
from app.db import connection
from app.db import queries as db
from app.main import app
from app.services import backtest as backtest_service


DATES = [datetime.date(2025, 1, 1) + datetime.timedelta(days=i) for i in range(90)]  # ~3 months


@pytest.fixture()
def db_con(pg_db):
    db.init_db()

    con = connection.get_connection()
    con.execute(
        "INSERT INTO schemes (scheme_code, scheme_name, fund_house, category, plan_type, option_type) VALUES "
        "(111, 'Fund A', 'AMC1', 'Equity Scheme - Large Cap Fund', 'Direct', 'Growth'),"
        "(222, 'Fund B', 'AMC2', 'Debt Scheme - Liquid Fund', 'Direct', 'Growth')"
    )
    rows = []
    for i, d in enumerate(DATES):
        rows.append((111, d.isoformat(), 100.0 + i * 0.5))  # gentle upward drift
        rows.append((222, d.isoformat(), 50.0 + (i % 10) * 0.1))  # small oscillation
    con.executemany("INSERT INTO nav_history (scheme_code, nav_date, nav) VALUES (%s, %s, %s)", rows)
    con.close()
    yield


class TestRunPortfolioBacktest:
    def test_sip_with_monthly_rebalancing_does_not_crash(self, db_con):
        """The exact combination that would AttributeError without the
        pd.to_datetime() fix (SIP needs _month_starts() for contribution
        dates; Monthly rebalancing needs it again for rebalance dates)."""
        result = backtest_service.run_portfolio_backtest(
            scheme_codes=[111, 222],
            weights={111: 60.0, 222: 40.0},
            mode="SIP (Monthly)",
            lump_sum_amount=0.0,
            sip_amount=5000.0,
            rebalance_freq="Monthly",
            start_date=DATES[0],
            end_date=DATES[-1],
        )
        assert "error" not in result
        assert result["final_value"] > 0
        assert result["n_contributions"] >= 2  # spans ~3 months of SIP contributions

    def test_lump_sum_no_rebalance_still_works(self, db_con):
        result = backtest_service.run_portfolio_backtest(
            scheme_codes=[111, 222],
            weights={111: 50.0, 222: 50.0},
            mode="Lump Sum",
            lump_sum_amount=10000.0,
            sip_amount=0.0,
            rebalance_freq="None",
            start_date=DATES[0],
            end_date=DATES[-1],
        )
        assert "error" not in result
        assert result["total_invested"] == pytest.approx(10000.0)

    def test_quarterly_rebalance_does_not_crash(self, db_con):
        result = backtest_service.run_portfolio_backtest(
            scheme_codes=[111, 222],
            weights={111: 50.0, 222: 50.0},
            mode="Lump Sum",
            lump_sum_amount=10000.0,
            sip_amount=0.0,
            rebalance_freq="Quarterly",
            start_date=DATES[0],
            end_date=DATES[-1],
        )
        assert "error" not in result

    def test_weights_are_normalized_regardless_of_input_scale(self, db_con):
        # Raw weights don't need to sum to 100 -- the service normalizes them.
        result = backtest_service.run_portfolio_backtest(
            scheme_codes=[111, 222],
            weights={111: 3.0, 222: 1.0},  # sums to 4, not 100
            mode="Lump Sum",
            lump_sum_amount=10000.0,
            sip_amount=0.0,
            rebalance_freq="None",
            start_date=DATES[0],
            end_date=DATES[-1],
        )
        assert result["normalized_weights"][111] == pytest.approx(0.75)
        assert result["normalized_weights"][222] == pytest.approx(0.25)

    def test_zero_total_weight_is_a_clean_error_not_a_crash(self, db_con):
        result = backtest_service.run_portfolio_backtest(
            scheme_codes=[111, 222],
            weights={111: 0.0, 222: 0.0},
            mode="Lump Sum",
            lump_sum_amount=10000.0,
            sip_amount=0.0,
            rebalance_freq="None",
            start_date=DATES[0],
            end_date=DATES[-1],
        )
        assert "error" in result

    def test_scheme_with_no_data_is_reported_as_missing_not_a_crash(self, db_con):
        result = backtest_service.run_portfolio_backtest(
            scheme_codes=[111, 999999],  # 999999 doesn't exist
            weights={111: 50.0, 999999: 50.0},
            mode="Lump Sum",
            lump_sum_amount=10000.0,
            sip_amount=0.0,
            rebalance_freq="None",
            start_date=DATES[0],
            end_date=DATES[-1],
        )
        assert "error" not in result
        assert 999999 in result["missing_codes"]
        assert result["normalized_weights"][111] == pytest.approx(1.0)

    def test_result_includes_a_real_xirr(self, db_con):
        result = backtest_service.run_portfolio_backtest(
            scheme_codes=[111, 222],
            weights={111: 50.0, 222: 50.0},
            mode="Lump Sum",
            lump_sum_amount=10000.0,
            sip_amount=0.0,
            rebalance_freq="None",
            start_date=DATES[0],
            end_date=DATES[-1],
        )
        assert result["money_weighted_xirr_pct"] is not None
        assert result["twr_metrics"]["cagr_pct"] is not None


# --- Window, trade dates, warnings, benchmarks (2026-09 review) --------------------------------

YEAR = [datetime.date(2024, 1, 1) + datetime.timedelta(days=i) for i in range(366)]  # 2024 is a leap year


def _nav(i: int, slope: float) -> float:
    return 100.0 + i * slope


@pytest.fixture()
def market(pg_db, monkeypatch):
    """A small market: an equity fund (weekdays only), a liquid fund (every day), a fund
    launched in March, one that stops publishing in September, an IDCW option with its
    Growth sibling, and an index fund to benchmark against."""
    monkeypatch.setattr(settings, "enable_sync_daemon", False)
    quant_analytics.get_synthetic_category_benchmark.cache_clear()
    con = connection.get_connection()
    con.execute(
        "INSERT INTO schemes (scheme_code, scheme_name, fund_house, category, plan_type, option_type, expense_ratio, ter_status) VALUES "
        "(501, 'Alpha Large Cap Fund', 'A', 'Equity Scheme - Large Cap Fund', 'Direct', 'Growth', 0.5, 'official'),"
        "(502, 'Beta Liquid Fund', 'B', 'Debt Scheme - Liquid Fund', 'Direct', 'Growth', 0.1, 'official'),"
        "(503, 'Gamma Mid Cap Fund', 'C', 'Equity Scheme - Mid Cap Fund', 'Direct', 'Growth', 0.6, 'official'),"
        "(504, 'Delta Small Cap Fund', 'D', 'Equity Scheme - Small Cap Fund', 'Direct', 'Growth', 0.7, 'official'),"
        "(505, 'Omega Corporate Bond Fund', 'E', 'Debt Scheme - Corporate Bond Fund', 'Direct', 'IDCW', 0.3, 'official'),"
        "(506, 'Omega Corporate Bond Fund', 'E', 'Debt Scheme - Corporate Bond Fund', 'Direct', 'Growth', 0.3, 'official'),"
        "(507, 'Nifty Index Fund', 'F', 'Other Scheme - Index Funds', 'Direct', 'Growth', 0.1, 'official')"
    )
    rows = []
    for i, d in enumerate(YEAR):
        rows.append((502, d, _nav(i, 0.02)))
        if d.weekday() >= 5:
            continue
        rows.append((501, d, _nav(i, 0.10)))
        rows.append((505, d, _nav(i, 0.01)))
        rows.append((506, d, _nav(i, 0.02)))
        rows.append((507, d, _nav(i, 0.05)))
        if d >= datetime.date(2024, 3, 1):
            rows.append((503, d, _nav(i, 0.20)))
        if d <= datetime.date(2024, 9, 30):
            rows.append((504, d, _nav(i, 0.15)))
    con.executemany("INSERT INTO nav_history (scheme_code, nav_date, nav) VALUES (%s, %s, %s)", rows)
    con.close()
    yield


def _run(codes, weights, **kw):
    args = dict(mode="SIP (Monthly)", lump_sum_amount=0.0, sip_amount=10000.0, rebalance_freq="None",
                start_date=datetime.date(2024, 1, 1), end_date=datetime.date(2024, 12, 31))
    args.update(kw)
    return backtest_service.run_portfolio_backtest(scheme_codes=codes, weights=weights, **args)


class TestWindowAndTradeDates:
    def test_sip_due_on_a_weekend_is_allotted_on_the_next_date_every_fund_priced(self, market):
        r = _run([501, 502], {501: 50, 502: 50}, sip_day=1)
        assert "error" not in r
        dates = sorted({t["date"] for t in r["trades"]})
        assert all(d.weekday() < 5 for d in dates)
        assert datetime.date(2024, 6, 3) in dates and datetime.date(2024, 6, 1) not in dates  # 1 Jun was a Saturday
        # The value series still runs on the liquid fund's weekends.
        assert any(pd.Timestamp(x).weekday() >= 5 for x in r["df_result"]["nav_date"])
        # A SIP into a blended calendar is annualised on ~365 obs/yr, measured from the data.
        assert r["assumptions"]["obs_per_year"] > 300

    def test_default_sip_day_is_the_start_dates_day(self, market):
        r = _run([501], {501: 100}, start_date=datetime.date(2024, 1, 10))
        assert r["assumptions"]["sip_day"] == 10
        assert [t["date"].day for t in r["trades"]][:3] == [10, 12, 11]  # 10 Feb is Sat -> Mon 12; 10 Mar Sun -> Mon 11

    def test_young_fund_moves_the_start_and_says_why(self, market):
        r = _run([501, 503], {501: 50, 503: 50})
        assert r["window"]["simulated_start"] == "2024-03-01"
        assert r["window"]["start_limited_by"] == [503]
        w = next(w for w in r["warnings"] if w["code"] == "start_limited")
        assert "Gamma Mid Cap Fund (Direct - Growth) [503]" in w["message"]

    def test_zero_weight_fund_does_not_shorten_the_window(self, market):
        r = _run([501, 503], {501: 100, 503: 0})
        assert r["window"]["simulated_start"] == "2024-01-01"
        assert r["zero_weight_codes"] == [503]
        assert [f["scheme_code"] for f in r["funds"]] == [501]

    def test_fund_that_stops_publishing_ends_the_simulation_instead_of_freezing(self, market):
        r = _run([501, 504], {501: 50, 504: 50})
        assert r["window"]["simulated_end"] == "2024-09-30"
        assert r["window"]["end_limited_by"] == [504]
        assert any(w["code"] == "end_limited" for w in r["warnings"])

    def test_negative_weight_is_rejected(self, market):
        r = _run([501, 502], {501: 150, 502: -50})
        assert "error" in r

    def test_short_window_withholds_annualised_rates(self, market):
        r = _run([501], {501: 100}, mode="Lump Sum", lump_sum_amount=10000.0,
                 start_date=datetime.date(2024, 6, 3), end_date=datetime.date(2024, 6, 14))
        assert r["money_weighted_xirr_pct"] is None and r["xirr_note"] == "too_short"
        assert r["twr_metrics"]["cagr_pct"] is None
        assert r["twr_metrics"]["total_return_pct"] is not None
        assert any(w["code"] == "short_window" for w in r["warnings"])


class TestIdcwAndBenchmarks:
    def test_idcw_fund_is_flagged_with_its_growth_option(self, market):
        r = _run([501, 505], {501: 50, 505: 50})
        f = next(f for f in r["funds"] if f["scheme_code"] == 505)
        assert f["is_idcw"] is True
        assert f["growth_alternative"]["scheme_code"] == 506
        w = next(w for w in r["warnings"] if w["code"] == "idcw")
        assert "Omega Corporate Bond Fund (Direct - Growth) [506]" in w["message"]

    def test_category_benchmark_of_a_fund_alone_in_its_category_is_the_fund_itself(self, market):
        """501 is the only Direct-Growth fund in its category, so its peer average IS 501:
        the benchmark run on the same schedule must land on exactly the same value."""
        # Starts mid-January so the peer series has a level before day one, as it always
        # does on real data (these synthetic NAVs begin on 1 January).
        r = _run([501], {501: 100}, benchmark="category", start_date=datetime.date(2024, 1, 15))
        b = r["benchmark"]
        assert b["kind"] == "category" and "error" not in b
        assert b["final_value"] == pytest.approx(r["final_value"], rel=1e-9)
        assert b["money_weighted_xirr_pct"] == pytest.approx(r["money_weighted_xirr_pct"], abs=1e-6)
        assert "benchmark_value" in r["df_result"].columns

    def test_scheme_benchmark_gets_the_same_money_on_the_same_dates(self, market):
        r = _run([501], {501: 100}, mode="Lump Sum", lump_sum_amount=100000.0, benchmark="scheme", benchmark_code=507)
        b = r["benchmark"]
        first, last = 0, YEAR.index(datetime.date(2024, 12, 31))
        expected = 100000.0 / 1.00005 * _nav(last, 0.05) / _nav(first, 0.05)
        assert b["final_value"] == pytest.approx(expected)
        assert b["name"] == "Nifty Index Fund (Direct - Growth) [507]"

    def test_gains_by_fund_add_up_to_the_portfolio_gain(self, market):
        r = _run([501, 502, 506], {501: 50, 502: 30, 506: 20}, rebalance_freq="Quarterly", exit_load_pct=1.0)
        assert sum(f["gain"] for f in r["funds"]) == pytest.approx(r["absolute_gain"])
        assert r["costs"]["stamp_duty"] > 0


def test_endpoint_serialises_the_full_result(market):
    with TestClient(app) as c:
        resp = c.post("/api/backtest", json={
            "scheme_codes": [501, 502], "weights": {"501": 60, "502": 40}, "mode": "SIP (Monthly)",
            "sip_amount": 5000, "rebalance_freq": "Quarterly", "start_date": "2024-01-01", "end_date": "2024-12-31",
            "benchmark": "category",
        })
    assert resp.status_code == 200
    body = resp.json()
    assert "error" not in body
    assert body["df_result"][0]["nav_date"].startswith("2024-01-01")
    assert body["trades"][0]["date"] == "2024-01-01"
    assert body["calendar_years"][0]["year"] == 2024
    assert body["assumptions"]["stamp_duty_rate_pct"] == pytest.approx(0.005)
    assert {"funds", "benchmark", "warnings", "window", "costs"} <= set(body)


# --- Category peers: asset class, coverage holes, start warning (2026-09-25 audit) -------------


@pytest.fixture()
def market_peers(market):
    """507 (Nifty index fund) now shares "Other Scheme - Index Funds" with a gilt index fund;
    601 is an IDCW contra fund whose only Growth peer stops publishing at the end of June."""
    con = connection.get_connection()
    con.execute(
        "INSERT INTO schemes (scheme_code, scheme_name, fund_house, category, plan_type, option_type) VALUES "
        "(508, 'Omega Gilt Index Fund', 'G', 'Other Scheme - Index Funds', 'Direct', 'Growth'),"
        "(601, 'Sigma Contra Fund', 'S', 'Equity Scheme - Contra Fund', 'Direct', 'IDCW'),"
        "(602, 'Tau Contra Fund', 'T', 'Equity Scheme - Contra Fund', 'Direct', 'Growth')"
    )
    rows = []
    for i, day in enumerate(YEAR):
        if day.weekday() >= 5:
            continue
        rows.append((508, day, _nav(i, 0.01)))
        rows.append((601, day, _nav(i, 0.08)))
        if day <= datetime.date(2024, 6, 28):
            rows.append((602, day, _nav(i, 0.12)))
    con.executemany("INSERT INTO nav_history (scheme_code, nav_date, nav) VALUES (%s, %s, %s)", rows)
    con.close()
    quant_analytics.get_synthetic_category_benchmark.cache_clear()
    yield


class TestCategoryPeers:
    def test_index_fund_peers_are_its_own_asset_class(self, market_peers):
        """507's category also holds a gilt index fund; its equity peer group is 507 alone,
        so the benchmark lands exactly on the portfolio (it used to average in the gilt fund)."""
        r = _run([507], {507: 100}, mode="Lump Sum", lump_sum_amount=100000.0, benchmark="category",
                 start_date=datetime.date(2024, 1, 15))
        b = r["benchmark"]
        assert b["components"][0]["asset_class"] == "Equity"
        assert b["final_value"] == pytest.approx(r["final_value"], rel=1e-9)

    def test_peer_series_that_stops_early_is_not_carried_flat(self, market_peers):
        r = _run([601], {601: 100}, mode="Lump Sum", lump_sum_amount=100000.0, benchmark="category",
                 start_date=datetime.date(2024, 1, 15))
        comp = r["benchmark"]["components"][0]
        assert comp["own_nav_used"] is True
        assert comp["own_nav_reason"].startswith("peer series ends 2024-06-28")
        w = next(w for w in r["warnings"] if w["code"] == "benchmark_partial")
        assert "peer series ends 2024-06-28" in w["message"]
        # Neutral slice: the benchmark is the fund itself, not a frozen peer level from July on.
        assert r["benchmark"]["final_value"] == pytest.approx(r["final_value"], rel=1e-9)

    def test_start_warning_names_the_first_allotment_when_it_is_later(self, market):
        r = _run([501, 503], {501: 50, 503: 50}, sip_day=15)
        w = next(w for w in r["warnings"] if w["code"] == "start_limited")
        assert "cannot start before 2024-03-01" in w["message"]
        assert "allotted on 2024-03-15" in w["message"]