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


def _nav_df(navs: list[float], start: str = "2024-01-01") -> pd.DataFrame:
    dates = pd.date_range(start, periods=len(navs), freq="D")
    return pd.DataFrame({"nav_date": dates, "nav": navs})


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
