"""Tier 1: Feature Coverage (Features 1 - 4: Quantitative Core & Tail Risk).
Verifies Multi-Factor Decomposition, Macro Stress Testing, Cornish-Fisher VaR, and CVaR/Drawdown.
Each feature has >= 5 isolated test cases (Total: 20 tests).
"""

import math
import numpy as np
import pandas as pd
import pytest
import scipy.stats as stats

from tests.e2e.e2e_oracles import (
    get_factor_attribution_fn,
    get_stress_testing_fn,
    get_factor_shocks_fn,
    get_cornish_fisher_var_fn,
    get_cvar_and_drawdown_fn,
)


# =====================================================================
# Feature 1: Multi-Factor Risk Decomposition (5 tests)
# =====================================================================

class TestTier1Feature1MultiFactorAttribution:
    """Feature 1: 4-Factor OLS regression (Market, Size, Value, Momentum) calibrated to Indian equity."""

    def test_f1_4factor_ols_regression_recovery(self):
        """Verifies 4-factor OLS correctly recovers known true betas and alpha."""
        np.random.seed(42)
        n = 500
        mkt = np.random.normal(0.0005, 0.012, n)
        smb = np.random.normal(0.0001, 0.008, n)
        hml = np.random.normal(-0.0001, 0.007, n)
        wml = np.random.normal(0.0003, 0.009, n)

        true_alpha_daily = 0.0001  # ~2.52% p.a.
        true_b_mkt = 1.15
        true_b_smb = 0.35
        true_b_hml = -0.20
        true_b_wml = 0.25

        noise = np.random.normal(0, 0.002, n)
        rf_daily = 0.00025

        fund_excess = true_alpha_daily + true_b_mkt * mkt + true_b_smb * smb + true_b_hml * hml + true_b_wml * wml + noise
        fund_ret = pd.Series(fund_excess + rf_daily)
        factors_df = pd.DataFrame({"mkt_excess": mkt, "smb": smb, "hml": hml, "wml": wml})

        fn = get_factor_attribution_fn()
        res = fn(fund_ret, factors_df, rf_daily=rf_daily)

        # Support both 'market'/'mkt_excess', 'size'/'smb', 'value'/'hml', 'momentum'/'wml'
        betas = res["factor_betas"]
        b_mkt = betas.get("mkt_excess", betas.get("market"))
        b_smb = betas.get("smb", betas.get("size"))
        b_hml = betas.get("hml", betas.get("value"))
        b_wml = betas.get("wml", betas.get("momentum"))

        # Verify betas recovered within 0.05
        assert abs(b_mkt - true_b_mkt) < 0.05
        assert abs(b_smb - true_b_smb) < 0.05
        assert abs(b_hml - true_b_hml) < 0.05
        assert abs(b_wml - true_b_wml) < 0.05
        assert abs(res["alpha_annualized_pct"] - (true_alpha_daily * 252 * 100)) < 1.0

    def test_f1_tstats_and_pvalues(self):
        """Verifies t-stats and p-values exist for intercept and all 4 factors."""
        np.random.seed(101)
        n = 250
        factors_df = pd.DataFrame({
            "mkt_excess": np.random.normal(0.0004, 0.01, n),
            "smb": np.random.normal(0.0001, 0.007, n),
            "hml": np.random.normal(0.0001, 0.007, n),
            "wml": np.random.normal(0.0002, 0.008, n),
        })
        fund_ret = pd.Series(factors_df["mkt_excess"] * 1.2 + 0.00025 + np.random.normal(0, 0.003, n))

        fn = get_factor_attribution_fn()
        res = fn(fund_ret, factors_df, rf_daily=0.00025)

        t_stats = res["t_stats"]
        p_values = res["p_values"]

        assert "alpha" in t_stats and "alpha" in p_values
        assert ("market" in t_stats or "mkt_excess" in t_stats)
        assert ("size" in t_stats or "smb" in t_stats)
        assert ("value" in t_stats or "hml" in t_stats)
        assert ("momentum" in t_stats or "wml" in t_stats)

        for key, pval in p_values.items():
            assert 0.0 <= pval <= 1.0, f"p-value out of bounds for {key}: {pval}"

    def test_f1_rsquared_and_adjusted_rsquared(self):
        """Verifies R² is bounded in [0, 1] and Adj R² <= R²."""
        np.random.seed(202)
        n = 300
        factors_df = pd.DataFrame({
            "mkt_excess": np.random.normal(0.0005, 0.01, n),
            "smb": np.random.normal(0.0, 0.006, n),
            "hml": np.random.normal(0.0, 0.006, n),
            "wml": np.random.normal(0.0, 0.006, n),
        })
        fund_ret = pd.Series(factors_df["mkt_excess"] * 1.0 + 0.00025 + np.random.normal(0, 0.005, n))

        fn = get_factor_attribution_fn()
        res = fn(fund_ret, factors_df, rf_daily=0.00025)

        assert 0.0 <= res["r_squared"] <= 1.0
        assert res["adj_r_squared"] <= res["r_squared"] + 1e-6

    def test_f1_systematic_vs_idiosyncratic_variance_decomposition(self):
        """Verifies systematic variance decomposition and idiosyncratic risk percentages."""
        np.random.seed(303)
        n = 400
        factors_df = pd.DataFrame({
            "mkt_excess": np.random.normal(0.0005, 0.01, n),
            "smb": np.random.normal(0.0, 0.006, n),
            "hml": np.random.normal(0.0, 0.006, n),
            "wml": np.random.normal(0.0, 0.006, n),
        })
        fund_ret = pd.Series(factors_df["mkt_excess"] * 0.9 + 0.00025 + np.random.normal(0, 0.004, n))

        fn = get_factor_attribution_fn()
        res = fn(fund_ret, factors_df, rf_daily=0.00025)

        assert 0.0 <= res["idiosyncratic_risk_pct"] <= 100.0
        # Sum of factor variance decomposition should be close to 100% of systematic variance
        sum_decomp = sum(res["variance_decomposition"].values())
        assert 98.0 <= sum_decomp <= 102.0 or sum_decomp == 0.0

    def test_f1_waterfall_components_structure(self):
        """Verifies waterfall data structure conforms to Plotly waterfall schema."""
        np.random.seed(404)
        n = 100
        factors_df = pd.DataFrame({
            "mkt_excess": np.random.normal(0.0005, 0.01, n),
            "smb": np.random.normal(0.0001, 0.006, n),
            "hml": np.random.normal(0.0001, 0.006, n),
            "wml": np.random.normal(0.0001, 0.006, n),
        })
        fund_ret = pd.Series(factors_df["mkt_excess"] * 1.05 + 0.00025)

        fn = get_factor_attribution_fn()
        res = fn(fund_ret, factors_df, rf_daily=0.00025)

        wf = res["waterfall_data"]
        labels = wf.get("x") or wf.get("labels")
        values = wf.get("y") or wf.get("values")
        measures = wf.get("measure") or wf.get("measures")

        assert labels is not None and values is not None and measures is not None
        assert len(labels) == len(values) == len(measures)
        assert measures[-1] == "total"
        assert any("Alpha" in str(lbl) for lbl in labels)



# =====================================================================
# Feature 2: Macro Scenario Stress Testing (5 tests)
# =====================================================================

class TestTier1Feature2MacroStressTesting:
    """Feature 2: Replay fund/portfolio drawdowns against historical shock windows and parametric shocks."""

    def test_f2_historical_replay_covid_march_2020(self):
        """Verifies COVID-19 historical crash replay captures deep drawdown and recovery."""
        dates = pd.date_range("2020-02-15", "2020-04-15", freq="D")
        # Simulate severe drop to March 23 then partial recovery
        days = len(dates)
        trough_day = 37  # March 23
        nav_curve = []
        val = 100.0
        for i in range(days):
            if i <= trough_day:
                val *= 0.988  # drop ~35%
            else:
                val *= 1.015  # rebound
            nav_curve.append(val)

        nav_series = pd.Series(nav_curve, index=dates)
        fn = get_stress_testing_fn()
        res = fn(nav_series)

        assert "covid_march_2020" in res or "covid_2020" in res
        covid = res.get("covid_march_2020") or res.get("covid_2020")
        dd = covid.get("drawdown_pct", covid.get("max_drawdown_pct"))
        bench_dd = covid.get("benchmark_mdd_pct", covid.get("benchmark_drawdown_pct"))

        assert dd is not None and dd < -20.0
        assert bench_dd is not None and bench_dd < -10.0

    def test_f2_historical_replay_rate_hike_2022(self):
        """Verifies 2022 rate hike scenario evaluates multi-month drawdown."""
        dates = pd.date_range("2022-01-03", "2022-06-20", freq="D")
        t = np.linspace(0, 1, len(dates))
        navs = 100.0 * (1.0 - 0.15 * np.sin(np.pi * t))
        nav_series = pd.Series(navs, index=dates)

        fn = get_stress_testing_fn()
        res = fn(nav_series)

        assert "rate_hike_2022" in res or "inflation_2022" in res
        rate_shock = res.get("rate_hike_2022") or res.get("inflation_2022")
        dd = rate_shock.get("drawdown_pct", rate_shock.get("max_drawdown_pct"))

        assert dd is not None and -25.0 <= dd <= -5.0

    def test_f2_historical_replay_volatility_spike_2024(self):
        """Verifies Lok Sabha election volatility spike window evaluates acute drop."""
        dates = pd.date_range("2024-05-23", "2024-06-04", freq="D")
        navs = [100.0, 100.5, 101.0, 101.2, 102.0, 102.5, 103.0, 103.2, 103.5, 104.0, 104.5, 96.0, 99.0]
        nav_series = pd.Series(navs[: len(dates)], index=dates)

        fn = get_stress_testing_fn()
        res = fn(nav_series)

        assert "volatility_spike_2024" in res or "volatility_2024" in res
        spike = res.get("volatility_spike_2024") or res.get("volatility_2024")
        dd = spike.get("drawdown_pct", spike.get("max_drawdown_pct"))

        assert dd is not None and dd < -3.0

    def test_f2_parametric_factor_shock_simulation(self):
        """Verifies linear factor perturbation calculation: Delta P = sum(beta_k * Delta F_k)."""
        betas = {"market": 1.20, "smb": 0.40, "hml": -0.25, "wml": 0.10}
        shocks = {"market": -0.15, "smb": -0.05, "hml": 0.02, "wml": -0.08}

        expected_impact = (1.20 * -0.15) + (0.40 * -0.05) + (-0.25 * 0.02) + (0.10 * -0.08)
        expected_impact_pct = round(expected_impact * 100.0, 4)

        fn = get_factor_shocks_fn()
        sim = fn(betas, shocks)

        impact = sim.get("portfolio_impact_pct", sim.get("total_return_impact_pct"))
        assert impact is not None
        assert math.isclose(impact, expected_impact_pct, abs_tol=1e-3)
        mkt_imp = sim.get("market_impact_pct") or sim.get("factor_impacts_pct", {}).get("market")
        assert mkt_imp is not None
        assert math.isclose(mkt_imp, -18.0, abs_tol=1e-3)

    def test_f2_downside_beta_calculation(self):
        """Verifies downside beta isolates negative benchmark days."""
        dates = pd.date_range("2020-02-15", "2020-04-15", freq="D")
        n = len(dates)
        np.random.seed(505)
        bench_ret = np.random.normal(-0.005, 0.02, n)
        fund_ret = bench_ret * 1.3 + np.random.normal(0, 0.005, n)

        bench_nav = 100.0 * np.cumprod(1.0 + bench_ret)
        fund_nav = 100.0 * np.cumprod(1.0 + fund_ret)

        fn = get_stress_testing_fn()
        res = fn(pd.Series(fund_nav, index=dates), pd.Series(bench_nav, index=dates))

        covid = res.get("covid_march_2020") or res.get("covid_2020")
        downside_b = covid.get("downside_beta")
        assert downside_b is not None
        assert downside_b > 0.5


# =====================================================================
# Feature 3: Cornish-Fisher Tail Risk VaR (5 tests)
# =====================================================================

class TestTier1Feature3CornishFisherVaR:
    """Feature 3: 95% and 99% Value at Risk adjusted for skewness and excess kurtosis."""

    def test_f3_gaussian_limit_when_skew_and_kurt_zero(self):
        """When skew=0 and kurt=0, Cornish-Fisher VaR exactly matches Gaussian VaR."""
        np.random.seed(606)
        # Large sample Gaussian distribution
        returns = np.random.normal(0.0005, 0.01, 10000)

        fn = get_cornish_fisher_var_fn()
        res = fn(returns, confidence_levels=[0.95, 0.99])

        # For normal distribution, skew ~ 0, kurt ~ 0
        assert abs(res["skewness"]) < 0.1
        assert abs(res["kurtosis"]) < 0.1
        # Difference between CF and Gaussian should be negligible
        assert abs(res["var_95_conservative_premium_pct"]) < 0.05
        assert abs(res["var_99_conservative_premium_pct"]) < 0.10

    def test_f3_negative_skewness_increases_var(self):
        """Negative skewness creates a fat left tail, yielding higher CF VaR than Gaussian."""
        np.random.seed(707)
        # Construct negatively skewed distribution (exponential losses)
        base = np.random.normal(0.001, 0.008, 2000)
        crashes = -np.random.exponential(0.025, 100)
        returns = np.concatenate([base, crashes])

        fn = get_cornish_fisher_var_fn()
        res = fn(returns, confidence_levels=[0.95, 0.99])

        assert res["skewness"] < -0.3
        # Cornish-Fisher VaR must be strictly larger than Gaussian VaR
        assert res["var_95_cf_daily_pct"] > res["var_95_gaussian_daily_pct"]
        assert res["var_99_cf_daily_pct"] > res["var_99_gaussian_daily_pct"]

    def test_f3_excess_kurtosis_increases_tail_risk(self):
        """Excess kurtosis (leptokurtic tails) amplifies 99% VaR."""
        np.random.seed(808)
        # Student's t-distribution with df=4 (heavy tails, high kurtosis)
        returns = stats.t.rvs(df=4, loc=0.0005, scale=0.01, size=3000)

        fn = get_cornish_fisher_var_fn()
        res = fn(returns, confidence_levels=[0.99])

        assert res["kurtosis"] > 1.5  # Heavy excess kurtosis
        assert res["var_99_conservative_premium_pct"] > 0.0

    def test_f3_annualized_var_scaling(self):
        """Annualized VaR scales daily VaR by sqrt(252)."""
        np.random.seed(909)
        returns = np.random.normal(0.0004, 0.012, 1000)

        fn = get_cornish_fisher_var_fn()
        res = fn(returns, confidence_levels=[0.95])

        expected_ann = round(res["var_95_cf_daily_pct"] * math.sqrt(252.0), 4)
        assert math.isclose(res["var_95_cf_ann_pct"], expected_ann, abs_tol=1e-3)

    def test_f3_var_confidence_monotonicity(self):
        """99% VaR must be strictly greater than 95% VaR."""
        np.random.seed(1010)
        returns = np.random.normal(0.0005, 0.015, 1500)

        fn = get_cornish_fisher_var_fn()
        res = fn(returns, confidence_levels=[0.95, 0.99])

        assert res["var_99_cf_daily_pct"] > res["var_95_cf_daily_pct"]


# =====================================================================
# Feature 4: Expected Shortfall & Capture Analytics (5 tests)
# =====================================================================

class TestTier1Feature4ExpectedShortfallAndCapture:
    """Feature 4: 99% CVaR, drawdown duration & recovery, downside capture efficiency."""

    def test_f4_cvar_99_is_strictly_greater_than_or_equal_to_var_99(self):
        """Expected Shortfall (mean of losses in tail) must be >= 99% VaR."""
        np.random.seed(1111)
        returns = np.random.normal(0.0004, 0.012, 2000)

        fn = get_cvar_and_drawdown_fn()
        res = fn(returns, confidence_level=0.99)

        # Expressed as negative return: CVaR is further in the tail (more negative)
        assert res["cvar_99_daily_pct"] <= res["var_99_daily_pct"]

    def test_f4_drawdown_duration_and_recovery_tracking(self):
        """Calculates accurate peak, trough, drawdown duration, and recovery days."""
        dates = pd.date_range("2023-01-01", "2023-06-30", freq="D")
        # Peak at day 20, trough at day 50, recovered by day 90
        navs = [100.0]
        for i in range(1, len(dates)):
            if i <= 20:
                navs.append(navs[-1] * 1.005)  # rises to peak ~110.5
            elif i <= 50:
                navs.append(navs[-1] * 0.992)  # drops ~21% to trough
            elif i <= 90:
                navs.append(navs[-1] * 1.008)  # recovers to peak
            else:
                navs.append(navs[-1] * 1.001)

        nav_s = pd.Series(navs, index=dates)
        ret_s = nav_s.pct_change().dropna().values

        fn = get_cvar_and_drawdown_fn()
        res = fn(ret_s, nav_series=nav_s)

        dd = res["drawdown_metrics"]
        assert dd["max_drawdown_pct"] < -15.0
        assert dd["drawdown_duration_days"] == 30  # 50 - 20 days
        assert dd["recovery_duration_days"] is not None
        assert dd["recovery_duration_days"] > 0

    def test_f4_ongoing_drawdown_recovery_is_none(self):
        """When NAV does not recover by the end of the timeseries, recovery_duration_days is None."""
        dates = pd.date_range("2023-01-01", "2023-04-01", freq="D")
        navs = [100.0]
        for i in range(1, len(dates)):
            if i <= 30:
                navs.append(navs[-1] * 1.005)
            else:
                navs.append(navs[-1] * 0.995)  # Ongoing decline

        nav_s = pd.Series(navs, index=dates)
        ret_s = nav_s.pct_change().dropna().values

        fn = get_cvar_and_drawdown_fn()
        res = fn(ret_s, nav_series=nav_s)

        dd = res["drawdown_metrics"]
        assert dd["recovery_duration_days"] is None

    def test_f4_downside_capture_ratio_calculation(self):
        """Calculates downside capture ratio comparing fund compound return on down days."""
        np.random.seed(1212)
        n = 200
        bench = np.random.normal(0.0002, 0.015, n)
        # Fund falls half as much as benchmark on down days
        fund = np.where(bench < 0, bench * 0.5, bench * 1.1)

        fn = get_cvar_and_drawdown_fn()
        res = fn(fund, bench_returns=bench)

        cap = res["capture_metrics"]
        assert cap["downside_capture_ratio"] < 80.0  # Defensive fund

    def test_f4_capture_efficiency_ratio(self):
        """Capture efficiency (upside capture / downside capture) > 1.0 identifies superior funds."""
        np.random.seed(1313)
        n = 250
        bench = np.random.normal(0.0003, 0.012, n)
        fund = np.where(bench < 0, bench * 0.6, bench * 1.2)

        fn = get_cvar_and_drawdown_fn()
        res = fn(fund, bench_returns=bench)

        cap = res["capture_metrics"]
        assert cap["upside_capture_ratio"] > cap["downside_capture_ratio"]
        assert cap["capture_efficiency"] > 1.0
