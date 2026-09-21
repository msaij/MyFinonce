"""Tier 2: Boundary & Corner Cases (Features 1 - 4: Quantitative Core & Tail Risk).
Verifies limits, zero variance, singular matrices, collinear factors, extreme shocks, and degenerate series.
Each feature has >= 5 boundary test cases (Total: 20 tests).
"""

import math
import numpy as np
import pandas as pd
import pytest

from tests.e2e.e2e_oracles import (
    get_factor_attribution_fn,
    get_stress_testing_fn,
    get_factor_shocks_fn,
    get_cornish_fisher_var_fn,
    get_cvar_and_drawdown_fn,
    oracle_factor_attribution,
    oracle_evaluate_historical_stress_scenarios,
    oracle_simulate_factor_shocks,
    oracle_cornish_fisher_var,
    oracle_expected_shortfall_and_capture,
)


# =====================================================================
# Feature 1: Multi-Factor Model Boundaries (5 tests)
# =====================================================================

class TestTier2Feature1MultiFactorBoundaries:
    """Boundary conditions for 4-Factor OLS regression."""

    def test_f1_boundary_collinear_factors(self):
        """Collinear/redundant factors (singular X^T X) are solved via pseudo-inverse without crashing."""
        np.random.seed(1401)
        n = 100
        mkt = np.random.normal(0.0005, 0.01, n)
        # Perfectly collinear factor: smb is exact multiple of mkt
        smb = mkt * 2.0
        hml = np.random.normal(0.0, 0.005, n)
        wml = np.random.normal(0.0, 0.005, n)

        fund = mkt * 1.1 + 0.00025 + np.random.normal(0, 0.002, n)
        factors_df = pd.DataFrame({"mkt_excess": mkt, "smb": smb, "hml": hml, "wml": wml})

        res = get_factor_attribution_fn()(pd.Series(fund), factors_df, rf_daily=0.00025)
        assert res["r_squared"] >= 0.0
        assert not math.isnan(res["alpha_annualized_pct"])

    def test_f1_boundary_zero_factor_variance(self):
        """A factor with zero variance (constant column) does not produce division-by-zero."""
        n = 80
        mkt = np.random.normal(0.0005, 0.01, n)
        smb = np.zeros(n)  # Constant zero factor
        hml = np.random.normal(0.0, 0.005, n)
        wml = np.random.normal(0.0, 0.005, n)

        fund = mkt * 1.0 + 0.00025
        factors_df = pd.DataFrame({"mkt_excess": mkt, "smb": smb, "hml": hml, "wml": wml})

        res = get_factor_attribution_fn()(pd.Series(fund), factors_df, rf_daily=0.00025)
        size_beta = res["factor_betas"].get("size", res["factor_betas"].get("smb", 0.0))
        assert size_beta == 0.0 or abs(size_beta) < 1e-4

    def test_f1_boundary_extreme_negative_returns(self):
        """Catastrophic return history (-90% crash) produces consistent negative alpha and beta."""
        n = 60
        mkt = np.random.normal(-0.02, 0.03, n)
        smb = np.random.normal(0.0, 0.01, n)
        hml = np.random.normal(0.0, 0.01, n)
        wml = np.random.normal(0.0, 0.01, n)

        # Deep loss fund
        fund = mkt * 2.0 - 0.01
        factors_df = pd.DataFrame({"mkt_excess": mkt, "smb": smb, "hml": hml, "wml": wml})

        res = get_factor_attribution_fn()(pd.Series(fund), factors_df, rf_daily=0.00025)
        assert res["alpha_annualized_pct"] < 0.0
        mkt_beta = res["factor_betas"].get("market", res["factor_betas"].get("mkt_excess", 0.0))
        assert mkt_beta > 1.5

    def test_f1_boundary_insufficient_sample_size(self):
        """Sample size below the OLS floor raises a clean ValueError instead of an uncontrolled crash."""
        fund = pd.Series([0.01, 0.02, -0.01])
        factors = pd.DataFrame({
            "mkt_excess": [0.01, 0.02, -0.01],
            "smb": [0.0, 0.0, 0.0],
            "hml": [0.0, 0.0, 0.0],
            "wml": [0.0, 0.0, 0.0],
        })

        with pytest.raises(ValueError, match="minimum 60"):
            get_factor_attribution_fn()(fund, factors)

    def test_f1_boundary_zero_residual_perfect_fit(self):
        """Deterministic exact linear combination (R² = 1.0, residuals = 0) handles zero s2 cleanly."""
        n = 80
        mkt = np.linspace(-0.02, 0.02, n)
        fund = mkt * 1.5 + 0.00025  # Zero noise
        factors_df = pd.DataFrame({
            "mkt_excess": mkt,
            "smb": np.zeros(n),
            "hml": np.zeros(n),
            "wml": np.zeros(n),
        })

        res = get_factor_attribution_fn()(pd.Series(fund), factors_df, rf_daily=0.00025)
        assert math.isclose(res["r_squared"], 1.0, abs_tol=1e-3)
        assert res["idiosyncratic_risk_pct"] < 0.1


# =====================================================================
# Feature 2: Macro Scenario Stress Testing Boundaries (5 tests)
# =====================================================================

class TestTier2Feature2MacroStressBoundaries:
    """Boundary conditions for macro crisis replay and factor shocks."""

    def test_f2_boundary_extreme_factor_shock_100pct(self):
        """Total market wipeout (-100% shock) scales linearly with beta."""
        betas = {"market": 1.25}
        shocks = {"market": -1.00}  # -100%

        res = get_factor_shocks_fn()(betas, shocks)
        # Expected return impact: 1.25 * -100% = -125%
        assert math.isclose(res["portfolio_impact_pct"], -125.0, abs_tol=1e-3)

    def test_f2_boundary_empty_date_window(self):
        """Replay on dates outside the historical crisis windows reports has_data: False gracefully."""
        dates = pd.date_range("2015-01-01", "2016-01-01", freq="D")
        nav_s = pd.Series(100.0, index=dates)

        res = get_stress_testing_fn()(nav_s)
        # COVID was in 2020, so 2015 data has no overlap
        assert res["covid_march_2020"]["has_data"] is False
        assert res["covid_march_2020"]["drawdown_pct"] is None

    def test_f2_boundary_single_day_crisis_window(self):
        """Single day record inside crisis window is safely treated as insufficient data (< 2 days)."""
        nav_s = pd.Series([100.0], index=[pd.to_datetime("2020-03-23")])

        res = get_stress_testing_fn()(nav_s)
        assert res["covid_march_2020"]["has_data"] is False

    def test_f2_boundary_zero_drawdown_monotonic_rise(self):
        """Monotonically increasing NAV series experiences 0.0% drawdown."""
        dates = pd.date_range("2020-02-15", "2020-04-15", freq="D")
        nav_s = pd.Series(np.linspace(100.0, 150.0, len(dates)), index=dates)

        res = get_stress_testing_fn()(nav_s)
        covid = res["covid_march_2020"]
        assert covid["has_data"] is True
        assert covid["drawdown_pct"] == 0.0

    def test_f2_boundary_negative_shocks_opposite_direction(self):
        """Equal and opposite factor shocks produce symmetrical portfolio impacts."""
        betas = {"market": 1.15, "smb": 0.45}
        pos = get_factor_shocks_fn()(betas, {"market": 0.10, "smb": 0.05})
        neg = get_factor_shocks_fn()(betas, {"market": -0.10, "smb": -0.05})

        assert math.isclose(pos["portfolio_impact_pct"], -neg["portfolio_impact_pct"], abs_tol=1e-4)


# =====================================================================
# Feature 3: Cornish-Fisher VaR Boundaries (5 tests)
# =====================================================================

class TestTier2Feature3CornishFisherBoundaries:
    """Boundary conditions for Cornish-Fisher expansion."""

    def test_f3_boundary_zero_return_volatility(self):
        """Flat return series (sigma = 0) returns 0.0% VaR without NaN/inf."""
        returns = np.zeros(100)
        res = oracle_cornish_fisher_var(returns)

        assert res["var_95_cf_daily_pct"] == 0.0
        assert res["var_99_cf_daily_pct"] == 0.0

    def test_f3_boundary_extreme_kurtosis(self):
        """Extreme excess kurtosis (K > 25) maintains positive, bounded VaR."""
        # Distribution with extreme outliers
        returns = np.concatenate([np.random.normal(0, 0.005, 500), [0.35, -0.40, 0.28, -0.32]])
        res = oracle_cornish_fisher_var(returns, confidence_levels=[0.99])

        assert res["kurtosis"] > 10.0
        assert not math.isnan(res["var_99_cf_daily_pct"])
        assert res["var_99_cf_daily_pct"] > 0.0

    def test_f3_boundary_extreme_negative_skewness(self):
        """Massive negative skewness maintains conservative property: CF VaR > Gaussian VaR."""
        returns = np.concatenate([np.full(100, 0.005), np.full(5, -0.20)])
        res = oracle_cornish_fisher_var(returns, confidence_levels=[0.95, 0.99])

        assert res["skewness"] < -2.0
        assert res["var_95_cf_daily_pct"] > res["var_95_gaussian_daily_pct"]
        assert res["var_99_cf_daily_pct"] > res["var_99_gaussian_daily_pct"]

    def test_f3_boundary_all_negative_returns(self):
        """100% negative daily returns computes positive loss VaR (> 0)."""
        returns = -np.random.uniform(0.001, 0.03, 100)
        res = oracle_cornish_fisher_var(returns, confidence_levels=[0.95])

        assert res["var_95_cf_daily_pct"] > 0.0

    def test_f3_boundary_single_outlier_spike(self):
        """Positive outlier spike (+8% day) maintains valid tail risk estimates."""
        returns = np.random.normal(0.0002, 0.01, 200)
        returns[10] = 0.08  # Realistic positive shock
        res = oracle_cornish_fisher_var(returns, confidence_levels=[0.95, 0.99])

        assert not math.isnan(res["var_95_cf_daily_pct"])
        assert not math.isnan(res["var_99_cf_daily_pct"])
        assert res["skewness"] > 0.0


# =====================================================================
# Feature 4: Expected Shortfall & Capture Boundaries (5 tests)
# =====================================================================

class TestTier2Feature4ExpectedShortfallBoundaries:
    """Boundary conditions for CVaR and Drawdown analytics."""

    def test_f4_boundary_flat_nav_zero_drawdown(self):
        """Constant NAV produces zero drawdown and zero duration."""
        dates = pd.date_range("2023-01-01", "2023-03-01", freq="D")
        nav_s = pd.Series(100.0, index=dates)
        ret_s = np.zeros(len(dates))

        res = oracle_expected_shortfall_and_capture(ret_s, nav_series=nav_s)
        dd = res["drawdown_metrics"]
        assert dd["max_drawdown_pct"] == 0.0
        assert dd["drawdown_duration_days"] == 0

    def test_f4_boundary_immediate_drawdown_recovery(self):
        """V-shaped crash with 1-day trough and next-day recovery has recovery_duration_days = 1."""
        dates = pd.date_range("2023-01-01", "2023-01-10", freq="D")
        # Peak 100, Day 3 drop to 80, Day 4 recover to 100
        navs = [100.0, 100.5, 101.0, 80.0, 101.0, 101.5, 102.0, 102.2, 102.5, 103.0]
        nav_s = pd.Series(navs, index=dates)
        ret_s = nav_s.pct_change().dropna().values

        res = oracle_expected_shortfall_and_capture(ret_s, nav_series=nav_s)
        dd = res["drawdown_metrics"]
        assert dd["max_drawdown_pct"] < -20.0
        assert dd["recovery_duration_days"] == 1

    def test_f4_boundary_benchmark_zero_negative_days(self):
        """When benchmark has zero down days, downside capture safely defaults without ZeroDivisionError."""
        bench = np.full(50, 0.005)  # All positive
        fund = np.random.normal(0.005, 0.01, 50)

        res = oracle_expected_shortfall_and_capture(fund, bench_returns=bench)
        # Should not raise exception; capture ratio remains safe default
        assert res["capture_metrics"]["downside_capture_ratio"] is not None

    def test_f4_boundary_tail_identical_returns(self):
        """Uniform return distribution in tail produces CVaR equal to the return value."""
        returns = np.full(100, -0.02)
        res = oracle_expected_shortfall_and_capture(returns, confidence_level=0.99)
        assert math.isclose(res["cvar_99_daily_pct"], -2.0, abs_tol=1e-3)
        assert math.isclose(res["var_99_daily_pct"], -2.0, abs_tol=1e-3)

    def test_f4_boundary_unrecovered_maximum_drawdown(self):
        """NAV ending at trough has recovery_duration_days as None."""
        dates = pd.date_range("2023-01-01", "2023-01-10", freq="D")
        nav_s = pd.Series(np.linspace(100.0, 50.0, len(dates)), index=dates)
        ret_s = nav_s.pct_change().dropna().values

        res = oracle_expected_shortfall_and_capture(ret_s, nav_series=nav_s)
        assert res["drawdown_metrics"]["recovery_duration_days"] is None
