"""
Adversarial Empirical Stress Tests - Milestone 1 (Challenger 2)
Focus: Tail Risk, Cornish-Fisher Expansion, Coherence Axioms, Drawdown Duration, Downside Capture Efficiency.

Author: Challenger 2 (critic, specialist)
Project: Indian Mutual Funds Platform Revamp
Target: backend/app/quant_analytics.py
"""

import math
import numpy as np
import pandas as pd
import pytest
import scipy.stats as stats
from app import quant_analytics


class TestAdversarialCornishFisherVaR:
    """
    Adversarially challenges Cornish-Fisher Value at Risk implementation:
    - Skewness impact: negative skewness (left fat tail) vs positive skewness (right tail)
    - Excess kurtosis impact: fat-tailed leptokurtic behavior vs Gaussian
    - Monotonicity across confidence levels (95% vs 99%)
    - Degenerate inputs: zero variance, identical returns, minimal sample sizes (N=3, N=4)
    """

    def test_severe_negative_skewness_vs_positive_skewness(self):
        """
        Adversarial test:
        Under severe negative skewness (crash-prone left tail), the return quantile must be
        substantially more negative (greater loss) than under positive skewness (jump-prone right tail),
        even when mean and standard deviation are matched.
        """
        rng = np.random.default_rng(701)
        n = 3000
        base_sigma = 0.012

        # 1. Left-tail crash distribution (negative skewness)
        # 96% normal days, 4% severe crash days (-5% to -8%)
        crash_shocks = rng.uniform(-0.08, -0.04, size=int(n * 0.04))
        normal_days = rng.normal(0.001, base_sigma, size=n - len(crash_shocks))
        crash_rets = np.concatenate([normal_days, crash_shocks])

        # 2. Right-tail jump distribution (positive skewness)
        # 96% normal days, 4% severe jump days (+4% to +8%)
        jump_shocks = rng.uniform(0.04, 0.08, size=int(n * 0.04))
        normal_days_j = rng.normal(-0.001, base_sigma, size=n - len(jump_shocks))
        jump_rets = np.concatenate([normal_days_j, jump_shocks])

        skew_crash = pd.Series(crash_rets).skew()
        skew_jump = pd.Series(jump_rets).skew()

        assert skew_crash < -1.0, f"Expected severe negative skew, got {skew_crash}"
        assert skew_jump > 1.0, f"Expected severe positive skew, got {skew_jump}"

        cf_var_99_crash = quant_analytics.cornish_fisher_var(crash_rets, alpha=0.01)
        cf_var_99_jump = quant_analytics.cornish_fisher_var(jump_rets, alpha=0.01)

        # In return space, crash quantile must be significantly more negative than jump quantile
        assert cf_var_99_crash < cf_var_99_jump
        # In loss space: crash loss must exceed jump loss
        assert abs(cf_var_99_crash) > abs(cf_var_99_jump)

        # At 95% confidence level, the same ordering must hold
        cf_var_95_crash = quant_analytics.cornish_fisher_var(crash_rets, alpha=0.05)
        cf_var_95_jump = quant_analytics.cornish_fisher_var(jump_rets, alpha=0.05)
        assert cf_var_95_crash < cf_var_95_jump

    def test_cf_var_exceeds_gaussian_var_with_fat_tails(self):
        """
        Adversarial test:
        For leptokurtic return distributions (fat tails, excess kurtosis > 0),
        the Cornish-Fisher 99% VaR loss must strictly exceed the standard Gaussian 99% VaR loss.
        """
        rng = np.random.default_rng(702)
        n = 5000

        # Student-t with df=4 has heavy tails and high excess kurtosis
        df_val = 4
        raw_t = rng.standard_t(df=df_val, size=n)
        scale = 0.015 / np.sqrt(df_val / (df_val - 2))
        rets_t = raw_t * scale

        kurt = pd.Series(rets_t).kurtosis()
        assert kurt > 2.0, f"Expected high excess kurtosis, got {kurt}"

        mu = float(np.mean(rets_t))
        sigma = float(np.std(rets_t, ddof=1))
        gaussian_var_99 = mu + stats.norm.ppf(0.01) * sigma
        cf_var_99 = quant_analytics.cornish_fisher_var(rets_t, alpha=0.01)

        # In return space: CF VaR is more negative than Gaussian
        assert cf_var_99 < gaussian_var_99
        # In loss space: CF VaR loss exceeds Gaussian VaR loss
        assert abs(cf_var_99) > abs(gaussian_var_99)

        # Also test with a crash-mixture leptokurtic distribution
        crash_rets = np.concatenate([
            rng.normal(0.0005, 0.01, int(n * 0.95)),
            rng.normal(-0.05, 0.02, int(n * 0.05)),
        ])
        mu_c = float(np.mean(crash_rets))
        sigma_c = float(np.std(crash_rets, ddof=1))
        gauss_c_99 = mu_c + stats.norm.ppf(0.01) * sigma_c
        cf_c_99 = quant_analytics.cornish_fisher_var(crash_rets, alpha=0.01)

        assert cf_c_99 < gauss_c_99
        assert abs(cf_c_99) > abs(gauss_c_99)

    def test_cf_var_degenerate_and_minimal_samples(self):
        """
        Adversarial edge case:
        - Constant series (zero variance)
        - Extremely small series: N=0, 1, 2, 3, 4
        - Series with all identical values
        """
        # Zero variance / constant
        const_rets = np.array([0.005] * 20)
        assert math.isclose(quant_analytics.cornish_fisher_var(const_rets, alpha=0.01), 0.005, abs_tol=1e-6)

        # Empty and sub-threshold samples (< 3)
        assert quant_analytics.cornish_fisher_var(np.array([]), alpha=0.01) == 0.0
        assert quant_analytics.cornish_fisher_var(np.array([0.01]), alpha=0.01) == 0.0
        assert quant_analytics.cornish_fisher_var(np.array([0.01, -0.02]), alpha=0.01) == 0.0

        # Sample size N=3 (kurtosis is undefined/NaN in pandas; must gracefully fallback without NaN)
        rets_3 = np.array([0.01, -0.02, 0.015])
        res_3 = quant_analytics.cornish_fisher_var(rets_3, alpha=0.05)
        assert not np.isnan(res_3)
        assert isinstance(res_3, float)

        # Sample size N=4
        rets_4 = np.array([0.01, -0.02, 0.015, -0.005])
        res_4 = quant_analytics.cornish_fisher_var(rets_4, alpha=0.01)
        assert not np.isnan(res_4)
        assert isinstance(res_4, float)

    def test_cf_var_confidence_monotonicity(self):
        """
        Adversarial test:
        For any non-degenerate distribution, 99% VaR must represent a greater loss
        (more negative return) than 95% VaR.
        """
        rng = np.random.default_rng(703)
        for _ in range(10):
            rets = rng.normal(0.0005, 0.015, 250)
            cf_95 = quant_analytics.cornish_fisher_var(rets, alpha=0.05)
            cf_99 = quant_analytics.cornish_fisher_var(rets, alpha=0.01)
            assert cf_99 < cf_95


class TestAdversarialCoherenceVerification:
    """
    Empirically verifies the coherent risk measure axiom for Conditional Value at Risk:
    In return space:
      CVaR_99 <= VaR_99 (return quantile)
    In loss space:
      Loss(CVaR_99) >= Loss(VaR_99)
    Tested across diverse distributional geometries.
    """

    @pytest.mark.parametrize("dist_name, params", [
        ("gaussian", {"loc": 0.0005, "scale": 0.012}),
        ("heavy_t", {"df": 3, "scale": 0.01}),
        ("bimodal_crash", {"mix_ratio": 0.08, "crash_mean": -0.06}),
        ("step_discrete", {"levels": [-0.04, -0.01, 0.0, 0.01, 0.03]}),
        ("uniform", {"low": -0.05, "high": 0.05}),
    ])
    def test_cvar_le_var_across_distribution_geometries(self, dist_name, params):
        rng = np.random.default_rng(801)
        n = 1000

        if dist_name == "gaussian":
            rets = rng.normal(params["loc"], params["scale"], n)
        elif dist_name == "heavy_t":
            rets = rng.standard_t(params["df"], n) * params["scale"]
        elif dist_name == "bimodal_crash":
            normal_part = rng.normal(0.001, 0.01, int(n * (1.0 - params["mix_ratio"])))
            crash_part = rng.normal(params["crash_mean"], 0.01, int(n * params["mix_ratio"]))
            rets = np.concatenate([normal_part, crash_part])
        elif dist_name == "step_discrete":
            rets = rng.choice(params["levels"], size=n)
        elif dist_name == "uniform":
            rets = rng.uniform(params["low"], params["high"], size=n)

        dates = pd.date_range("2023-01-01", periods=len(rets), freq="D")
        df_fund = pd.DataFrame({
            "nav_date": dates,
            "nav": 100.0 * np.cumprod(1.0 + rets),
            "daily_return": rets,
        })

        metrics = quant_analytics.compute_tail_risk_metrics(df_fund)

        v99 = metrics["var_99_daily_pct"]
        cv99 = metrics["cvar_99_daily_pct"]
        v95 = metrics["var_95_daily_pct"]
        cv95 = metrics["cvar_95_daily_pct"]

        # 1. Coherence in return space: CVaR <= VaR (more negative)
        assert cv99 <= v99 + 1e-5, f"Coherence violated at 99%: CVaR={cv99} > VaR={v99}"
        assert cv95 <= v95 + 1e-5, f"Coherence violated at 95%: CVaR={cv95} > VaR={v95}"

        # 2. In loss space: Expected Shortfall loss >= VaR loss
        loss_cvar_99 = -cv99
        loss_var_99 = -v99
        assert loss_cvar_99 >= loss_var_99 - 1e-5

        # 3. Confidence level monotonicity: 99% CVaR loss >= 95% CVaR loss
        assert cv99 <= cv95 + 1e-5

        # 4. Annualized metrics preserve identical relationship
        assert metrics["cvar_99_ann_pct"] <= metrics["var_99_ann_pct"] + 1e-5
        assert metrics["cvar_95_ann_pct"] <= metrics["var_95_ann_pct"] + 1e-5

    def test_cvar_on_small_samples_boundary(self):
        """Coherence must hold even on very small sample sizes (N=5, 10, 20)."""
        rng = np.random.default_rng(802)
        for n in [5, 7, 10, 15, 20]:
            rets = rng.normal(0.0005, 0.02, n)
            dates = pd.date_range("2024-01-01", periods=n, freq="D")
            df = pd.DataFrame({
                "nav_date": dates,
                "nav": 100.0 * np.cumprod(1.0 + rets),
                "daily_return": rets,
            })
            metrics = quant_analytics.compute_tail_risk_metrics(df)
            assert metrics["cvar_99_daily_pct"] <= metrics["var_99_daily_pct"] + 1e-5


class TestAdversarialDrawdownDuration:
    """
    Adversarially stress-tests compute_drawdown_duration_metrics:
    - Unrecovered series (fund drops and never recovers)
    - Monotonically declining series (straight drop)
    - Monotonically rising series (no drawdowns)
    - Flat / constant series
    - Multi-cycle drawdowns (recovered earlier MDD, currently in secondary drawdown)
    """

    def test_unrecovered_series_behavior(self):
        """
        Unrecovered series:
        Fund peaks on Day 2, crashes on Day 4, rebounds slightly on Day 6,
        but never reaches the peak again.
        Expected:
        - recovered == False
        - max_drawdown_recovery_date is None
        - drawdown_recovery_days is None
        - is_currently_underwater == True
        - current_underwater_days == days from peak to series end
        """
        dates = pd.date_range("2024-01-01", periods=7, freq="D")
        navs = [100.0, 110.0, 120.0, 90.0, 70.0, 85.0, 95.0]
        # Day 0: 100, Day 1: 110, Day 2: 120 (Peak)
        # Day 3: 90, Day 4: 70 (Trough: (70-120)/120 = -41.6667%)
        # Day 5: 85, Day 6: 95 (Never recovered to 120)
        df = pd.DataFrame({"nav_date": dates, "nav": navs})
        df = quant_analytics.compute_daily_returns(df)

        dd = quant_analytics.compute_drawdown_duration_metrics(df)

        assert math.isclose(dd["max_drawdown_pct"], -41.6667, abs_tol=1e-3)
        assert dd["max_drawdown_peak_date"] == "2024-01-03"
        assert dd["max_drawdown_trough_date"] == "2024-01-05"
        assert dd["max_drawdown_recovery_date"] is None
        assert dd["drawdown_recovery_days"] is None
        assert dd["recovered"] is False
        assert dd["is_currently_underwater"] is True
        assert dd["drawdown_decline_days"] == 2  # Jan 3 to Jan 5
        assert dd["drawdown_total_duration_days"] == 4  # Jan 3 to Jan 7
        assert dd["current_underwater_days"] == 4
        assert dd["longest_underwater_days"] == 4

    def test_monotonically_declining_series(self):
        """
        Monotonically declining series:
        Fund falls every single day from start to finish.
        Expected:
        - peak is day 0, trough is last day
        - recovered == False
        - recovery_date is None
        - decline_days == total calendar days
        - is_currently_underwater == True
        """
        dates = pd.date_range("2024-01-01", periods=6, freq="D")
        navs = [100.0, 95.0, 90.0, 85.0, 80.0, 75.0]
        df = pd.DataFrame({"nav_date": dates, "nav": navs})

        dd = quant_analytics.compute_drawdown_duration_metrics(df)

        assert math.isclose(dd["max_drawdown_pct"], -25.0, abs_tol=1e-3)
        assert dd["max_drawdown_peak_date"] == "2024-01-01"
        assert dd["max_drawdown_trough_date"] == "2024-01-06"
        assert dd["max_drawdown_recovery_date"] is None
        assert dd["drawdown_recovery_days"] is None
        assert dd["drawdown_decline_days"] == 5
        assert dd["drawdown_total_duration_days"] == 5
        assert dd["recovered"] is False
        assert dd["is_currently_underwater"] is True
        assert dd["longest_underwater_days"] == 5
        assert dd["current_underwater_days"] == 5

    def test_monotonically_rising_series(self):
        """
        Monotonically rising series:
        Fund increases every single day.
        Expected:
        - max_drawdown_pct == 0.0
        - drawdown_decline_days == 0
        - drawdown_recovery_days == 0
        - drawdown_total_duration_days == 0
        - recovered == True
        - is_currently_underwater == False
        - longest_underwater_days == 0
        - current_underwater_days == 0
        """
        dates = pd.date_range("2024-01-01", periods=6, freq="D")
        navs = [100.0, 102.0, 105.0, 108.0, 112.0, 115.0]
        df = pd.DataFrame({"nav_date": dates, "nav": navs})

        dd = quant_analytics.compute_drawdown_duration_metrics(df)

        assert dd["max_drawdown_pct"] == 0.0
        assert dd["drawdown_decline_days"] == 0
        assert dd["drawdown_recovery_days"] == 0
        assert dd["drawdown_total_duration_days"] == 0
        assert dd["recovered"] is True
        assert dd["is_currently_underwater"] is False
        assert dd["longest_underwater_days"] == 0
        assert dd["current_underwater_days"] == 0

    def test_flat_constant_series(self):
        """
        Flat constant series:
        Fund stays flat at 100.0 for every day.
        Must produce 0.0 drawdown, recovered True, and zero underwater duration.
        """
        dates = pd.date_range("2024-01-01", periods=5, freq="D")
        navs = [100.0, 100.0, 100.0, 100.0, 100.0]
        df = pd.DataFrame({"nav_date": dates, "nav": navs})

        dd = quant_analytics.compute_drawdown_duration_metrics(df)

        assert dd["max_drawdown_pct"] == 0.0
        assert dd["recovered"] is True
        assert dd["is_currently_underwater"] is False
        assert dd["longest_underwater_days"] == 0

    def test_multi_drawdown_recovered_vs_current_state(self):
        """
        Complex multi-drawdown scenario:
        - MDD occurred in cycle 1: peak 100 -> trough 50 (-50%) -> recovered to 100 -> new peak 120.
        - Cycle 2: drop from 120 to 108 (-10%), currently unrecovered.
        Verifies that:
        - max_drawdown_pct is -50.0%
        - recovered is True (for the MDD)
        - is_currently_underwater is True (due to Cycle 2)
        - longest_underwater_days reflects the 50% drawdown spell
        - current_underwater_days reflects the active spell
        """
        dates = pd.date_range("2024-01-01", periods=5, freq="D")
        navs = [100.0, 50.0, 100.0, 120.0, 108.0]
        df = pd.DataFrame({"nav_date": dates, "nav": navs})

        dd = quant_analytics.compute_drawdown_duration_metrics(df)

        assert math.isclose(dd["max_drawdown_pct"], -50.0, abs_tol=1e-3)
        assert dd["recovered"] is True
        assert dd["max_drawdown_recovery_date"] == "2024-01-03"
        assert dd["is_currently_underwater"] is True
        assert dd["longest_underwater_days"] == 2
        assert dd["current_underwater_days"] == 1


class TestAdversarialDownsideCaptureEfficiency:
    """
    Adversarially tests compute_benchmark_relative_metrics and downside capture:
    - Zero benchmark down days (benchmark only rises)
    - Zero fund down days (fund only rises on benchmark down days)
    - Zero fund return on benchmark down days (fund flat)
    - Both benchmark and fund having zero down days
    - Graceful handling of short or empty series
    """

    def test_zero_benchmark_down_days(self):
        """
        Adversarial edge case:
        Benchmark experiences strictly positive returns on all trading days.
        Down capture calculation must not divide by zero or crash;
        must safely default to 100.0 down capture.
        """
        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        r_bench = np.array([0.01] * 10)  # Always positive
        r_fund = np.array([-0.005, 0.008] * 5)

        df_fund = pd.DataFrame({
            "nav_date": dates,
            "nav": 100.0 * np.cumprod(1.0 + r_fund),
            "daily_return": r_fund,
        })
        df_bench = pd.DataFrame({
            "nav_date": dates,
            "nav": 100.0 * np.cumprod(1.0 + r_bench),
            "daily_return": r_bench,
        })

        res = quant_analytics.compute_benchmark_relative_metrics(df_fund, df_bench)
        assert res["down_market_capture_pct"] == 100.0
        assert not np.isnan(res["up_market_capture_pct"])

        # Also via compute_tail_risk_metrics
        tail_res = quant_analytics.compute_tail_risk_metrics(df_fund, df_bench)
        bench_cap = tail_res["benchmark_capture"]
        assert bench_cap["down_market_capture_pct"] == 100.0
        assert bench_cap["downside_capture_efficiency"] is not None

    def test_zero_fund_down_days_during_benchmark_drawdown(self):
        """
        Adversarial edge case:
        Benchmark experiences negative returns (-1.0%), but the fund gains (+0.5%)
        on those exact same days.
        Expected:
        - down_market_capture_pct is negative (fund gained while benchmark lost)
        - capture_ratio and downside_capture_efficiency are computed without zero division
        """
        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        r_bench = np.array([-0.01, 0.01] * 5)
        r_fund = np.array([0.005] * 10)  # Fund always positive

        df_fund = pd.DataFrame({
            "nav_date": dates,
            "nav": 100.0 * np.cumprod(1.0 + r_fund),
            "daily_return": r_fund,
        })
        df_bench = pd.DataFrame({
            "nav_date": dates,
            "nav": 100.0 * np.cumprod(1.0 + r_bench),
            "daily_return": r_bench,
        })

        res = quant_analytics.compute_benchmark_relative_metrics(df_fund, df_bench)
        # Fund return > 0 while bench < 0 means down capture is negative
        assert res["down_market_capture_pct"] < 0

        tail_res = quant_analytics.compute_tail_risk_metrics(df_fund, df_bench)
        assert tail_res["benchmark_capture"]["downside_capture_efficiency"] is not None

    def test_fund_exactly_flat_on_benchmark_down_days(self):
        """
        Adversarial edge case:
        Fund return is exactly 0.0 on all benchmark down days.
        down_market_capture_pct will evaluate to 0.0.
        Division by zero must be protected:
        - capture_ratio must be None / NaN
        - downside_capture_efficiency must be None
        """
        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        r_bench = np.array([-0.01, 0.01] * 5)
        r_fund = np.array([0.0, 0.01] * 5)  # Exactly 0 on bench down days

        df_fund = pd.DataFrame({
            "nav_date": dates,
            "nav": 100.0 * np.cumprod(1.0 + r_fund),
            "daily_return": r_fund,
        })
        df_bench = pd.DataFrame({
            "nav_date": dates,
            "nav": 100.0 * np.cumprod(1.0 + r_bench),
            "daily_return": r_bench,
        })

        res = quant_analytics.compute_benchmark_relative_metrics(df_fund, df_bench)
        assert res["down_market_capture_pct"] == 0.0
        assert res["capture_ratio"] is None

        tail_res = quant_analytics.compute_tail_risk_metrics(df_fund, df_bench)
        assert tail_res["benchmark_capture"]["downside_capture_efficiency"] is None

    def test_both_benchmark_and_fund_only_positive(self):
        """
        Adversarial edge case:
        Neither benchmark nor fund ever decline (both strictly positive returns).
        System must handle gracefully with down capture 100.0 and valid DCE.
        """
        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        r_bench = np.array([0.01] * 10)
        r_fund = np.array([0.02] * 10)

        df_fund = pd.DataFrame({
            "nav_date": dates,
            "nav": 100.0 * np.cumprod(1.0 + r_fund),
            "daily_return": r_fund,
        })
        df_bench = pd.DataFrame({
            "nav_date": dates,
            "nav": 100.0 * np.cumprod(1.0 + r_bench),
            "daily_return": r_bench,
        })

        tail_res = quant_analytics.compute_tail_risk_metrics(df_fund, df_bench)
        assert tail_res["benchmark_capture"]["down_market_capture_pct"] == 100.0
        assert tail_res["benchmark_capture"]["downside_capture_efficiency"] is not None

    def test_empty_or_sub_threshold_benchmark(self):
        """
        Adversarial test:
        If benchmark dataframe has fewer than 5 overlapping days or is empty,
        compute_tail_risk_metrics must return an empty benchmark_capture dictionary
        without error.
        """
        dates = pd.date_range("2024-01-01", periods=3, freq="D")
        df_fund = pd.DataFrame({
            "nav_date": dates,
            "nav": [100.0, 101.0, 102.0],
            "daily_return": [0.0, 0.01, 0.01],
        })
        df_bench_short = pd.DataFrame({
            "nav_date": dates,
            "nav": [100.0, 100.5, 101.0],
            "daily_return": [0.0, 0.005, 0.005],
        })

        tail_res = quant_analytics.compute_tail_risk_metrics(df_fund, df_bench_short)
        assert tail_res["benchmark_capture"] == {}
