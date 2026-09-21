"""
Adversarial empirical stress-testing harness for Milestone 1:
Factor Risk Attribution (factor_model.py) and Crisis Stress Testing (stress_testing.py).

Verifies under hostile and degenerate inputs:
1. Factor collinearity (perfect collinearity, near-singular X^T X condition numbers up to 10^16, rank-deficiency).
2. Degenerate return series (all zero, constant equal to rf, constant positive, NaN, Inf, flash crash, extreme spikes).
3. Extreme factor shocks (-50% market drop, +100% volatility/factor spikes, multiple simultaneous factor crashes).
4. Crisis window date slicing (sparse NAV dates, missing lookbacks, flat NAV, monotonic NAV, duplicate dates, shuffled dates).
5. Mathematical invariance: Variance decomposition strictly sums to 100.0% across 1,000 randomized Monte Carlo trials.
"""

import math
import numpy as np
import pandas as pd
import pytest

from app.factor_model import (
    compute_multivariate_factor_attribution,
    build_factor_figures,
)
from app.stress_testing import (
    evaluate_historical_stress_scenarios,
    simulate_factor_shocks,
    generate_stress_figure,
)


class TestFactorCollinearityAdversarial:
    """Adversarially tests collinearity, near-singularity, and rank deficiency in factor regression."""

    def test_perfect_collinearity_two_factors(self):
        """When SMB is exactly equal to MKT, X^T X is strictly singular."""
        dates = pd.date_range("2023-01-01", periods=100, freq="B")
        mkt = np.random.randn(100) * 0.01
        hml = np.random.randn(100) * 0.01
        wml = np.random.randn(100) * 0.01
        factors = pd.DataFrame({"mkt_excess": mkt, "smb": mkt, "hml": hml, "wml": wml}, index=dates)
        fund = pd.Series(mkt * 1.5 + hml * 0.2, index=dates)

        res = compute_multivariate_factor_attribution(fund, factors)
        assert res["n_observations"] == 100
        decomp_sum = sum(res["variance_decomposition"].values())
        assert math.isclose(decomp_sum, 100.0, abs_tol=1e-2), f"Decomp sum {decomp_sum} != 100%"
        assert 0.0 <= res["r_squared"] <= 1.0

    def test_perfect_collinearity_all_four_factors(self):
        """When all 4 factors are identical, design matrix has rank 2 (intercept + 1 factor)."""
        dates = pd.date_range("2023-01-01", periods=100, freq="B")
        mkt = np.random.randn(100) * 0.01
        factors = pd.DataFrame({"mkt_excess": mkt, "smb": mkt, "hml": mkt, "wml": mkt}, index=dates)
        fund = pd.Series(mkt * 2.0, index=dates)

        res = compute_multivariate_factor_attribution(fund, factors)
        decomp = res["variance_decomposition"]
        assert math.isclose(sum(decomp.values()), 100.0, abs_tol=1e-2)
        # All 4 equal should result in balanced equal split
        assert math.isclose(decomp["market"], 25.0, abs_tol=1e-2)
        assert math.isclose(decomp["size"], 25.0, abs_tol=1e-2)
        assert math.isclose(decomp["value"], 25.0, abs_tol=1e-2)
        assert math.isclose(decomp["momentum"], 25.0, abs_tol=1e-2)

    def test_exact_linear_combination_of_factors(self):
        """WML is an exact linear combination: 0.5*MKT - 0.3*SMB + 0.8*HML."""
        dates = pd.date_range("2023-01-01", periods=120, freq="B")
        rng = np.random.default_rng(42)
        mkt = rng.normal(0, 0.01, 120)
        smb = rng.normal(0, 0.01, 120)
        hml = rng.normal(0, 0.01, 120)
        wml = 0.5 * mkt - 0.3 * smb + 0.8 * hml
        factors = pd.DataFrame({"mkt_excess": mkt, "smb": smb, "hml": hml, "wml": wml}, index=dates)
        fund = pd.Series(mkt * 1.2 + smb * 0.3, index=dates)

        res = compute_multivariate_factor_attribution(fund, factors)
        decomp_sum = sum(res["variance_decomposition"].values())
        assert math.isclose(decomp_sum, 100.0, abs_tol=1e-2)
        assert 0.0 <= res["r_squared"] <= 1.0

    @pytest.mark.parametrize("eps", [1e-4, 1e-8, 1e-12, 1e-16])
    def test_near_singular_matrix_extreme_condition_numbers(self, eps):
        """Tests condition numbers from 10^4 up to machine precision 10^16."""
        dates = pd.date_range("2023-01-01", periods=100, freq="B")
        rng = np.random.default_rng(999)
        mkt = rng.normal(0, 0.01, 100)
        smb = mkt + eps * rng.normal(0, 0.01, 100)
        hml = rng.normal(0, 0.01, 100)
        wml = rng.normal(0, 0.01, 100)
        factors = pd.DataFrame({"mkt_excess": mkt, "smb": smb, "hml": hml, "wml": wml}, index=dates)
        fund = pd.Series(mkt * 1.1 + rng.normal(0, 0.001, 100), index=dates)

        res = compute_multivariate_factor_attribution(fund, factors)
        decomp_sum = sum(res["variance_decomposition"].values())
        assert math.isclose(decomp_sum, 100.0, abs_tol=1e-2), f"Decomp sum {decomp_sum} != 100% for eps={eps}"
        assert 0.0 <= res["r_squared"] <= 1.0

    def test_constant_factor_zero_variance(self):
        """When SMB has zero variance (all 0.0), its beta and variance contribution must be 0.0."""
        dates = pd.date_range("2023-01-01", periods=100, freq="B")
        rng = np.random.default_rng(555)
        mkt = rng.normal(0, 0.01, 100)
        hml = rng.normal(0, 0.01, 100)
        wml = rng.normal(0, 0.01, 100)
        factors = pd.DataFrame({"mkt_excess": mkt, "smb": [0.0] * 100, "hml": hml, "wml": wml}, index=dates)
        fund = pd.Series(mkt * 1.1 + hml * 0.4, index=dates)

        res = compute_multivariate_factor_attribution(fund, factors)
        assert math.isclose(res["factor_betas"]["size"], 0.0, abs_tol=1e-5)
        assert math.isclose(res["variance_decomposition"]["size"], 0.0, abs_tol=1e-3)
        assert math.isclose(sum(res["variance_decomposition"].values()), 100.0, abs_tol=1e-2)

    def test_constant_factor_nonzero_collinear_with_intercept(self):
        """When SMB is constant 0.05, it is directly collinear with the constant intercept column."""
        dates = pd.date_range("2023-01-01", periods=100, freq="B")
        rng = np.random.default_rng(777)
        mkt = rng.normal(0, 0.01, 100)
        hml = rng.normal(0, 0.01, 100)
        wml = rng.normal(0, 0.01, 100)
        factors = pd.DataFrame({"mkt_excess": mkt, "smb": [0.05] * 100, "hml": hml, "wml": wml}, index=dates)
        fund = pd.Series(mkt * 1.2 + hml * 0.3, index=dates)

        res = compute_multivariate_factor_attribution(fund, factors)
        assert math.isclose(sum(res["variance_decomposition"].values()), 100.0, abs_tol=1e-2)
        assert res["r_squared"] >= 0.0


class TestReturnDistributionsAndEdgeCasesAdversarial:
    """Tests pathological return series: zero, constant, NaN, Inf, flash crashes."""

    def test_zero_return_series_fund(self):
        """Fund returns are all 0.0 on every trading day."""
        dates = pd.date_range("2023-01-01", periods=80, freq="B")
        rng = np.random.default_rng(111)
        factors = pd.DataFrame({
            "mkt_excess": rng.normal(0, 0.01, 80),
            "smb": rng.normal(0, 0.01, 80),
            "hml": rng.normal(0, 0.01, 80),
            "wml": rng.normal(0, 0.01, 80),
        }, index=dates)
        fund = pd.Series([0.0] * 80, index=dates)

        res = compute_multivariate_factor_attribution(fund, factors)
        assert res["r_squared"] == 0.0
        assert math.isclose(sum(res["variance_decomposition"].values()), 100.0, abs_tol=1e-2)

    def test_constant_returns_equal_rf(self):
        """Fund return exactly equals daily risk free rate (excess return is identically zero)."""
        dates = pd.date_range("2023-01-01", periods=60, freq="B")
        rf = 0.00025
        rng = np.random.default_rng(222)
        factors = pd.DataFrame({
            "mkt_excess": rng.normal(0, 0.01, 60),
            "smb": rng.normal(0, 0.01, 60),
            "hml": rng.normal(0, 0.01, 60),
            "wml": rng.normal(0, 0.01, 60),
        }, index=dates)
        fund = pd.Series([rf] * 60, index=dates)

        res = compute_multivariate_factor_attribution(fund, factors, rf_daily=rf)
        assert res["r_squared"] == 0.0
        assert math.isclose(sum(res["variance_decomposition"].values()), 100.0, abs_tol=1e-2)

    def test_all_zero_factor_returns(self):
        """All 4 factors are zero on all dates."""
        dates = pd.date_range("2023-01-01", periods=80, freq="B")
        factors = pd.DataFrame({
            "mkt_excess": [0.0] * 80,
            "smb": [0.0] * 80,
            "hml": [0.0] * 80,
            "wml": [0.0] * 80,
        }, index=dates)
        fund = pd.Series(np.random.randn(80) * 0.01, index=dates)

        res = compute_multivariate_factor_attribution(fund, factors)
        assert res["r_squared"] == 0.0
        assert math.isclose(sum(res["variance_decomposition"].values()), 100.0, abs_tol=1e-2)

    def test_negative_factor_betas_euler_decomposition(self):
        """Inverse fund with negative market and size betas."""
        dates = pd.date_range("2023-01-01", periods=150, freq="B")
        rng = np.random.default_rng(333)
        mkt = rng.normal(0, 0.01, 150)
        smb = rng.normal(0, 0.01, 150)
        hml = rng.normal(0, 0.01, 150)
        wml = rng.normal(0, 0.01, 150)
        factors = pd.DataFrame({"mkt_excess": mkt, "smb": smb, "hml": hml, "wml": wml}, index=dates)
        fund = pd.Series(-1.4 * mkt - 0.7 * smb + 0.3 * hml, index=dates)

        res = compute_multivariate_factor_attribution(fund, factors)
        assert math.isclose(sum(res["variance_decomposition"].values()), 100.0, abs_tol=1e-2)
        assert res["factor_betas"]["market"] < -1.0
        assert res["factor_betas"]["size"] < -0.5

    def test_flash_crash_and_extreme_outliers(self):
        """Single-day flash crash of -99.9% and single-day spike of +500%."""
        dates = pd.date_range("2023-01-01", periods=100, freq="B")
        rng = np.random.default_rng(444)
        mkt = rng.normal(0, 0.01, 100)
        smb = rng.normal(0, 0.01, 100)
        hml = rng.normal(0, 0.01, 100)
        wml = rng.normal(0, 0.01, 100)
        factors = pd.DataFrame({"mkt_excess": mkt, "smb": smb, "hml": hml, "wml": wml}, index=dates)

        # Flash crash
        fund_crash = pd.Series(mkt * 1.0, index=dates)
        fund_crash.iloc[40] = -0.999
        res_crash = compute_multivariate_factor_attribution(fund_crash, factors)
        assert math.isclose(sum(res_crash["variance_decomposition"].values()), 100.0, abs_tol=1e-2)

        # Spike
        fund_spike = pd.Series(mkt * 1.0, index=dates)
        fund_spike.iloc[60] = 5.0
        res_spike = compute_multivariate_factor_attribution(fund_spike, factors)
        assert math.isclose(sum(res_spike["variance_decomposition"].values()), 100.0, abs_tol=1e-2)

    def test_nan_interleaved_in_fund_and_factors(self):
        """Interleaved NaN values must be cleanly aligned and dropped."""
        dates = pd.date_range("2023-01-01", periods=80, freq="B")
        rng = np.random.default_rng(555)
        mkt = rng.normal(0, 0.01, 80)
        smb = rng.normal(0, 0.01, 80)
        hml = rng.normal(0, 0.01, 80)
        wml = rng.normal(0, 0.01, 80)
        factors = pd.DataFrame({"mkt_excess": mkt, "smb": smb, "hml": hml, "wml": wml}, index=dates)
        fund = pd.Series(mkt * 1.1, index=dates)

        fund.iloc[5] = np.nan
        fund.iloc[15] = np.nan
        factors.iloc[20, 0] = np.nan
        factors.iloc[30, 2] = np.nan

        res = compute_multivariate_factor_attribution(fund, factors)
        assert res["n_observations"] == 76  # 4 rows dropped
        assert math.isclose(sum(res["variance_decomposition"].values()), 100.0, abs_tol=1e-2)

    def test_non_overlapping_dates_raises_clean_value_error(self):
        """Fund returns and factor returns with zero overlapping dates must raise clean ValueError."""
        dates_fund = pd.date_range("2020-01-01", periods=30, freq="B")
        dates_factors = pd.date_range("2023-01-01", periods=30, freq="B")
        fund = pd.Series(0.01, index=dates_fund)
        factors = pd.DataFrame({
            "mkt_excess": [0.01] * 30,
            "smb": [0.0] * 30,
            "hml": [0.0] * 30,
            "wml": [0.0] * 30,
        }, index=dates_factors)

        with pytest.raises(ValueError, match="Insufficient overlapping observations"):
            compute_multivariate_factor_attribution(fund, factors)

    def test_build_factor_figures_handles_constant_fund(self):
        """Plotly figures generation works without error on constant or zero-return funds."""
        dates = pd.date_range("2023-01-01", periods=80, freq="B")
        rng = np.random.default_rng(666)
        factors = pd.DataFrame({
            "mkt_excess": rng.normal(0, 0.01, 80),
            "smb": rng.normal(0, 0.01, 80),
            "hml": rng.normal(0, 0.01, 80),
            "wml": rng.normal(0, 0.01, 80),
        }, index=dates)
        fund = pd.Series([0.005] * 80, index=dates)

        res = compute_multivariate_factor_attribution(fund, factors)
        figs = build_factor_figures(res, "Constant Fund")
        assert "factor_waterfall" in figs
        assert "factor_decomposition" in figs


class TestExtremeFactorShocksAdversarial:
    """Stress tests parametric factor shock simulation under extreme boundaries."""

    def test_extreme_market_drop_negative_50_pct(self):
        betas = {"market": 1.25, "size": 0.30, "value": -0.10, "momentum": 0.15}
        # -50% shock decimal: -0.50
        res_dec = simulate_factor_shocks(betas, {"market": -0.50})
        # -50% shock percentage: -50.0
        res_pct = simulate_factor_shocks(betas, {"market": -50.0})

        assert math.isclose(res_dec["total_return_impact_pct"], -62.5, abs_tol=1e-3)
        assert math.isclose(res_pct["total_return_impact_pct"], -62.5, abs_tol=1e-3)
        assert math.isclose(res_dec["rupee_impact"], -62500.0, abs_tol=1e-2)
        assert math.isclose(res_dec["projected_capital"], 37500.0, abs_tol=1e-2)

    def test_extreme_positive_shock_volatility_spike_100_pct(self):
        """Tests factor with positive 100% shock."""
        betas = {"market": 1.0, "momentum": 0.8}
        res_dec = simulate_factor_shocks(betas, {"momentum": 1.0})  # 1.0 = 100%
        res_pct = simulate_factor_shocks(betas, {"momentum": 100.0})

        assert math.isclose(res_dec["total_return_impact_pct"], 80.0, abs_tol=1e-3)
        assert math.isclose(res_pct["total_return_impact_pct"], 80.0, abs_tol=1e-3)
        assert math.isclose(res_dec["projected_capital"], 180000.0, abs_tol=1e-2)

    def test_multi_factor_extreme_drawdown(self):
        """Simultaneous catastrophic shock across all factors."""
        betas = {"market": 1.1, "size": 0.6, "value": 0.4, "momentum": 0.5}
        shocks = {"market": -0.40, "size": -0.30, "value": -0.20, "momentum": -0.25}
        # Expected: 1.1*-40 + 0.6*-30 + 0.4*-20 + 0.5*-25 = -44 - 18 - 8 - 12.5 = -82.5%
        res = simulate_factor_shocks(betas, shocks, initial_capital=500000.0)

        assert math.isclose(res["total_return_impact_pct"], -82.5, abs_tol=1e-3)
        assert math.isclose(res["rupee_impact"], -412500.0, abs_tol=1e-2)
        assert math.isclose(res["projected_capital"], 87500.0, abs_tol=1e-2)

    def test_empty_or_extraneous_factors_in_shocks(self):
        """Shocks dictionary containing factors not present in betas, or betas empty."""
        betas = {"market": 1.0}
        shocks = {"crypto": -0.50, "oil": 0.30}
        res = simulate_factor_shocks(betas, shocks)

        assert res["total_return_impact_pct"] == 0.0
        assert res["rupee_impact"] == 0.0
        assert res["projected_capital"] == 100000.0

        res_empty = simulate_factor_shocks({}, {})
        assert res_empty["total_return_impact_pct"] == 0.0

    def test_factor_shocks_is_percentage_continuity(self):
        """Verifies continuous scaling when is_percentage flag is explicitly passed."""
        betas = {"market": 1.20, "size": 0.40}
        # Decimal mode
        r_dec1 = simulate_factor_shocks(betas, {"market": 0.01}, is_percentage=False)
        r_dec2 = simulate_factor_shocks(betas, {"market": 0.0105}, is_percentage=False)
        assert math.isclose(r_dec2["total_return_impact_pct"] / r_dec1["total_return_impact_pct"], 1.05, rel_tol=1e-5)
        # Percentage mode
        r_pct1 = simulate_factor_shocks(betas, {"market": 1.0}, is_percentage=True)
        r_pct2 = simulate_factor_shocks(betas, {"market": 1.05}, is_percentage=True)
        assert math.isclose(r_pct2["total_return_impact_pct"] / r_pct1["total_return_impact_pct"], 1.05, rel_tol=1e-5)
        assert math.isclose(r_pct1["total_return_impact_pct"], r_dec1["total_return_impact_pct"], rel_tol=1e-5)


class TestCrisisWindowDateSlicingAdversarial:
    """Tests historical stress testing under degenerate date slices and NAV series."""

    def test_sparse_crisis_window_sub_threshold(self):
        """Fund with fewer than 3 dates in crisis window must report available=False."""
        df_sparse = pd.DataFrame({
            "nav_date": ["2019-12-01", "2020-01-01", "2020-03-01", "2020-03-15", "2020-06-01"],
            "nav": [100.0, 102.0, 95.0, 80.0, 110.0],
        })
        # In COVID window (2020-02-15 to 2020-04-15), only 2 dates exist
        res = evaluate_historical_stress_scenarios(111, df_hist=df_sparse)
        covid = res["covid_march_2020"]
        assert covid["available"] is False
        assert "not active" in covid.get("reason", "").lower()

    def test_sparse_crisis_window_minimal_valid(self):
        """Fund with exactly 3 dates in crisis window successfully computes drawdown."""
        df_3dates = pd.DataFrame({
            "nav_date": ["2019-12-01", "2020-01-01", "2020-02-20", "2020-03-15", "2020-04-01", "2020-05-01"],
            "nav": [100.0, 105.0, 100.0, 70.0, 80.0, 110.0],
        })
        res = evaluate_historical_stress_scenarios(222, df_hist=df_3dates)
        covid = res["covid_march_2020"]
        assert covid["available"] is True
        assert math.isclose(covid["max_drawdown_pct"], -33.3333, abs_tol=1e-2)
        assert covid["trough_date"] == "2020-03-15"
        assert covid["recovered"] is True

    def test_fund_starts_at_crisis_start_date_no_lookback(self):
        """Fund starts on 2020-02-15 with no prior lookback."""
        dates = pd.date_range("2020-02-15", "2020-12-31", freq="D")
        navs = [100.0 - i * 0.2 if i < 30 else 94.0 + (i - 30) * 0.3 for i in range(len(dates))]
        df_fund = pd.DataFrame({"nav_date": dates, "nav": navs})

        res = evaluate_historical_stress_scenarios(333, df_hist=df_fund)
        covid = res["covid_march_2020"]
        assert covid["available"] is True
        assert covid["peak_date"] == "2020-02-15"

    def test_flat_nav_throughout_crisis(self):
        """Fund maintains exact 10.0 NAV throughout crisis."""
        dates = pd.date_range("2020-01-01", "2021-01-01", freq="D")
        df_fund = pd.DataFrame({"nav_date": dates, "nav": [10.0] * len(dates)})

        res = evaluate_historical_stress_scenarios(444, df_hist=df_fund)
        covid = res["covid_march_2020"]
        assert covid["available"] is True
        assert covid["max_drawdown_pct"] == 0.0
        assert covid["recovered"] is True

    def test_monotonically_increasing_nav_during_crisis(self):
        """Inverse/hedged fund that rose every day during the crash."""
        dates = pd.date_range("2020-01-01", "2021-01-01", freq="D")
        df_fund = pd.DataFrame({"nav_date": dates, "nav": np.linspace(100, 200, len(dates))})

        res = evaluate_historical_stress_scenarios(555, df_hist=df_fund)
        covid = res["covid_march_2020"]
        assert covid["available"] is True
        assert covid["max_drawdown_pct"] == 0.0

    def test_duplicate_dates_in_nav_history(self):
        """Duplicate dates in NAV history do not trigger crashes or unhandled exceptions."""
        dates = pd.date_range("2020-01-01", "2021-01-01", freq="D").tolist()
        dates_dup = dates + [pd.to_datetime("2020-03-23"), pd.to_datetime("2020-03-23")]
        navs = [100.0 - (15.0 if d >= pd.to_datetime("2020-03-01") else 0.0) for d in dates_dup]
        df_dup = pd.DataFrame({"nav_date": dates_dup, "nav": navs})

        res = evaluate_historical_stress_scenarios(666, df_hist=df_dup)
        covid = res["covid_march_2020"]
        assert covid["available"] is True
        assert math.isclose(covid["max_drawdown_pct"], -15.0, abs_tol=1e-2)

    def test_unsorted_nav_dates(self):
        """Randomly shuffled dates are correctly sorted and yield valid crisis metrics."""
        dates = pd.date_range("2020-01-01", "2021-01-01", freq="D").tolist()
        np.random.default_rng(12).shuffle(dates)
        navs = [100.0 - (25.0 if d >= pd.to_datetime("2020-03-20") else 0.0) for d in dates]
        df_shuffled = pd.DataFrame({"nav_date": dates, "nav": navs})

        res = evaluate_historical_stress_scenarios(777, df_hist=df_shuffled)
        covid = res["covid_march_2020"]
        assert covid["available"] is True
        assert math.isclose(covid["max_drawdown_pct"], -25.0, abs_tol=1e-2)
        assert covid["trough_date"] == "2020-03-20"

    def test_nonexistent_scheme_in_db_unhandled_attribute_error(self):
        """Evaluating non-existent scheme_code without df_hist is handled gracefully with available=False."""
        res = evaluate_historical_stress_scenarios(scheme_code=-999999)
        assert res is not None
        assert res["scheme_code"] == -999999
        assert "covid_march_2020" in res
        assert res["covid_march_2020"]["available"] is False

    def test_all_nan_navs_in_crisis_window_unhandled_value_error(self):
        """When NAVs during the crisis window are all NA, handled gracefully with available=False."""
        dates = pd.date_range("2020-01-01", "2021-01-01", freq="D")
        df = pd.DataFrame({"nav_date": dates, "nav": [100.0] * len(dates)})
        df.loc[(df["nav_date"] >= "2020-02-15") & (df["nav_date"] <= "2020-04-15"), "nav"] = None
        res = evaluate_historical_stress_scenarios(100, df_hist=df)
        assert res is not None
        assert "covid_march_2020" in res
        assert res["covid_march_2020"]["available"] is False


class TestMonteCarloVarianceDecompositionInvariant:
    """Stress tests the mathematical invariant: sum(variance_decomposition) == 100.0% across 1,000 trials."""

    def test_monte_carlo_1000_trials_strict_100_sum(self):
        rng = np.random.default_rng(888)
        failures = []

        for trial in range(1000):
            n = rng.integers(60, 250)
            dates = pd.date_range("2023-01-01", periods=n, freq="B")
            mkt = rng.normal(0, 0.015, n)
            smb = rng.normal(0, 0.008, n)
            hml = rng.normal(0, 0.007, n)
            wml = rng.normal(0, 0.009, n)

            # Randomly inject collinearity in 20% of trials
            if rng.random() < 0.20:
                collin_type = rng.integers(0, 3)
                if collin_type == 0:
                    smb = mkt * 1.5
                elif collin_type == 1:
                    wml = 0.4 * mkt + 0.6 * hml
                else:
                    smb = np.zeros(n)

            factors = pd.DataFrame({"mkt_excess": mkt, "smb": smb, "hml": hml, "wml": wml}, index=dates)

            b_m = rng.uniform(-2.0, 2.5)
            b_s = rng.uniform(-1.5, 1.5)
            b_h = rng.uniform(-1.5, 1.5)
            b_w = rng.uniform(-1.0, 1.0)
            noise = rng.normal(0, rng.uniform(0.0001, 0.03), n)

            fund = pd.Series(b_m * mkt + b_s * smb + b_h * hml + b_w * wml + noise, index=dates)

            res = compute_multivariate_factor_attribution(fund, factors)
            decomp = res["variance_decomposition"]
            total_sum = sum(decomp.values())
            dev = abs(total_sum - 100.0)

            if not math.isclose(total_sum, 100.0, abs_tol=1e-2):
                failures.append((trial, n, total_sum, dev))

        assert len(failures) == 0, f"Found {len(failures)} trials violating 100% sum! Examples: {failures[:5]}"
