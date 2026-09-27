"""Regression safety net for quant_analytics.py, and targeted tests for this
migration's two deliberate edits to it: the Monte Carlo seed fix (module-level
np.random.seed -> a local np.random.default_rng(seed) parameter) and the
"-" -> None sentinel fix. See the migration plan's "DataFrame/numpy -> JSON
boundary" section for why both changes were made.
"""

import numpy as np
import pandas as pd
import pytest

from app import quant_analytics


def _nav_df(navs: list[float], start: str = "2024-01-01", freq: str = "D") -> pd.DataFrame:
    dates = pd.date_range(start, periods=len(navs), freq=freq)
    return pd.DataFrame({"nav_date": dates, "nav": navs})


def _walk(n: int, sigma: float = 0.006, seed: int = 5) -> list[float]:
    rets = np.random.default_rng(seed).normal(0.0004, sigma, n - 1)
    return list(100.0 * np.cumprod(np.concatenate([[1.0], 1.0 + rets])))


class TestAnnualizationBase:
    """sqrt(252) is only right for a trading-day series. Liquid and overnight funds publish
    a NAV every calendar day, so the base has to follow the data -- measured once where the
    series is born, then carried on the object rather than re-guessed at each use."""

    def test_calendar_day_series_lands_on_365_and_a_trading_day_series_near_252(self):
        calendar = pd.date_range("2022-01-01", "2024-12-31", freq="D")
        trading = pd.date_range("2022-01-01", "2024-12-31", freq="B")
        assert quant_analytics.infer_obs_per_year(calendar) == pytest.approx(365.25, abs=1.0)
        ppy = quant_analytics.infer_obs_per_year(trading)
        assert abs(ppy - 252.0) / 252.0 < 0.05                       # an all-equity book behaves as before
        assert ppy < 300                                             # and is nowhere near calendar-daily

    def test_a_suspended_fund_keeps_its_real_frequency(self):
        """The whole reason the base is not a row count: a fund that stopped publishing for
        three months still ticks 365 times a year when it publishes. len/span would call it
        ~335 and understate its annualised volatility by 4%."""
        days = pd.date_range("2022-01-01", "2024-12-31", freq="D")
        suspended = days[(days < "2023-05-01") | (days >= "2023-08-01")]
        naive = len(suspended) / ((suspended[-1] - suspended[0]).days / 365.25)
        assert naive < 340
        assert quant_analytics.infer_obs_per_year(suspended) == pytest.approx(365.25, abs=1.0)

    def test_an_attached_base_is_used_instead_of_re_deriving_one(self):
        """The attribute is the mechanism; re-derivation is only the fallback. A frame tagged
        upstream must annualise on that number even though its own rows say otherwise."""
        df = quant_analytics.compute_daily_returns(_nav_df(_walk(400), freq="B"))
        derived = quant_analytics.compute_risk_adjusted_metrics(df)
        quant_analytics.with_obs_per_year(df, quant_analytics.CALENDAR_DAYS_PER_YEAR)
        attached = quant_analytics.compute_risk_adjusted_metrics(df)
        assert abs(derived["obs_per_year"] - 252.0) / 252.0 < 0.05
        assert attached["obs_per_year"] == pytest.approx(365.25)
        assert attached["vol_annualized_pct"] == pytest.approx(
            derived["vol_annualized_pct"] * np.sqrt(365.25 / derived["obs_per_year"]), rel=1e-9
        )

    def test_a_daily_funds_volatility_is_no_longer_understated(self):
        """One return sample, two calendars. The calendar-day reading must be sqrt(365.25/252)
        = 1.20x the trading-day one -- previously they came out identical."""
        navs = _walk(900, sigma=0.0004)
        cal = quant_analytics.compute_risk_adjusted_metrics(quant_analytics.compute_daily_returns(_nav_df(navs, freq="D")))
        trd = quant_analytics.compute_risk_adjusted_metrics(quant_analytics.compute_daily_returns(_nav_df(navs, freq="B")))
        # Per-observation volatility is measured around the return expected for each
        # return's span (span_residuals: a + b x days, fitted). On the trading-day calendar
        # the Monday spans give the fit a slope to estimate, so the two readings of one
        # sample now differ by that fitted slope's degree of freedom (~0.04% here), not 0.
        assert cal["vol_daily_pct"] == pytest.approx(trd["vol_daily_pct"], rel=1e-2)
        assert cal["vol_annualized_pct"] / cal["vol_daily_pct"] == pytest.approx(np.sqrt(cal["obs_per_year"]), rel=1e-9)
        assert trd["vol_annualized_pct"] / trd["vol_daily_pct"] == pytest.approx(np.sqrt(trd["obs_per_year"]), rel=1e-9)
        assert cal["vol_annualized_pct"] > trd["vol_annualized_pct"] * 1.15

    def test_the_quant_page_annualises_a_window_on_the_cadence_it_had_then(self):
        """Same base as the Compare tab (cadence_obs_per_year): a fund that published daily
        years ago and on trading days now is not annualised on its old calendar."""
        dates = pd.date_range("2019-01-01", "2021-12-31", freq="D").append(pd.bdate_range("2022-01-03", "2024-12-31"))
        raw = pd.DataFrame({"nav_date": dates, "nav": _walk(len(dates))})
        assert quant_analytics.infer_obs_per_year(dates) > 350
        _, cov = quant_analytics.prepare_fund_timeseries(raw, pd.Timestamp("2024-06-01").date(), pd.Timestamp("2024-12-31").date())
        assert abs(cov["obs_per_year"] - 261.0) < 2.0

    def test_prepare_fund_timeseries_publishes_and_carries_the_base(self):
        raw = _nav_df(_walk(500), freq="D")
        df, cov = quant_analytics.prepare_fund_timeseries(raw, raw["nav_date"].iloc[60].date(), raw["nav_date"].iloc[-1].date())
        assert cov["obs_per_year"] == pytest.approx(365.25)
        assert quant_analytics.get_obs_per_year(df) == pytest.approx(365.25)   # survives the slice
        vol = df["rolling_vol_ann"].dropna()
        std = df["daily_return"].rolling(30).std().dropna()
        assert vol.iloc[-1] == pytest.approx(float(std.iloc[-1]) * np.sqrt(365.25) * 100.0)

    def test_the_overlap_ticks_as_often_as_the_sparser_series(self):
        """A daily liquid fund measured against a trading-day benchmark only shares trading
        days, so tracking error must annualise on 252-ish, not on the fund's own 365."""
        fund = quant_analytics.compute_daily_returns(_nav_df(_walk(800), freq="D"))
        bench = quant_analytics.compute_daily_returns(_nav_df(_walk(600, seed=9), freq="B"))
        rel = quant_analytics.compute_benchmark_relative_metrics(fund, bench)
        assert abs(rel["obs_per_year"] - 252.0) / 252.0 < 0.05


class TestComputeRiskAdjustedMetrics:
    def test_total_return_is_exact_regardless_of_path(self):
        """total_return_pct depends only on the first/last NAV *among rows
        with a defined daily_return* -- compute_risk_adjusted_metrics()
        deliberately dropna(subset=["daily_return"]) first, and a series's
        very first row always has a NaN daily_return (pct_change has nothing
        before it to diff against), so that row is excluded from navs[0].
        For [100, 90, 130, 80, 200], the row with nav=100 is dropped, so
        total_return is measured from 90 (the second point) to 200: a
        (200-90)/90 = 122.222...% move, not from the original 100 -- this
        was the first version of this test's own wrong assumption, not a
        code bug; asserting the real, documented behavior here instead."""
        df = quant_analytics.compute_daily_returns(_nav_df([100, 90, 130, 80, 200]))
        metrics = quant_analytics.compute_risk_adjusted_metrics(df)
        assert metrics["total_return_pct"] == pytest.approx((200 - 90) / 90 * 100.0)

    def test_monotonic_rise_has_no_drawdown_and_calmar_is_none_not_dash(self):
        """A NAV series that only ever goes up has zero drawdown -- Calmar
        (CAGR / |max drawdown|) is mathematically undefined (division by zero),
        not zero. The pre-migration code represented "undefined" with the
        string "-" mixed into an otherwise-numeric dict; this migration's
        deliberate fix makes it None (JSON null) instead -- assert the fix,
        not the old sentinel."""
        df = quant_analytics.compute_daily_returns(_nav_df([100, 105, 110, 115, 120, 125]))
        metrics = quant_analytics.compute_risk_adjusted_metrics(df)
        assert metrics["max_drawdown_pct"] == pytest.approx(0.0)
        assert metrics["calmar_ratio"] is None
        assert metrics["calmar_ratio"] != "-"

    def test_win_rate_matches_hand_count(self):
        """4 up days, 1 down day out of 5 -> exactly 80% win rate."""
        df = quant_analytics.compute_daily_returns(_nav_df([100, 110, 121, 133.1, 130, 143.1]))
        metrics = quant_analytics.compute_risk_adjusted_metrics(df)
        assert metrics["win_rate_pct"] == pytest.approx(80.0)

    def test_too_short_series_returns_empty_dict(self):
        df = quant_analytics.compute_daily_returns(_nav_df([100, 101]))
        assert quant_analytics.compute_risk_adjusted_metrics(df) == {}


class TestRunMonteCarloSimulation:
    def _returns(self) -> np.ndarray:
        rng = np.random.default_rng(1)  # only to build a plausible input series, not under test
        return rng.normal(0.0005, 0.01, size=252)

    def test_same_seed_is_fully_reproducible(self):
        """The whole point of this migration's edit: a seed parameter replacing
        a global np.random.seed(42) call. Two calls with the same explicit seed
        must produce byte-identical percentile bands."""
        returns = self._returns()
        result_a = quant_analytics.run_monte_carlo_simulation(100.0, returns, n_simulations=200, n_days=50, seed=7)
        result_b = quant_analytics.run_monte_carlo_simulation(100.0, returns, n_simulations=200, n_days=50, seed=7)
        assert result_a["p50"] == pytest.approx(result_b["p50"])
        assert result_a["expected_terminal"] == pytest.approx(result_b["expected_terminal"])

    def test_different_seed_gives_different_result(self):
        """Confirms the fix actually parameterizes the randomness (not silently
        ignoring `seed` and still reading module-global state)."""
        returns = self._returns()
        result_a = quant_analytics.run_monte_carlo_simulation(100.0, returns, n_simulations=200, n_days=50, seed=1)
        result_b = quant_analytics.run_monte_carlo_simulation(100.0, returns, n_simulations=200, n_days=50, seed=2)
        assert result_a["expected_terminal"] != pytest.approx(result_b["expected_terminal"])

    def test_default_seed_still_42_for_backward_compatible_determinism(self):
        """Existing callers that don't pass `seed` must keep getting the same
        deterministic behavior the app has always shown users (its own UI
        caption says the analysis is deterministic) -- the default changed
        from an unconditional global side effect to an explicit default
        argument, but the observable default behavior must not change."""
        returns = self._returns()
        result_a = quant_analytics.run_monte_carlo_simulation(100.0, returns, n_simulations=200, n_days=50)
        result_b = quant_analytics.run_monte_carlo_simulation(100.0, returns, n_simulations=200, n_days=50, seed=42)
        assert result_a["p50"] == pytest.approx(result_b["p50"])

    def test_echoes_the_base_it_stepped_on(self):
        """The step count and the chart's years-from-today divisor have to be the same
        number, or a "5 year" fan of a calendar-day series silently plots 3.45 years."""
        result = quant_analytics.run_monte_carlo_simulation(
            100.0, self._returns(), n_simulations=50, n_days=1826, obs_per_year=365.25
        )
        assert result["n_days"] == 1826 and result["obs_per_year"] == pytest.approx(365.25)
        assert result["n_days"] / result["obs_per_year"] == pytest.approx(5.0, abs=0.01)
        assert len(result["days"]) == result["n_days"] + 1

    def test_does_not_mutate_global_numpy_random_state(self):
        """Before the fix, np.random.seed(42) inside the function reset global
        RNG state as a side effect of merely calling it -- unsafe under
        concurrent requests in a server. After the fix, calling this function
        must leave the global RNG's state exactly as it was found."""
        returns = self._returns()
        np.random.seed(123)
        before = np.random.get_state()[1].copy()
        quant_analytics.run_monte_carlo_simulation(100.0, returns, n_simulations=50, n_days=20, seed=99)
        after = np.random.get_state()[1]
        assert np.array_equal(before, after)


class TestComputeBenchmarkRelativeMetrics:
    def test_perfectly_tracking_benchmark_has_beta_one_and_alpha_zero(self):
        """A fund whose daily returns are identical to its benchmark's is the
        textbook Beta=1, Alpha=0, R-squared=1 case."""
        df_fund = quant_analytics.compute_daily_returns(_nav_df([100, 102, 101, 105, 108, 107, 110]))
        df_bench = quant_analytics.compute_daily_returns(_nav_df([100, 102, 101, 105, 108, 107, 110]))
        metrics = quant_analytics.compute_benchmark_relative_metrics(df_fund, df_bench)
        assert metrics["beta"] == pytest.approx(1.0, abs=1e-6)
        assert metrics["alpha_annualized_pct"] == pytest.approx(0.0, abs=1e-6)
        assert metrics["r_squared"] == pytest.approx(1.0, abs=1e-6)

    def test_empty_inputs_return_empty_dict(self):
        assert quant_analytics.compute_benchmark_relative_metrics(pd.DataFrame(), pd.DataFrame()) == {}
