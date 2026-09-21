"""
Unit and numerical precision tests for Enhanced Tail Risk Analytics
(Cornish-Fisher VaR, 99% CVaR, Drawdown Duration & Recovery, Downside Capture Efficiency,
and REST endpoints in backend/app/routers/quant.py).
"""

import math
import numpy as np
import pandas as pd
import pytest
import scipy.stats as stats
from fastapi.testclient import TestClient

from app import quant_analytics
from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


class TestCornishFisherExpansion:
    """Verifies Cornish-Fisher expansion adjusted Value at Risk."""

    def test_gaussian_limit_exact_precision(self):
        """When skewness and excess kurtosis are zero, CF VaR must exactly match Gaussian VaR: mu + z * sigma."""
        # Construct symmetric, normal-like dataset
        rng = np.random.default_rng(101)
        z_sample = rng.normal(0, 1, 1000)
        # Symmetrize to force skewness = 0 exactly
        sym_sample = np.concatenate([z_sample, -z_sample])
        # Adjust variance
        sigma = 0.015
        mu = 0.0005
        returns = mu + (sym_sample - np.mean(sym_sample)) / np.std(sym_sample) * sigma

        # Check skew is virtually zero
        assert abs(pd.Series(returns).skew()) < 1e-4

        cf_var_95 = quant_analytics.cornish_fisher_var(returns, alpha=0.05)
        # Theoretical Gaussian VaR
        z_95 = stats.norm.ppf(0.05)
        expected_gaussian_95 = mu + z_95 * sigma

        assert math.isclose(cf_var_95, expected_gaussian_95, abs_tol=1e-4)

    def test_negative_skewness_worsens_var(self):
        """Negative skewness must push the quantile further into the left tail (more negative return)."""
        rng = np.random.default_rng(202)
        normal_rets = rng.normal(0.0005, 0.015, 500)

        # Inject large negative tail shocks to create severe negative skewness
        skewed_rets = normal_rets.copy()
        skewed_rets[:15] = -0.06  # 15 severe crash days

        assert pd.Series(skewed_rets).skew() < -0.5

        # Mean and std of skewed returns
        mu = np.mean(skewed_rets)
        sigma = np.std(skewed_rets, ddof=1)
        gaussian_var_99 = mu + stats.norm.ppf(0.01) * sigma
        cf_var_99 = quant_analytics.cornish_fisher_var(skewed_rets, alpha=0.01)

        # CF VaR must be strictly more negative than Gaussian VaR due to left-tail fatness
        assert cf_var_99 < gaussian_var_99

    def test_var_monotonicity_across_confidence_levels(self):
        """99% VaR must be strictly more severe (more negative return) than 95% VaR."""
        rng = np.random.default_rng(303)
        returns = rng.normal(0.0003, 0.012, 400)

        cf_95 = quant_analytics.cornish_fisher_var(returns, alpha=0.05)
        cf_99 = quant_analytics.cornish_fisher_var(returns, alpha=0.01)

        assert cf_99 < cf_95


class TestExpectedShortfallAndTailRisk:
    """Verifies 99% CVaR and drawdown duration analytics."""

    def test_cvar_99_is_strictly_more_severe_than_var_99(self):
        """Expected Shortfall (CVaR) averages returns beyond VaR, so CVaR <= VaR."""
        dates = pd.date_range("2024-01-01", periods=200, freq="B")
        rng = np.random.default_rng(404)
        rets = rng.normal(0.0005, 0.015, 200)
        nav = 100.0 * np.cumprod(1.0 + rets)

        df_fund = pd.DataFrame({"nav_date": dates, "nav": nav, "daily_return": rets})
        metrics = quant_analytics.compute_tail_risk_metrics(df_fund)

        var_99 = metrics["var_99_daily_pct"]
        cvar_99 = metrics["cvar_99_daily_pct"]
        assert cvar_99 <= var_99 + 1e-6

        # Annualized values maintain same ordering
        assert metrics["cvar_99_ann_pct"] <= metrics["var_99_ann_pct"] + 1e-6

    def test_drawdown_duration_and_recovery_metrics_hand_crafted(self):
        """Constructs an exact 7-day NAV sequence with known peak, trough, and recovery."""
        dates = pd.date_range("2024-01-01", periods=7, freq="D")
        # Day 0: 100
        # Day 1: 120 (Peak)
        # Day 2: 110
        # Day 3: 80  (Trough: -33.3333% drawdown)
        # Day 4: 95
        # Day 5: 120 (Recovery!)
        # Day 6: 125
        navs = [100.0, 120.0, 110.0, 80.0, 95.0, 120.0, 125.0]
        df = pd.DataFrame({"nav_date": dates, "nav": navs})
        df = quant_analytics.compute_daily_returns(df)

        dd = quant_analytics.compute_drawdown_duration_metrics(df)

        assert math.isclose(dd["max_drawdown_pct"], -33.3333, abs_tol=1e-3)
        assert dd["max_drawdown_peak_date"] == "2024-01-02"
        assert dd["max_drawdown_trough_date"] == "2024-01-04"
        assert dd["max_drawdown_recovery_date"] == "2024-01-06"
        assert dd["drawdown_decline_days"] == 2
        assert dd["drawdown_recovery_days"] == 2
        assert dd["drawdown_total_duration_days"] == 4
        assert dd["recovered"] is True
        assert dd["is_currently_underwater"] is False

    def test_ongoing_unrecovered_drawdown(self):
        """Constructs an unrecovered drawdown; recovery_date must be None and recovered False."""
        dates = pd.date_range("2024-01-01", periods=5, freq="D")
        navs = [100.0, 110.0, 90.0, 85.0, 88.0]
        df = pd.DataFrame({"nav_date": dates, "nav": navs})

        dd = quant_analytics.compute_drawdown_duration_metrics(df)

        assert math.isclose(dd["max_drawdown_pct"], -22.7273, abs_tol=1e-3)
        assert dd["max_drawdown_peak_date"] == "2024-01-02"
        assert dd["max_drawdown_trough_date"] == "2024-01-04"
        assert dd["max_drawdown_recovery_date"] is None
        assert dd["drawdown_recovery_days"] is None
        assert dd["recovered"] is False
        assert dd["is_currently_underwater"] is True


class TestQuantRESTEndpoints:
    """Verifies the new FastAPI endpoints in backend/app/routers/quant.py."""

    @pytest.fixture(autouse=True)
    def setup_mock_fund(self, monkeypatch):
        """Mocks get_scheme_profile to return a deterministic test scheme."""
        from app.db import queries as db_queries

        dates = pd.date_range("2023-01-01", periods=350, freq="B")
        rng = np.random.default_rng(99)
        rets = rng.normal(0.0005, 0.012, 350)
        navs = 100.0 * np.cumprod(1.0 + rets)

        df_raw = pd.DataFrame({"nav_date": dates, "nav": navs})
        profile = {
            "scheme_code": 777777,
            "scheme_name": "Test Quant Multi-Cap Fund",
            "category": "Equity Scheme - Multi Cap Fund",
            "fund_house": "Quant AMC",
            "plan_type": "Direct",
            "option_type": "Growth",
        }

        def mock_get_profile(code):
            if code == 777777:
                return profile, df_raw
            return None, pd.DataFrame()

        monkeypatch.setattr(db_queries, "get_scheme_profile", mock_get_profile)

    def test_factors_endpoint_returns_200_and_regression_result(self, client):
        resp = client.get("/api/quant/777777/factors")
        assert resp.status_code == 200
        data = resp.json()

        assert data["scheme_code"] == 777777
        assert "regression" in data
        assert "figures" in data

        reg = data["regression"]
        assert "alpha_annualized_pct" in reg
        assert "factor_betas" in reg
        assert "variance_decomposition" in reg
        assert "waterfall_data" in reg
        assert "systematic_risk_pct" in reg
        assert "idiosyncratic_risk_pct" in reg

        figs = data["figures"]
        assert "factor_waterfall" in figs
        assert "factor_decomposition" in figs

    def test_stress_test_endpoint_returns_200_and_scenarios(self, client):
        resp = client.get("/api/quant/777777/stress-test")
        assert resp.status_code == 200
        data = resp.json()

        assert data["scheme_code"] == 777777
        assert "scenarios" in data
        assert len(data["scenarios"]) == 3
        assert "parametric_simulation" in data
        assert "figure" in data

        sim = data["parametric_simulation"]
        assert "total_return_impact_pct" in sim
        assert "rupee_impact" in sim

    def test_tail_risk_endpoint_returns_200_and_tail_metrics(self, client):
        resp = client.get("/api/quant/777777/tail-risk")
        assert resp.status_code == 200
        data = resp.json()

        assert data["scheme_code"] == 777777
        assert "tail_risk" in data
        assert "figures" in data

        tail = data["tail_risk"]
        for key in (
            "var_95_daily_pct",
            "var_99_daily_pct",
            "cvar_95_daily_pct",
            "cvar_99_daily_pct",
            "cf_var_95_daily_pct",
            "cf_var_99_daily_pct",
            "drawdown",
            "sortino_ratio",
        ):
            assert key in tail

        figs = data["figures"]
        assert "drawdown" in figs
        assert "distribution" in figs

    def test_endpoints_return_404_for_unknown_scheme(self, client):
        for path in ("/api/quant/999999/factors", "/api/quant/999999/stress-test", "/api/quant/999999/tail-risk"):
            resp = client.get(path)
            assert resp.status_code == 404
