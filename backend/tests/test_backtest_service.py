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

import pytest

from app.db import connection
from app.db import queries as db
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
