"""Regression safety net for the dense, zero-prior-coverage math in
portfolio_sim.py -- this is exactly the code the migration plan flags as
highest-risk to silently break during a port (see the migration plan,
"Zero test coverage on the riskiest numerical code").
"""

import datetime

import numpy as np
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
        # Changed deliberately (2026-09): this window spans 9 days, and the app withholds
        # every annualised rate under 30 days (annualising a few days turns a small move
        # into a huge rate). Before, this asserted a 0% XIRR; now it must be withheld with
        # the reason, and the same flat round-trip is checked over a long window below.
        assert result["money_weighted_xirr_pct"] is None
        assert result["xirr_note"] == "too_short"
        assert result["twr_metrics"]["cagr_pct"] is None
        assert result["n_contributions"] == 1
        assert set(result["codes"]) == {1, 2}

    def test_flat_round_trip_over_a_year_is_a_zero_xirr(self):
        """Invest X, get X back a year later: a textbook 0% XIRR (and 0% TWR)."""
        dates = pd.date_range("2024-01-01", periods=400, freq="D")
        nav_wide = pd.DataFrame({1: [100.0] * 400}, index=dates)
        result = portfolio_sim.run_backtest(nav_wide, {1: 1.0}, "Lump Sum", 10000.0, 0.0, portfolio_sim.REBALANCE_NONE)
        assert result["money_weighted_xirr_pct"] == pytest.approx(0.0, abs=1e-6)
        assert result["twr_metrics"]["cagr_pct"] == pytest.approx(0.0, abs=1e-9)

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

    def test_non_positive_amount_is_an_error(self):
        nav_wide = self._two_fund_nav_wide()
        result = portfolio_sim.run_backtest(nav_wide, {1: 0.5, 2: 0.5}, "Lump Sum", 0.0, 0.0, portfolio_sim.REBALANCE_NONE)
        assert "error" in result


# --- Schedules -------------------------------------------------------------------------------


def _weekdays(start: str, end: str) -> pd.DatetimeIndex:
    d = pd.date_range(start, end, freq="D")
    return d[d.weekday < 5]


class TestSipSchedule:
    def test_due_date_on_a_weekend_rolls_to_the_next_trade_date(self):
        td = _weekdays("2024-05-01", "2024-07-31")
        out = portfolio_sim.sip_schedule(td, sip_day=1, schedule_from=datetime.date(2024, 5, 1))
        # 1 Jun 2024 was a Saturday -> Monday 3 Jun; 1 May and 1 Jul were weekdays.
        assert [d.date() for d in out] == [datetime.date(2024, 5, 1), datetime.date(2024, 6, 3), datetime.date(2024, 7, 1)]

    def test_day_31_means_month_end(self):
        td = pd.date_range("2024-01-01", "2024-04-30", freq="D")
        out = portfolio_sim.sip_schedule(td, sip_day=31, schedule_from=datetime.date(2024, 1, 1))
        assert [d.date() for d in out] == [datetime.date(2024, 1, 31), datetime.date(2024, 2, 29),
                                           datetime.date(2024, 3, 31), datetime.date(2024, 4, 30)]

    def test_due_date_before_the_window_start_is_skipped(self):
        td = pd.date_range("2024-01-01", "2024-03-31", freq="D")
        out = portfolio_sim.sip_schedule(td, sip_day=5, schedule_from=datetime.date(2024, 1, 20))
        assert [d.date() for d in out] == [datetime.date(2024, 2, 5), datetime.date(2024, 3, 5)]

    def test_due_date_rolled_past_the_window_end_is_dropped(self):
        td = _weekdays("2024-06-03", "2024-06-28")  # last weekday Fri 28 Jun
        out = portfolio_sim.sip_schedule(td, sip_day=29, schedule_from=datetime.date(2024, 6, 1),
                                         end=datetime.date(2024, 6, 30))
        assert out == []

    def test_no_sip_day_keeps_the_first_trade_date_of_each_month(self):
        td = _weekdays("2024-05-15", "2024-07-31")
        out = portfolio_sim.sip_schedule(td)
        assert [d.date() for d in out] == [datetime.date(2024, 5, 15), datetime.date(2024, 6, 3), datetime.date(2024, 7, 1)]


# --- Engine rules ----------------------------------------------------------------------------


class TestTradingRules:
    def test_purchase_is_never_allotted_at_a_carried_forward_nav(self):
        """A liquid fund prices Saturday; the equity fund does not. A SIP due on Saturday
        must buy the equity fund at MONDAY's NAV. The old engine bought it at Friday's
        (carried forward) and pocketed Monday's move."""
        cal = pd.date_range("2024-05-27", "2024-07-10", freq="D")
        equity = pd.Series(np.nan, index=cal)
        for d in cal[cal.weekday < 5]:
            equity[d] = 100.0
        equity[pd.Timestamp("2024-06-03")] = 110.0      # Monday jump
        equity[pd.Timestamp("2024-06-04"):] = 110.0
        equity[cal.weekday >= 5] = np.nan
        liquid = pd.Series(1000.0, index=cal)
        raw = pd.DataFrame({1: equity, 2: liquid})
        trade_dates = raw.dropna().index
        result = portfolio_sim.run_backtest(
            raw.ffill(), {1: 0.5, 2: 0.5}, "SIP", 0.0, 10000.0, portfolio_sim.REBALANCE_NONE,
            trade_dates=trade_dates, sip_day=1, schedule_from=datetime.date(2024, 6, 1),
        )
        buys = [t for t in result["trades"] if t["scheme_code"] == 1]
        assert buys[0]["date"] == datetime.date(2024, 6, 3)
        assert buys[0]["nav"] == pytest.approx(110.0)
        assert buys[0]["units"] == pytest.approx(5000.0 / 110.0)

    def test_stamp_duty_applies_from_july_2020_only(self):
        dates = pd.date_range("2020-06-29", periods=5, freq="D")
        nav = pd.DataFrame({1: [10.0] * 5}, index=dates)
        kw = dict(stamp_duty_rate=0.00005, stamp_duty_from=datetime.date(2020, 7, 1))
        before = portfolio_sim.run_backtest(nav, {1: 1.0}, "Lump Sum", 100000.0, 0.0, "None", **kw)
        after = portfolio_sim.run_backtest(nav.iloc[2:], {1: 1.0}, "Lump Sum", 100000.0, 0.0, "None", **kw)
        assert before["costs"]["stamp_duty"] == 0.0
        assert before["funds"][0]["units"] == pytest.approx(10000.0)
        # Stamp duty comes out of the amount paid: 100000 / 1.00005 is invested.
        assert after["costs"]["stamp_duty"] == pytest.approx(100000.0 - 100000.0 / 1.00005)
        assert after["funds"][0]["units"] == pytest.approx(100000.0 / 1.00005 / 10.0)
        assert after["final_value"] == pytest.approx(100000.0 / 1.00005)

    def test_rebalance_restores_target_weights_and_gains_add_up(self):
        dates = pd.date_range("2024-01-01", "2024-03-31", freq="D")
        nav = pd.DataFrame({1: np.linspace(100, 200, len(dates)), 2: [100.0] * len(dates)}, index=dates)
        result = portfolio_sim.run_backtest(nav, {1: 0.5, 2: 0.5}, "Lump Sum", 10000.0, 0.0, portfolio_sim.REBALANCE_MONTHLY)
        rows = result["df_result"].set_index("nav_date")
        for d in ("2024-02-01", "2024-03-01"):
            r = rows.loc[pd.Timestamp(d)]
            assert r["holding_1"] == pytest.approx(r["holding_2"])  # back to 50/50 after the trade
        assert result["costs"]["rebalance_count"] == 2
        assert sum(f["gain"] for f in result["funds"]) == pytest.approx(result["absolute_gain"])
        sells = [t for t in result["trades"] if t["type"] == "Rebalance sell"]
        assert all(t["scheme_code"] == 1 for t in sells)

    def test_exit_load_is_charged_on_young_units_and_reduces_the_buy(self):
        dates = pd.date_range("2024-01-01", "2024-02-15", freq="D")
        nav = pd.DataFrame({1: np.linspace(100, 200, len(dates)), 2: [100.0] * len(dates)}, index=dates)
        result = portfolio_sim.run_backtest(nav, {1: 0.5, 2: 0.5}, "Lump Sum", 10000.0, 0.0,
                                            portfolio_sim.REBALANCE_MONTHLY, exit_load_pct=1.0, exit_load_days=365)
        sell = next(t for t in result["trades"] if t["type"] == "Rebalance sell")
        buy = next(t for t in result["trades"] if t["type"] == "Rebalance buy")
        assert sell["exit_load"] == pytest.approx(0.01 * -sell["amount"])  # every unit is < 1 year old
        assert buy["amount"] == pytest.approx(-sell["amount"] - sell["exit_load"])
        assert result["costs"]["exit_load"] == pytest.approx(sell["exit_load"])
        assert result["costs"]["rebalance_sold_under_exit_load_days"] == pytest.approx(-sell["amount"])
        assert sum(f["gain"] for f in result["funds"]) == pytest.approx(result["absolute_gain"])

    def test_contributions_are_not_returns_in_the_twr(self):
        """A SIP into a flat NAV makes 0%. The value line climbs with every instalment,
        but the time-weighted index must not move."""
        dates = pd.date_range("2024-01-01", "2024-12-31", freq="D")
        nav = pd.DataFrame({1: [50.0] * len(dates)}, index=dates)
        result = portfolio_sim.run_backtest(nav, {1: 1.0}, "SIP", 0.0, 5000.0, "None", sip_day=10,
                                            schedule_from=datetime.date(2024, 1, 1))
        assert result["n_contributions"] == 12
        assert result["final_value"] == pytest.approx(60000.0)
        assert result["df_result"]["twr_index"].iloc[-1] == pytest.approx(100.0)
        assert result["twr_metrics"]["cagr_pct"] == pytest.approx(0.0, abs=1e-9)

    def test_lump_sum_twr_cagr_equals_xirr(self):
        dates = pd.date_range("2023-01-02", "2024-12-31", freq="D")
        nav = pd.DataFrame({1: 100.0 * 1.0003 ** np.arange(len(dates))}, index=dates)
        result = portfolio_sim.run_backtest(nav, {1: 1.0}, "Lump Sum", 10000.0, 0.0, "None")
        assert result["twr_metrics"]["cagr_pct"] == pytest.approx(result["money_weighted_xirr_pct"], abs=1e-6)

    def test_annualisation_base_follows_the_supplied_frequency(self):
        dates = pd.date_range("2024-01-01", "2024-12-31", freq="D")
        rng = np.random.default_rng(1)
        nav = pd.DataFrame({1: 100.0 * np.cumprod(1 + rng.normal(0, 0.01, len(dates)))}, index=dates)
        r365 = portfolio_sim.run_backtest(nav, {1: 1.0}, "Lump Sum", 1000.0, 0.0, "None", obs_per_year=365.25)
        r252 = portfolio_sim.run_backtest(nav, {1: 1.0}, "Lump Sum", 1000.0, 0.0, "None", obs_per_year=252.0)
        assert r365["twr_metrics"]["obs_per_year"] == pytest.approx(365.25)
        ratio = r365["twr_metrics"]["vol_annualized_pct"] / r252["twr_metrics"]["vol_annualized_pct"]
        assert ratio == pytest.approx((365.25 / 252.0) ** 0.5)


class TestCalendarYearReturns:
    def test_bases_on_the_previous_year_end_and_flags_partial_years(self):
        s = pd.Series(
            [100.0, 110.0, 121.0, 133.1],
            index=pd.DatetimeIndex(["2023-03-15", "2023-12-29", "2024-12-31", "2025-06-30"]),
        )
        out = {r["year"]: r for r in portfolio_sim.calendar_year_returns(s)}
        assert out[2023]["return_pct"] == pytest.approx(10.0) and out[2023]["partial"] is True
        assert out[2024]["return_pct"] == pytest.approx(10.0) and out[2024]["partial"] is False
        assert out[2024]["from"] == "2023-12-29"
        assert out[2025]["return_pct"] == pytest.approx(10.0) and out[2025]["partial"] is True
