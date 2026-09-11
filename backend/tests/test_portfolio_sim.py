"""Regression safety net for the dense, zero-prior-coverage math in
portfolio_sim.py -- this is exactly the code the migration plan flags as
highest-risk to silently break during a port (see the migration plan,
"Zero test coverage on the riskiest numerical code").
"""

import datetime

import pandas as pd
import pytest

from app import portfolio_sim


class TestXirr:
    def test_closed_form_two_flow_case(self):
        """Invest 100000, receive 110000 365 days later. With exactly two cash
        flows, XIRR has a closed form: (F/P)^(1/years) - 1, where `years` is
        xirr()'s own day-count convention (elapsed_days / 365.25, confirmed
        by reading portfolio_sim.py) -- NOT a round 0.10, since a 365-day
        (whole calendar year) span is not exactly one 365.25-day "year" (and
        `datetime.date` can't represent the fractional 0.25 day needed to make
        it exact -- date arithmetic has no sub-day resolution, so
        `timedelta(days=365.25)` added to a date silently truncates to 365).
        Compute the expected rate from the same convention rather than assume
        a round number, then check the code matches its own documented
        formula tightly."""
        t0 = datetime.date(2024, 1, 1)
        t1 = t0 + datetime.timedelta(days=365)
        years = (t1 - t0).days / 365.25
        expected_rate = (110000.0 / 100000.0) ** (1.0 / years) - 1.0
        rate = portfolio_sim.xirr([(t0, -100000.0), (t1, 110000.0)])
        assert rate is not None
        assert rate == pytest.approx(expected_rate, abs=1e-9)

    def test_no_sign_change_is_unsolvable(self):
        """Two flows that never go negative-then-positive (or vice versa) is not
        a real investment cash-flow series -- must return None, never a wild
        or wrong rate."""
        t0 = datetime.date(2024, 1, 1)
        t1 = datetime.date(2024, 6, 1)
        assert portfolio_sim.xirr([(t0, 100.0), (t1, 200.0)]) is None

    def test_single_flow_is_unsolvable(self):
        assert portfolio_sim.xirr([(datetime.date(2024, 1, 1), -100.0)]) is None


class TestRunBacktest:
    def _two_fund_nav_wide(self) -> pd.DataFrame:
        """10 trading days. Scheme 1: 100 -> 110 (+10%). Scheme 2: 100 -> 90
        (-10%). Equal-weighted, no rebalancing: the two moves cancel exactly,
        so a lump-sum backtest's final value must equal what was invested,
        to the cent -- a hand-verifiable, not-approximate assertion."""
        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        nav_1 = [100 + i * (10 / 9) for i in range(10)]  # 100 -> 110
        nav_2 = [100 - i * (10 / 9) for i in range(10)]  # 100 -> 90
        return pd.DataFrame({1: nav_1, 2: nav_2}, index=dates)

    def test_lump_sum_equal_and_opposite_moves_nets_to_invested_amount(self):
        nav_wide = self._two_fund_nav_wide()
        result = portfolio_sim.run_backtest(
            nav_wide=nav_wide,
            weights={1: 0.5, 2: 0.5},
            mode="Lump Sum",
            lump_sum_amount=10000.0,
            sip_amount=0.0,
            rebalance_freq=portfolio_sim.REBALANCE_NONE,
        )
        assert "error" not in result
        assert result["total_invested"] == pytest.approx(10000.0)
        assert result["final_value"] == pytest.approx(10000.0, abs=0.01)
        assert result["absolute_gain"] == pytest.approx(0.0, abs=0.01)
        assert result["absolute_return_pct"] == pytest.approx(0.0, abs=0.01)
        # Flat round-trip (invest X, get X back later) is a textbook 0% XIRR.
        assert result["money_weighted_xirr_pct"] == pytest.approx(0.0, abs=0.1)
        assert result["n_contributions"] == 1
        assert set(result["codes"]) == {1, 2}

    def test_empty_window_returns_error_not_exception(self):
        result = portfolio_sim.run_backtest(
            nav_wide=pd.DataFrame(),
            weights={1: 1.0},
            mode="Lump Sum",
            lump_sum_amount=10000.0,
            sip_amount=0.0,
            rebalance_freq=portfolio_sim.REBALANCE_NONE,
        )
        assert "error" in result
