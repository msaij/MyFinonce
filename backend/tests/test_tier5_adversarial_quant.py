"""Tier 5 White-Box Adversarial Coverage Hardening Test Suite.

Comprehensive white-box mathematical stress tests, boundary condition verifications,
and adversarial test harnesses for Indian Mutual Funds Quant & Portfolio Optimization engines:
1. Degenerate & Zero-Variance Series (constant NAV, zero standard deviation, collinear factors).
2. High-Dimensional Collinearity & Attilio Meucci (2009) PCA factor entropy ENCB stability and bounds.
3. Cornish-Fisher VaR & CVaR expansions under extreme skewness (|S| > 3) and high excess kurtosis (K > 10).
4. Black-Litterman optimization with conflicting tactical views, zero-confidence view matrices (omega -> inf), and singular prior covariance.
5. Lopez de Prado HRP with single-asset and two-asset edge topologies, negative correlation matrices, and antithetic pairs.
6. Fee drag multi-horizon compounding with zero expense ratio, negative alpha, zero capital, and extreme 30-year horizons.
"""

from __future__ import annotations

import math
import numpy as np
import pandas as pd
import pytest

from app import factor_model
from app import quant_analytics
from app import portfolio_hrp
from app import portfolio_black_litterman
from app import risk_budgeting
from app import stress_testing
from app.services import fee_drag


# ==============================================================================
# 1. DEGENERATE & ZERO-VARIANCE BOUNDARY CONDITIONS
# ==============================================================================

class TestAdversarialDegenerateZeroVariance:
    """Stress tests engines against degenerate, zero-variance, and flat NAV series."""

    def test_quant_daily_returns_constant_nav(self):
        """Constant NAV series must yield zero daily returns and zero drawdown without errors."""
        dates = pd.date_range("2023-01-01", periods=60, freq="B")
        df_const = pd.DataFrame({"nav_date": dates, "nav": [100.0] * 60})
        df_ret = quant_analytics.compute_daily_returns(df_const)

        assert len(df_ret) == 60
        assert (df_ret["daily_return"].dropna() == 0.0).all()
        assert (df_ret["drawdown"].dropna() == 0.0).all()
        assert (df_ret["cum_return"].dropna() == 0.0).all()

    def test_quant_risk_adjusted_metrics_zero_variance(self):
        """Zero variance return series must produce zero volatility and handle division by zero gracefully."""
        dates = pd.date_range("2023-01-01", periods=60, freq="B")
        df_flat = pd.DataFrame({
            "nav_date": dates,
            "nav": [100.0] * 60,
            "daily_return": [0.0] * 60,
            "drawdown": [0.0] * 60,
        })
        res = quant_analytics.compute_risk_adjusted_metrics(df_flat)

        assert res["vol_daily_pct"] == 0.0
        assert res["vol_annualized_pct"] == 0.0
        assert res["sharpe_ratio"] == 0.0
        assert res["sortino_ratio"] == 0.0
        # Calmar should be None when there is no drawdown (mdd = 0)
        assert res["calmar_ratio"] is None
        assert res["max_drawdown_pct"] == 0.0
        assert not math.isnan(res["var_95_daily_pct"])
        assert not math.isnan(res["cvar_95_daily_pct"])

    def test_cornish_fisher_zero_variance_returns(self):
        """Cornish-Fisher VaR on zero-variance returns returns mean without division by zero or NaN."""
        zeros = np.zeros(100)
        var_cf = quant_analytics.cornish_fisher_var(zeros, alpha=0.05)
        assert var_cf == 0.0
        assert not math.isnan(var_cf)

        res_cf = quant_analytics.compute_cornish_fisher_var(zeros, confidence_levels=[0.95, 0.99])
        assert res_cf["var_95_cf_daily_pct"] == 0.0
        assert res_cf["var_99_cf_daily_pct"] == 0.0

    def test_drawdown_duration_monotonic_flat_nav(self):
        """Monotonic flat NAV series must return max_drawdown_pct=0.0 and recovered=True."""
        dates = pd.date_range("2023-01-01", periods=50, freq="B")
        df_flat = pd.DataFrame({"nav_date": dates, "nav": [50.0] * 50})
        dd = quant_analytics.compute_drawdown_duration_metrics(df_flat)

        assert dd["max_drawdown_pct"] == 0.0
        assert dd["recovered"] is True
        assert dd["drawdown_decline_days"] == 0
        assert dd["longest_underwater_days"] == 0
        assert dd["is_currently_underwater"] is False

    def test_quant_tail_risk_zero_variance_suite(self):
        """compute_tail_risk_metrics must complete on constant NAV series without throwing exceptions."""
        dates = pd.date_range("2023-01-01", periods=40, freq="B")
        df_flat = pd.DataFrame({
            "nav_date": dates,
            "nav": [100.0] * 40,
            "daily_return": [0.0] * 40,
            "drawdown": [0.0] * 40,
        })
        res = quant_analytics.compute_tail_risk_metrics(df_flat)
        assert res["cf_var_95_daily_pct"] == 0.0
        assert res["cf_var_99_daily_pct"] == 0.0
        assert res["drawdown"]["max_drawdown_pct"] == 0.0

    def test_factor_model_constant_nav_zero_returns(self):
        """Factor attribution on constant NAV fund returns handles zero total sum of squares gracefully."""
        dates = pd.date_range("2023-01-01", periods=80, freq="B")
        fund_ret = pd.Series(0.0, index=dates)
        rng = np.random.default_rng(42)
        factor_rets = pd.DataFrame({
            "mkt_excess": rng.normal(0.0005, 0.01, 80),
            "smb": rng.normal(0.0001, 0.005, 80),
            "hml": rng.normal(0.0001, 0.005, 80),
            "wml": rng.normal(0.0001, 0.005, 80),
        }, index=dates)

        res = factor_model.compute_multivariate_factor_attribution(fund_ret, factor_rets, rf_daily=0.00025)

        assert res["r_squared"] == 0.0
        assert res["adj_r_squared"] == 0.0
        # Alpha should be negative daily risk-free rate annualized: -0.00025 * 252 * 100 = -6.3%
        assert res["alpha_annualized_pct"] == pytest.approx(-6.3, abs=0.1)
        # Variance decomposition must strictly sum to 100.0%
        decomp_sum = sum(res["variance_decomposition"].values())
        assert math.isclose(decomp_sum, 100.0, abs_tol=1e-3)
        assert res["systematic_risk_pct"] == 0.0
        assert res["idiosyncratic_risk_pct"] == 100.0

    def test_factor_model_collinear_factor_matrix(self):
        """Collinear factor matrix triggers pseudo-inverse and recovers parameters without LinAlgError."""
        dates = pd.date_range("2023-01-01", periods=60, freq="B")
        rng = np.random.default_rng(123)
        col = rng.normal(0.0005, 0.01, 60)
        # SMB, HML, WML are exact linear multiples of mkt_excess (rank 1 factor space)
        factor_rets = pd.DataFrame({
            "mkt_excess": col,
            "smb": col * 1.5,
            "hml": col * -0.8,
            "wml": col * 2.0,
        }, index=dates)
        fund_ret = pd.Series(col * 1.2 + rng.normal(0, 0.002, 60), index=dates)

        res = factor_model.compute_multivariate_factor_attribution(fund_ret, factor_rets)

        assert not math.isnan(res["alpha_annualized_pct"])
        assert 0.0 <= res["r_squared"] <= 1.0
        assert math.isclose(sum(res["variance_decomposition"].values()), 100.0, abs_tol=1e-3)

    def test_macro_stress_constant_nav_crisis_replay(self):
        """Crisis replay on flat NAV series shows zero peak-to-trough drawdown and recovered=True."""
        dates = pd.date_range("2020-01-01", "2020-06-30", freq="B")
        df_fund = pd.DataFrame({"nav_date": dates, "nav": [100.0] * len(dates)})
        sc_cfg = stress_testing.HISTORICAL_SCENARIOS[0]  # COVID March 2020

        res = stress_testing._compute_single_scenario(df_fund, None, sc_cfg)

        assert res["has_data"] is True
        assert res["max_drawdown_pct"] == 0.0
        assert res["drawdown_duration_days"] == 0
        assert res["recovered"] is True
        assert res["peak_nav"] == 100.0
        assert res["trough_nav"] == 100.0


# ==============================================================================
# 2. HIGH-DIMENSIONAL COLLINEARITY & ATTILIO MEUCCI (2009) ENCB STABILITY
# ==============================================================================

class TestAdversarialHighDimensionalCollinearityMeucci:
    """Stress tests high-dimensional collinearity and Attilio Meucci PCA factor entropy bounds."""

    def test_meucci_encb_12_assets_collinear_bounds(self):
        """12 highly collinear assets (rho >= 0.99) collapse ENCB towards 1.0 bet while ENC remains 12.0."""
        n = 12
        rho = 0.99
        corr = np.full((n, n), rho)
        np.fill_diagonal(corr, 1.0)
        vols = np.linspace(0.12, 0.28, n)
        cov = corr * np.outer(vols, vols)
        weights = np.ones(n) / n
        asset_names = [f"Collinear_{i}" for i in range(n)]

        res = risk_budgeting.compute_risk_budgeting(weights, cov, asset_names=asset_names)

        enc = res["effective_number_of_constituents"]
        encb = res["effective_number_of_correlated_bets"]
        encb_inv_hhi = res["encb_inverse_hhi"]

        # Nominal constituents: 12 equal weights -> ENC = 12.0
        assert enc == pytest.approx(12.0, abs=1e-2)

        # Attilio Meucci (2009) theorem: Under severe collinearity, 1st principal component explains
        # almost all risk variance. Factor risk entropy collapses, so ENCB must be bounded in [1.0, 1.5]!
        assert 1.0 <= encb <= 1.5, f"Expected ENCB ~ 1.0 under rho=0.99 collinearity, got {encb}"
        assert 1.0 <= encb_inv_hhi <= 1.5

        # Strict upper and lower bounds: 1.0 <= ENCB <= N
        assert 1.0 <= encb <= float(n)

    def test_meucci_encb_12_orthogonal_uncorrelated_assets(self):
        """12 orthogonal uncorrelated assets (rho = 0.0) produce ENCB equal to nominal ENC (12.0)."""
        n = 12
        cov_diag = np.diag([0.04] * n)  # Identical variance, zero covariance
        weights = np.ones(n) / n

        res = risk_budgeting.compute_risk_budgeting(weights, cov_diag)

        enc = res["effective_number_of_constituents"]
        encb = res["effective_number_of_correlated_bets"]

        assert enc == pytest.approx(12.0, abs=1e-2)
        assert encb == pytest.approx(12.0, abs=0.1)

    def test_euler_risk_decomposition_under_high_collinearity(self):
        """Euler risk contribution exactness holds under near-singular 12-asset covariance matrix."""
        n = 12
        rng = np.random.default_rng(999)
        # Generate rank-2 covariance matrix to simulate severe low-rank collinearity
        factor_loadings = rng.normal(0, 1, size=(n, 2))
        cov_collinear = factor_loadings @ factor_loadings.T + 1e-5 * np.eye(n)
        weights = rng.dirichlet(np.ones(n))

        res = risk_budgeting.compute_risk_budgeting(weights, cov_collinear)

        port_vol = res["portfolio_volatility"]
        rc_sum = sum(res["risk_contributions"].values())
        prc_sum = sum(res["percentage_risk_contributions"].values())

        # Euler decomposition: sum(RC_i) = sigma_p
        assert rc_sum == pytest.approx(port_vol, rel=1e-3)
        # Euler decomposition: sum(PRC_i) = 100.0%
        assert prc_sum == pytest.approx(100.0, rel=1e-3)

    def test_hrp_12_collinear_assets_quasi_diag_bisection(self):
        """HRP tree clustering and recursive bisection on 12 collinear assets allocates weights without collapse."""
        n = 12
        corr = np.full((n, n), 0.995)
        np.fill_diagonal(corr, 1.0)
        vols = np.linspace(0.10, 0.22, n)
        cov = corr * np.outer(vols, vols)
        asset_names = [f"Asset_{i}" for i in range(n)]

        res = portfolio_hrp.optimize_hrp(cov, corr, asset_names=asset_names)

        weights = res["weights"]
        assert len(weights) == 12
        assert len(res["cluster_order"]) == 12
        # Weights must be non-negative and sum strictly to 1.000000
        assert all(w >= 0.0 for w in weights.values())
        assert sum(weights.values()) == pytest.approx(1.0, abs=1e-5)
        # No single asset collapses to 0 or explodes to 1.0 under HRP
        assert all(w > 0.01 for w in weights.values())


# ==============================================================================
# 3. CORNISH-FISHER VaR & CVaR EXPANSIONS UNDER EXTREME HIGHER MOMENTS
# ==============================================================================

class TestAdversarialCornishFisherExtremeMoments:
    """Stress tests Cornish-Fisher and CVaR expansions under extreme skewness and fat kurtosis."""

    def test_cornish_fisher_extreme_negative_skew_and_excess_kurtosis(self):
        """Extreme crash distribution (|S| > 3, K > 10) produces finite, conservative Cornish-Fisher VaR."""
        # Synthesize heavy left-tailed crash distribution (Black Monday / Flash Crash model)
        rng = np.random.default_rng(42)
        n = 1500
        rets = rng.normal(0.0008, 0.009, n)
        # Inject extreme negative tail crash shocks
        crash_indices = rng.choice(n, size=20, replace=False)
        rets[crash_indices] -= rng.exponential(0.15, size=20)

        ret_s = pd.Series(rets)
        skew = float(ret_s.skew())
        kurt = float(ret_s.kurtosis())

        assert skew < -3.0, f"Expected S < -3.0, got {skew}"
        assert kurt > 10.0, f"Expected K > 10.0, got {kurt}"

        cf_var_95 = quant_analytics.cornish_fisher_var(rets, alpha=0.05)
        cf_var_99 = quant_analytics.cornish_fisher_var(rets, alpha=0.01)

        # Must evaluate to valid finite floating point numbers
        assert not math.isnan(cf_var_95) and not math.isinf(cf_var_95)
        assert not math.isnan(cf_var_99) and not math.isinf(cf_var_99)

        # 99% CF VaR must be strictly more negative than 95% CF VaR (monotonic risk tail)
        assert cf_var_99 < cf_var_95

        res_cf = quant_analytics.compute_cornish_fisher_var(rets, confidence_levels=[0.95, 0.99])
        assert res_cf["skewness"] == pytest.approx(skew, abs=0.01)
        assert res_cf["kurtosis"] == pytest.approx(kurt, abs=0.01)
        # Conservative premium at 99% must be positive (CF VaR more conservative than Gaussian)
        assert res_cf["var_99_conservative_premium_pct"] > 0.0

    def test_cornish_fisher_extreme_positive_skew_and_kurtosis(self):
        """Extreme positive skewness (S > 3, K > 10) produces finite Cornish-Fisher VaR."""
        rng = np.random.default_rng(77)
        n = 1500
        rets = rng.normal(-0.0005, 0.01, n)
        # Inject extreme positive lottery upside shocks
        moon_indices = rng.choice(n, size=25, replace=False)
        rets[moon_indices] += rng.exponential(0.18, size=25)

        ret_s = pd.Series(rets)
        assert ret_s.skew() > 3.0
        assert ret_s.kurtosis() > 10.0

        res_cf = quant_analytics.compute_cornish_fisher_var(rets, confidence_levels=[0.95, 0.99])
        assert not math.isnan(res_cf["var_95_cf_daily_pct"])
        assert not math.isnan(res_cf["var_99_cf_daily_pct"])

    def test_expected_shortfall_monotonicity_under_fat_tails(self):
        """99% CVaR (Expected Shortfall) is strictly more severe than 99% VaR threshold under crash regimes."""
        rng = np.random.default_rng(2026)
        n = 1000
        rets = rng.normal(0.0004, 0.012, n)
        crash_idx = rng.choice(n, size=15, replace=False)
        rets[crash_idx] -= rng.exponential(0.10, size=15)

        res = quant_analytics.compute_expected_shortfall_and_capture(rets, confidence_level=0.99)

        cvar_99 = res["cvar_99_daily_pct"]
        var_99 = res["var_99_daily_pct"]

        # CVaR is the expectation beyond VaR, so in signed terms CVaR <= VaR (more negative)
        assert cvar_99 <= var_99, f"Expected CVaR_99 ({cvar_99}) <= VaR_99 ({var_99})"
        assert not math.isnan(cvar_99) and not math.isinf(cvar_99)


# ==============================================================================
# 4. BLACK-LITTERMAN ROBUSTNESS: CONFLICTING VIEWS, ZERO CONFIDENCE, SINGULAR PRIOR
# ==============================================================================

class TestAdversarialBlackLittermanRobustness:
    """Stress tests Black-Litterman Bayesian allocation under adversarial tactical views and singular priors."""

    def test_black_litterman_conflicting_tactical_views(self):
        """Diametrically opposing views on the same asset pair cancel out symmetrically without LinAlgError."""
        cov = np.array([
            [0.04, 0.01, 0.01],
            [0.01, 0.04, 0.01],
            [0.01, 0.01, 0.04]
        ])
        w_mkt = np.array([0.40, 0.30, 0.30])
        # Conflicting views: View 1 claims A0 > A1 by +10%, View 2 claims A0 < A1 by -10%
        P = np.array([
            [1.0, -1.0, 0.0],
            [1.0, -1.0, 0.0],
        ])
        Q = np.array([0.10, -0.10])

        res = portfolio_black_litterman.optimize_black_litterman(cov, w_mkt, P, Q)

        weights = res["optimal_weights"]
        assert len(weights) == 3
        assert sum(weights) == pytest.approx(1.0, abs=1e-5)
        assert all(w >= 0.0 for w in weights)
        # Because views symmetrically cancel (+10% and -10%), weights must remain close to market prior
        assert weights[0] == pytest.approx(w_mkt[0], abs=0.05)
        assert weights[1] == pytest.approx(w_mkt[1], abs=0.05)

    def test_black_litterman_zero_confidence_asymptotic_convergence(self):
        """Zero confidence views (omega -> inf) cause posterior weights to converge to market equilibrium prior."""
        cov = np.array([
            [0.05, 0.01, 0.02],
            [0.01, 0.04, 0.01],
            [0.02, 0.01, 0.06]
        ])
        w_mkt = np.array([0.50, 0.25, 0.25])
        # Aggressive tactical view: Asset 0 return = 80.0%
        P = np.array([[1.0, 0.0, 0.0]])
        Q = np.array([0.80])

        # Zero confidence: investor has 0 conviction in this outrageous view
        res = portfolio_black_litterman.optimize_black_litterman(
            cov, w_mkt, P, Q, views_confidences=[0.0]
        )

        weights = res["optimal_weights"]
        assert sum(weights) == pytest.approx(1.0, abs=1e-5)
        # As omega -> inf, Bayesian posterior reverts strictly to market equilibrium weights
        for i in range(3):
            assert weights[i] == pytest.approx(w_mkt[i], abs=0.02)

    def test_black_litterman_full_confidence_view_adoption(self):
        """100% confidence views (omega -> 0) force posterior return to strictly adopt view Q."""
        cov = np.array([
            [0.04, 0.01, 0.01],
            [0.01, 0.04, 0.01],
            [0.01, 0.01, 0.04]
        ])
        w_mkt = np.array([0.3333, 0.3333, 0.3334])
        P = np.array([[1.0, 0.0, 0.0]])
        Q = np.array([0.45])

        res = portfolio_black_litterman.optimize_black_litterman(
            cov, w_mkt, P, Q, views_confidences=[1.0]
        )

        # Asset 0 posterior expected return matches view Q (45%)
        er_post = res["posterior_expected_returns"]
        assert er_post[0] == pytest.approx(0.45, abs=0.01)
        # Optimal weight tilts heavily towards Asset 0
        assert res["optimal_weights"][0] > w_mkt[0]

    def test_black_litterman_singular_prior_covariance(self):
        """Singular prior covariance matrix (rank-deficient) stabilizes via pseudo-inverse without crash."""
        # Rank-2 covariance for 3 assets (Asset 0 and Asset 1 are identical twins)
        cov_sing = np.array([
            [0.04, 0.04, 0.01],
            [0.04, 0.04, 0.01],
            [0.01, 0.01, 0.04]
        ])
        w_mkt = np.array([0.30, 0.30, 0.40])
        P = np.array([[0.0, 0.0, 1.0]])
        Q = np.array([0.18])

        res = portfolio_black_litterman.optimize_black_litterman(cov_sing, w_mkt, P, Q)

        weights = res["optimal_weights"]
        assert sum(weights) == pytest.approx(1.0, abs=1e-5)
        assert all(w >= 0.0 for w in weights)
        assert not any(math.isnan(w) for w in weights)


# ==============================================================================
# 5. LOPEZ DE PRADO HRP: EDGE TOPOLOGIES & NEGATIVE CORRELATIONS
# ==============================================================================

class TestAdversarialLopezDePradoHRP:
    """Stress tests Hierarchical Risk Parity on edge tree topologies and negative correlation matrices."""

    def test_hrp_single_asset_edge_topology(self):
        """Single asset (N=1) returns trivial 100% allocation without clustering collapse."""
        cov_1 = np.array([[0.0625]])
        res = portfolio_hrp.optimize_hrp(cov_1, asset_names=["Nifty50"])

        assert res["weights"] == {"Nifty50": 1.0}
        assert res["cluster_order"] == ["Nifty50"]
        # Annualized vol: sqrt(0.0625 * 252) = 3.9686
        assert res["portfolio_vol_ann"] == pytest.approx(3.9686, abs=1e-3)

    def test_hrp_two_asset_edge_topology(self):
        """Two assets (N=2) single linkage bisects directly into inverse-variance weights."""
        # Asset 0: var = 0.04, Asset 1: var = 0.09.
        # Inverse variances: 1/0.04 = 25, 1/0.09 = 11.1111 -> w0 = 25/36.111 = 0.6923, w1 = 0.3077
        cov_2 = np.array([
            [0.04, 0.00],
            [0.00, 0.09]
        ])
        res = portfolio_hrp.optimize_hrp(cov_2, asset_names=["LowVol", "HighVol"])

        w = res["weights"]
        assert sum(w.values()) == pytest.approx(1.0, abs=1e-5)
        assert w["LowVol"] == pytest.approx(0.6923, abs=1e-3)
        assert w["HighVol"] == pytest.approx(0.3077, abs=1e-3)

    def test_hrp_negative_correlation_matrix(self):
        """Negative correlation matrix (rho = -0.8) yields valid distance metric in [0, 1] and risk reduction."""
        corr_neg = np.array([
            [1.0, -0.8, -0.5],
            [-0.8, 1.0, 0.1],
            [-0.5, 0.1, 1.0]
        ])
        vols = np.array([0.20, 0.18, 0.25])
        cov_neg = corr_neg * np.outer(vols, vols)
        names = ["Equity", "LongBond", "Gold"]

        res = portfolio_hrp.optimize_hrp(cov_neg, corr_neg, asset_names=names)

        w = res["weights"]
        assert len(w) == 3
        assert sum(w.values()) == pytest.approx(1.0, abs=1e-5)
        assert all(val >= 0.0 for val in w.values())
        # Diversification with negative correlation produces portfolio vol lower than constituent vols
        port_vol_daily = res["portfolio_vol_ann"] / math.sqrt(252.0)
        assert port_vol_daily < min(vols)

    def test_hrp_perfect_antithetic_pair(self):
        """Perfect anti-correlation (rho = -1.0) distance D = sqrt(0.5*(1 - (-1))) = 1.0 handled cleanly."""
        corr_anti = np.array([
            [1.0, -1.0],
            [-1.0, 1.0]
        ])
        cov_anti = corr_anti * np.outer([0.20, 0.20], [0.20, 0.20])

        res = portfolio_hrp.optimize_hrp(cov_anti, corr_anti, asset_names=["AssetA", "AssetB"])

        assert sum(res["weights"].values()) == pytest.approx(1.0, abs=1e-5)
        # Symmetrical anti-correlated assets get 50/50 weights
        assert res["weights"]["AssetA"] == pytest.approx(0.5, abs=1e-3)
        assert res["weights"]["AssetB"] == pytest.approx(0.5, abs=1e-3)

    def test_hrp_zero_variance_covariance_matrix(self):
        """Degenerate all-zero covariance matrix allocates uniform 1/N weights without division by zero."""
        cov_zero = np.zeros((4, 4))
        names = ["Z1", "Z2", "Z3", "Z4"]

        res = portfolio_hrp.optimize_hrp(cov_zero, asset_names=names)

        w = res["weights"]
        assert sum(w.values()) == pytest.approx(1.0, abs=1e-5)
        assert res["portfolio_vol_ann"] == 0.0


# ==============================================================================
# 6. FEE DRAG MULTI-HORIZON COMPOUNDING, ZERO FEES, NEGATIVE ALPHA & 30Y HORIZONS
# ==============================================================================

class TestAdversarialFeeDragMultiHorizon:
    """Stress tests fee drag attribution under boundary expense ratios, negative alpha, and extreme horizons."""

    def test_fee_drag_zero_expense_ratio_boundary(self):
        """Zero expense ratio (TER = 0.0%) for both Direct and Regular produces exactly 0.0 fee drag."""
        res = fee_drag.compute_fee_drag_attribution(
            direct_cagr=0.12,
            regular_cagr=0.12,
            ter_direct=0.0,
            ter_regular=0.0,
            initial_capital=100000.0,
        )
        for h in ["1Y", "3Y", "5Y", "10Y"]:
            assert res["horizons"][h]["cumulative_drag_pct"] == 0.0
            assert res["horizons"][h]["rupee_wealth_erosion"] == 0.0
            assert res["horizons"][h]["cagr_spread_pct"] == 0.0

    def test_fee_drag_zero_direct_ter_with_positive_regular_ter(self):
        """Zero Direct TER (zero-fee index fund) isolates 100% of fee drag to distribution drag."""
        res = fee_drag.compute_fee_drag_attribution(
            direct_cagr=0.15,
            regular_cagr=0.135,
            ter_direct=0.0,
            ter_regular=0.015,
            direct_alpha=3.0,
            initial_capital=100000.0,
        )
        alpha_iso = res["alpha_isolation"]

        # Gross alpha equals direct alpha since operating drag is 0.0%
        assert alpha_iso["gross_alpha_pct"] == pytest.approx(3.0, abs=1e-3)
        assert alpha_iso["operating_drag_pct"] == 0.0
        assert alpha_iso["distribution_drag_pct"] == pytest.approx(1.5, abs=1e-3)

    def test_fee_drag_negative_alpha_underperformance(self):
        """Negative manager alpha preserves Direct plan superiority without attribution corruption."""
        res = fee_drag.compute_fee_drag_attribution(
            direct_cagr=0.08,
            regular_cagr=0.068,
            ter_direct=0.0075,
            ter_regular=0.0195,
            direct_alpha=-4.5,
            regular_alpha=-5.7,
            initial_capital=100000.0,
        )
        alpha_iso = res["alpha_isolation"]

        assert alpha_iso["net_alpha_direct_pct"] == -4.5
        assert alpha_iso["net_alpha_regular_pct"] == -5.7
        # Gross alpha = direct_alpha + direct_ter = -4.5 + 0.75 = -3.75%
        assert alpha_iso["gross_alpha_pct"] == pytest.approx(-3.75, abs=1e-3)
        assert alpha_iso["ter_differential_pct"] == pytest.approx(1.20, abs=1e-3)

    def test_fee_drag_zero_initial_capital(self):
        """Zero initial capital yields zero rupee wealth erosion without division by zero errors."""
        res = fee_drag.compute_fee_drag_attribution(
            direct_cagr=0.15,
            regular_cagr=0.138,
            ter_direct=0.0075,
            ter_regular=0.0195,
            initial_capital=0.0,
        )
        for h in ["1Y", "3Y", "5Y", "10Y"]:
            assert res["horizons"][h]["wealth_direct"] == 0.0
            assert res["horizons"][h]["wealth_regular"] == 0.0
            assert res["horizons"][h]["rupee_wealth_erosion"] == 0.0

    def test_fee_drag_extreme_30y_horizon_compounding(self):
        """Verifies fee drag attribution over an extreme 30-year horizon.

        Adversarial test: An institutional investor requests fee drag modeling over 30 years.
        Expected compounding:
            W_D(30) = C_0 * (1 + CAGR_D)^30 = 100,000 * (1 + 0.15)^30 = ₹6,621,177.20
            W_R(30) = C_0 * (1 + CAGR_R)^30 = 100,000 * (1 + 0.13)^30 = ₹3,911,589.83
            Rupee Wealth Erosion = W_D - W_R = ₹2,709,587.37
            Cumulative Drag % = (W_D - W_R) / W_D * 100 = 40.92%

        Defect Under Test:
        In backend/app/services/fee_drag.py, HORIZON_YEARS is hardcoded as:
            HORIZON_YEARS = {'1Y': 1.0, '3Y': 3.0, '5Y': 5.0, '10Y': 10.0}
        and line 177 uses `years = HORIZON_YEARS.get(h, 1.0)`.
        When '30Y' is requested, the engine fails to recognize it and silently defaults to 1.0 year,
        returning W_D = ₹115,000 and drag = 1.74%, understating 30-year rupee wealth erosion by 1,354x!
        """
        res = fee_drag.compute_fee_drag_attribution(
            direct_cagr=0.15,
            regular_cagr=0.13,
            ter_direct=0.0075,
            ter_regular=0.0175,
            horizons=["30Y"],
            initial_capital=100000.0,
        )
        assert "30Y" in res["horizons"], "Result horizons missing '30Y' entry"
        h30 = res["horizons"]["30Y"]

        expected_direct_wealth = 100000.0 * ((1.0 + 0.15) ** 30.0)
        expected_regular_wealth = 100000.0 * ((1.0 + 0.13) ** 30.0)
        expected_erosion = expected_direct_wealth - expected_regular_wealth
        expected_drag_pct = (expected_erosion / expected_direct_wealth) * 100.0

        # Assert 30-year mathematical compounding:
        assert h30["wealth_direct"] == pytest.approx(expected_direct_wealth, rel=1e-2), (
            f"Defect in fee_drag.py: '30Y' horizon compounded for {h30['wealth_direct']} (1 year) "
            f"instead of {expected_direct_wealth:.2f} (30 years). HORIZON_YEARS lacks '30Y'."
        )
        assert h30["wealth_regular"] == pytest.approx(expected_regular_wealth, rel=1e-2)
        assert h30["rupee_wealth_erosion"] == pytest.approx(expected_erosion, rel=1e-2)
        assert h30["cumulative_drag_pct"] == pytest.approx(expected_drag_pct, abs=0.5)
