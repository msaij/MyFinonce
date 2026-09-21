"""Adversarial stress test harness for Milestone 5 Phase 1 mathematical parity & oracles.

Tests:
1. Attilio Meucci (2009) PCA orthogonal factor entropy:
   - 2 collinear assets (rho >= 0.98): ENCB == 1.0 < ENC == 2.0 in both production and oracle.
   - 5 perfectly uncorrelated assets: ENCB == 5.0 in both production and oracle.
   - Near-singular / rank-deficient covariance matrices.
   - Active eigenspace filtering on zero-weight assets.
2. Cornish-Fisher VaR and CVaR expansions:
   - Zero skewness and zero excess kurtosis matches Gaussian quantiles exactly (w == z).
   - Standard normal financial return sample matches Gaussian VaR within 1 basis point (< 0.02%).
   - Negative skewness and positive excess kurtosis matches Cornish-Fisher polynomial.
   - Conservative premium positivity under fat left tails.
   - Parity between production quant_analytics and e2e_oracles.
3. Downside beta and stress testing dates:
   - Date normalization across homogeneous series of diverse formats (datetime.date, pd.Timestamp,
     ISO strings, np.datetime64).
   - Cross-format merges (e.g. ISO string series joined with datetime.date series) succeed cleanly.
   - Inner merge stability without any try/except fallback.
"""

import datetime
import math
import numpy as np
import pandas as pd
import pytest
import scipy.stats as stats

from app.risk_budgeting import compute_risk_budgeting
from tests.e2e.e2e_oracles import oracle_compute_risk_budgeting
from app.quant_analytics import (
    compute_cornish_fisher_var,
    cornish_fisher_var,
    compute_expected_shortfall_and_capture,
)
from tests.e2e.e2e_oracles import (
    oracle_cornish_fisher_var,
    oracle_expected_shortfall_and_capture,
    get_stress_testing_fn,
    get_risk_budgeting_fn,
    get_cornish_fisher_var_fn,
    get_cvar_and_drawdown_fn,
)
from app.stress_testing import evaluate_historical_stress_scenarios, _normalize_date_series


class TestAdversarialMeucciPCAEntropy:
    """Stress tests for Attilio Meucci (2009) PCA orthogonal factor entropy."""

    @pytest.mark.parametrize("rho", [0.98, 0.99, 0.999, 0.9999])
    def test_two_collinear_assets_encb_equals_one(self, rho: float):
        """2 collinear assets with rho >= 0.98 must yield ENCB = 1.0 while ENC = 2.0.
        
        This distinguishes Meucci (2009) PCA orthogonal factor entropy from naive
        percentage risk contribution entropy, which erroneously yields ENCB = 2.0.
        """
        sigma = 0.02
        cov = np.array([
            [sigma**2, rho * sigma**2],
            [rho * sigma**2, sigma**2],
        ])
        weights = [0.5, 0.5]
        names = ["Collinear_A", "Collinear_B"]

        prod_res = compute_risk_budgeting(weights, cov, names)
        orc_res = oracle_compute_risk_budgeting(np.array(weights), cov, names)

        # 1. ENC must be 2.0 (equal weighting in 2 assets)
        assert prod_res["effective_number_of_constituents"] == 2.0
        assert orc_res["effective_number_of_constituents"] == 2.0

        # 2. ENCB must collapse to 1.0 (single dominant principal component)
        assert prod_res["effective_number_of_correlated_bets"] == 1.0
        assert orc_res["effective_number_of_correlated_bets"] == 1.0

        # 3. ENCB must be strictly less than ENC
        assert prod_res["effective_number_of_correlated_bets"] < prod_res["effective_number_of_constituents"]
        assert orc_res["effective_number_of_correlated_bets"] < orc_res["effective_number_of_constituents"]

    def test_five_uncorrelated_assets_encb_equals_five(self):
        """5 perfectly uncorrelated assets must yield ENCB approx 5.0 and ENC = 5.0."""
        n = 5
        cov = np.eye(n) * (0.015**2)
        weights = [1.0 / n] * n
        names = [f"Asset_{i}" for i in range(n)]

        prod_res = compute_risk_budgeting(weights, cov, names)
        orc_res = oracle_compute_risk_budgeting(np.array(weights), cov, names)

        assert prod_res["effective_number_of_constituents"] == 5.0
        assert orc_res["effective_number_of_constituents"] == 5.0

        # ENCB must be approximately 5.0 (within rounding of exp(ln(5)))
        assert math.isclose(prod_res["effective_number_of_correlated_bets"], 5.0, abs_tol=0.01)
        assert math.isclose(orc_res["effective_number_of_correlated_bets"], 5.0, abs_tol=0.01)
        assert math.isclose(
            prod_res["effective_number_of_correlated_bets"],
            orc_res["effective_number_of_correlated_bets"],
            abs_tol=1e-4,
        )

    def test_near_singular_covariance_matrix(self):
        """Matrix with condition number > 10^8 (rank-deficient + tiny epsilon noise).
        
        Must not throw LinAlgError, must not produce NaN/Inf, and must bound ENCB in [1.0, N].
        """
        rng = np.random.RandomState(42)
        X_latent = rng.randn(200, 2)
        # Assets 3, 4, 5 are exact linear combinations of 1 and 2 with 1e-11 noise
        X_full = np.column_stack([
            X_latent[:, 0],
            X_latent[:, 1],
            2.0 * X_latent[:, 0] - X_latent[:, 1] + 1e-11 * rng.randn(200),
            -X_latent[:, 0] + 3.0 * X_latent[:, 1] + 1e-11 * rng.randn(200),
            0.5 * X_latent[:, 0] + 0.5 * X_latent[:, 1] + 1e-11 * rng.randn(200),
        ])
        cov_singular = np.cov(X_full, rowvar=False)
        cond = np.linalg.cond(cov_singular)
        assert cond > 1e8, f"Condition number was only {cond}"

        weights = [0.2] * 5
        names = [f"Singular_{i}" for i in range(5)]

        prod_res = compute_risk_budgeting(weights, cov_singular, names)
        orc_res = oracle_compute_risk_budgeting(np.array(weights), cov_singular, names)

        # Must not be NaN or Inf
        encb_prod = prod_res["effective_number_of_correlated_bets"]
        encb_orc = orc_res["effective_number_of_correlated_bets"]
        assert not math.isnan(encb_prod) and not math.isinf(encb_prod)
        assert not math.isnan(encb_orc) and not math.isinf(encb_orc)

        # Must be within theoretical bounds [1.0, 5.0]
        assert 1.0 <= encb_prod <= 5.0
        assert 1.0 <= encb_orc <= 5.0

        # Exact parity between production and oracle
        assert math.isclose(encb_prod, encb_orc, abs_tol=0.05)

    def test_single_active_asset_and_zero_weight_masking(self):
        """Zero-weight assets must be filtered from active eigenspace without corrupting ENCB."""
        cov = np.diag([0.04, 0.09, 0.16])
        weights = [1.0, 0.0, 0.0]
        names = ["Active", "Zero1", "Zero2"]

        prod_res = compute_risk_budgeting(weights, cov, names)
        orc_res = oracle_compute_risk_budgeting(np.array(weights), cov, names)

        assert prod_res["effective_number_of_constituents"] == 1.0
        assert prod_res["effective_number_of_correlated_bets"] == 1.0
        assert orc_res["effective_number_of_correlated_bets"] == 1.0
        assert prod_res["percentage_risk_contributions"]["Zero1"] == 0.0
        assert prod_res["percentage_risk_contributions"]["Zero2"] == 0.0


class TestAdversarialCornishFisherVaR:
    """Stress tests for Cornish-Fisher expansion Value at Risk and Expected Shortfall."""

    def test_standard_normal_zero_skew_zero_kurtosis_matches_gaussian(self):
        """When skewness=0 and excess kurtosis=0, Cornish-Fisher expansion polynomial:
          w = z + (z^2-1)S/6 + (z^3-3z)K/24 - (2z^3-5z)S^2/36
        reduces identically to w = z.
        """
        # 1. Exact polynomial verification for skew=0, kurt=0
        for alpha in [0.05, 0.01]:
            z = float(stats.norm.ppf(alpha))
            s = 0.0
            k = 0.0
            w = (
                z
                + ((z ** 2 - 1.0) * s) / 6.0
                + ((z ** 3 - 3.0 * z) * k) / 24.0
                - ((2.0 * z ** 3 - 5.0 * z) * (s ** 2)) / 36.0
            )
            assert w == z, f"Expected w == z for s=0, k=0, got w={w}, z={z}"

        # 2. Empirical test on a standard normal financial return sample (vol = 0.015)
        rng = np.random.RandomState(999)
        sample = rng.normal(0.0004, 0.015, 100000)
        sample = np.concatenate([sample, -sample + 2 * 0.0004])  # exact zero skewness

        res_prod = compute_cornish_fisher_var(sample, confidence_levels=[0.95, 0.99])
        # Conservative premium must be virtually 0.0% (< 1 basis point / 0.02%)
        assert math.isclose(res_prod["var_95_conservative_premium_pct"], 0.0, abs_tol=0.02)
        assert math.isclose(res_prod["var_99_conservative_premium_pct"], 0.0, abs_tol=0.02)

    def test_negative_skewness_and_excess_kurtosis_polynomial_matching(self):
        """Verify Cornish-Fisher polynomial on fat-tailed, negatively skewed distribution.
        
        Assert:
        1. w_alpha is more negative than z_alpha (fatter left tail).
        2. var_cf_daily > var_gaussian (conservative premium > 0).
        3. Exact parity between app.quant_analytics and tests.e2e.e2e_oracles.
        """
        rng = np.random.RandomState(1234)
        bulk = rng.normal(0.0005, 0.01, 2000)
        crashes = rng.normal(-0.04, 0.02, 100)
        returns = np.concatenate([bulk, crashes])

        prod_cf = compute_cornish_fisher_var(returns, confidence_levels=[0.95, 0.99])
        orc_cf = oracle_cornish_fisher_var(returns, confidence_levels=[0.95, 0.99])

        # Skewness must be strictly negative
        assert prod_cf["skewness"] < -0.5
        # Kurtosis must be strictly positive (leptokurtic)
        assert prod_cf["kurtosis"] > 1.0

        # Conservative premium must be positive: CF VaR > Gaussian VaR
        assert prod_cf["var_95_conservative_premium_pct"] > 0.0
        assert prod_cf["var_99_conservative_premium_pct"] > 0.0
        assert prod_cf["var_99_cf_daily_pct"] > prod_cf["var_95_cf_daily_pct"]

        # Production vs Oracle parity
        assert math.isclose(prod_cf["var_95_cf_daily_pct"], orc_cf["var_95_cf_daily_pct"], rel_tol=1e-2)
        assert math.isclose(prod_cf["var_99_cf_daily_pct"], orc_cf["var_99_cf_daily_pct"], rel_tol=1e-2)
        assert math.isclose(prod_cf["var_99_cf_ann_pct"], orc_cf["var_99_cf_ann_pct"], rel_tol=1e-2)

    def test_cvar_subsumes_var_and_drawdown_metrics(self):
        """Expected Shortfall (CVaR) must be at least as severe as VaR (cvar <= var in return terms)."""
        rng = np.random.RandomState(4321)
        returns = rng.standard_t(df=4, size=1000) * 0.01  # Heavy-tailed Student-t
        dates = pd.date_range("2022-01-01", periods=len(returns), freq="D")
        nav_series = pd.Series(100.0 * np.cumprod(1.0 + returns), index=dates)

        prod_cvar = compute_expected_shortfall_and_capture(returns, nav_series=nav_series, confidence_level=0.99)
        orc_cvar = oracle_expected_shortfall_and_capture(returns, nav_series=nav_series, confidence_level=0.99)

        # In return space: cvar_99 <= var_99 (loss magnitude is greater)
        assert prod_cvar["cvar_99_daily_pct"] <= prod_cvar["var_99_daily_pct"]
        assert orc_cvar["cvar_99_daily_pct"] <= orc_cvar["var_99_daily_pct"]

        # Parity
        assert math.isclose(prod_cvar["cvar_99_daily_pct"], orc_cvar["cvar_99_daily_pct"], rel_tol=1e-2)
        assert prod_cvar["drawdown_metrics"]["max_drawdown_pct"] < 0.0
        assert prod_cvar["drawdown_metrics"]["drawdown_duration_days"] > 0


class TestAdversarialDownsideBetaDateNormalization:
    """Stress tests verifying pd.to_datetime normalization and absence of try/except fallback."""

    def test_normalize_date_series_handles_heterogeneous_formats(self):
        """_normalize_date_series must convert diverse formats to uniform timezone-naive datetime64[ns]
        and allow seamless cross-format merges.
        """
        # Test series 1: ISO strings
        s_iso = _normalize_date_series(["2020-03-23", "2020-03-24"])
        assert str(s_iso.dtype) == "datetime64[ns]"

        # Test series 2: Naive Timestamps
        s_ts = _normalize_date_series([
            pd.Timestamp("2020-03-23"),
            pd.Timestamp("2020-03-24"),
        ])
        assert str(s_ts.dtype) == "datetime64[ns]"

        # Test series 3: python datetime.date objects
        s_date = _normalize_date_series([
            datetime.date(2020, 3, 23),
            datetime.date(2020, 3, 24),
        ])
        assert str(s_date.dtype) == "datetime64[ns]"

        # Test series 4: numpy datetime64
        s_np = _normalize_date_series([
            np.datetime64("2020-03-23"),
            np.datetime64("2020-03-24"),
        ])
        assert str(s_np.dtype) == "datetime64[ns]"

        # Verify cross-format inner merge between ISO string series and datetime.date series
        df1 = pd.DataFrame({"nav_date": s_iso, "r_fund": [0.01, -0.02]})
        df2 = pd.DataFrame({"nav_date": s_date, "r_bench": [-0.015, -0.025]})
        merged = pd.merge(df1, df2, on="nav_date", how="inner")
        assert len(merged) == 2
        assert list(merged["r_fund"]) == [0.01, -0.02]

    def test_stress_testing_downside_beta_with_mismatched_index_types(self):
        """Fund NAV series with string DatetimeIndex vs Benchmark DataFrame with datetime.date column.
        
        Must merge cleanly and compute downside beta without throwing or returning default fallback.
        """
        dates = pd.date_range("2020-01-01", "2020-04-30", freq="D")
        n = len(dates)

        # Create COVID crash simulation data
        rng = np.random.RandomState(777)
        bench_ret = rng.normal(-0.005, 0.02, n)
        fund_ret = 1.15 * bench_ret + rng.normal(0.0001, 0.005, n)

        # Fund as Series with ISO string index
        fund_nav = 100.0 * np.cumprod(1.0 + fund_ret)
        fund_series = pd.Series(fund_nav, index=[d.strftime("%Y-%m-%d") for d in dates])

        # Benchmark as DataFrame with python datetime.date objects in 'nav_date' column
        bench_nav = 1000.0 * np.cumprod(1.0 + bench_ret)
        bench_df = pd.DataFrame({
            "nav_date": [d.date() for d in dates],
            "nav": bench_nav,
        })

        # Directly call production evaluate_historical_stress_scenarios
        res = evaluate_historical_stress_scenarios(
            fund_series,
            benchmark_series=bench_df,
        )

        assert "covid_march_2020" in res
        covid = res["covid_march_2020"]
        assert covid["has_data"] is True
        assert covid["max_drawdown_pct"] < -10.0
        # Downside beta must be computed from the merged data (~1.15) rather than 1.0 default
        assert covid["downside_beta"] is not None
        assert math.isclose(covid["downside_beta"], 1.15, abs_tol=0.20)
        assert covid["downside_beta"] != 1.0  # Not stuck at base default

    def test_oracle_adapter_getters_have_no_try_except_masking(self):
        """Verify that get_*_fn adapters export production functions directly without fallback masking."""
        fn_rb = get_risk_budgeting_fn()
        fn_cf = get_cornish_fisher_var_fn()
        fn_cvar = get_cvar_and_drawdown_fn()
        fn_stress = get_stress_testing_fn()

        # Check that these are the true production functions
        assert fn_rb is compute_risk_budgeting
        assert fn_cf is compute_cornish_fisher_var
        assert fn_cvar is compute_expected_shortfall_and_capture
        assert callable(fn_stress)
