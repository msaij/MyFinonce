"""Tier 2: Boundary & Corner Cases (Features 5 - 7: Portfolio Optimization & Risk Budgeting).
Verifies singular covariance matrices, 100% correlation collapse resistance, single-asset universes,
contradictory views, zero-confidence views, and extreme risk aversion.
Each feature has >= 5 boundary test cases (Total: 15 tests).
"""

import math
import numpy as np
import pytest

from tests.e2e.e2e_oracles import (
    get_hrp_fn,
    get_black_litterman_fn,
    get_risk_budgeting_fn,
    oracle_optimize_hrp,
    oracle_optimize_black_litterman,
    oracle_compute_risk_budgeting,
)


# =====================================================================
# Feature 5: HRP Boundary Cases (5 tests)
# =====================================================================

class TestTier2Feature5HRPBoundaries:
    """Boundary conditions for Hierarchical Risk Parity."""

    def test_f5_boundary_singular_covariance_matrix(self):
        """Singular, rank-deficient covariance matrix allocates stable weights without collapsing (unlike Markowitz)."""
        # 4 assets constructed from 2 latent factors (rank 2 matrix)
        F = np.array([[0.02, 0.01], [-0.01, 0.03], [0.015, -0.01], [0.03, 0.02]])
        cov_singular = F @ F.T  # Rank 2, 4x4 matrix (determinant = 0)
        d = np.sqrt(np.diag(cov_singular))
        corr_singular = cov_singular / np.outer(d, d)
        names = ["A", "B", "C", "D"]

        res = get_hrp_fn()(cov_singular, corr_singular, names)

        weights = res["weights"]
        assert math.isclose(sum(weights.values()), 1.0, abs_tol=1e-5)
        for w in weights.values():
            assert not math.isnan(w)
            assert w >= 0.0

    def test_f5_boundary_identical_100pct_correlated_assets(self):
        """Identical assets (correlation = 1.0, distance = 0) allocate without division-by-zero or weight collapse."""
        n = 4
        cov = np.full((n, n), 0.04)
        corr = np.ones((n, n))
        names = ["Fund1", "Fund2", "Fund3", "Fund4"]

        res = get_hrp_fn()(cov, corr, names)

        assert math.isclose(sum(res["weights"].values()), 1.0, abs_tol=1e-5)
        for w in res["weights"].values():
            assert not math.isnan(w)
            assert math.isclose(w, 1.0 / n, abs_tol=1e-3)

    def test_f5_boundary_single_asset_universe(self):
        """Single asset universe (N=1) trivially receives 100% weight."""
        cov = np.array([[0.05]])
        corr = np.array([[1.0]])
        names = ["SoloAsset"]

        res = get_hrp_fn()(cov, corr, names)

        assert res["weights"]["SoloAsset"] == 1.0
        assert res["cluster_order"] == ["SoloAsset"]

    def test_f5_boundary_extreme_variance_disparity(self):
        """Disparity of 1,000,000x in variance allocates without underflow or overflow."""
        cov = np.diag([1e-4, 100.0])
        corr = np.eye(2)
        names = ["UltraSafe", "UltraVolatile"]

        res = get_hrp_fn()(cov, corr, names)

        assert res["weights"]["UltraSafe"] > 0.99
        assert res["weights"]["UltraVolatile"] < 0.01
        assert sum(res["weights"].values()) <= 1.0001

    def test_f5_boundary_negative_correlation(self):
        """Strongly negative correlation (-0.95) handles distance transformation cleanly."""
        cov = np.array([[0.04, -0.038], [-0.038, 0.04]])
        corr = np.array([[1.0, -0.95], [-0.95, 1.0]])
        names = ["Equity", "Hedge"]

        res = get_hrp_fn()(cov, corr, names)

        assert math.isclose(res["weights"]["Equity"], 0.5, abs_tol=1e-3)
        assert math.isclose(res["weights"]["Hedge"], 0.5, abs_tol=1e-3)


# =====================================================================
# Feature 6: Black-Litterman Boundary Cases (5 tests)
# =====================================================================

class TestTier2Feature6BlackLittermanBoundaries:
    """Boundary conditions for Black-Litterman allocation."""

    def test_f6_boundary_contradictory_views(self):
        """Two conflicting views on the same asset blend consistently without matrix inversion collapse."""
        cov = np.diag([0.04, 0.04])
        w_mkt = np.array([0.5, 0.5])
        # View 1: Asset 0 returns +10%; View 2: Asset 0 returns -10%
        P = np.array([[1.0, 0.0], [1.0, 0.0]])
        Q = np.array([0.10, -0.10])

        res = get_black_litterman_fn()(cov, w_mkt, P, Q)

        opt_w = res["optimal_weights"]
        assert math.isclose(sum(opt_w), 1.0, abs_tol=1e-4)
        assert all(w >= 0.0 for w in opt_w)

    def test_f6_boundary_zero_confidence_uninformative_views(self):
        """Near-zero confidence in views retains weights close to the prior equilibrium allocation."""
        cov = np.diag([0.04, 0.04])
        w_mkt = np.array([0.5, 0.5])
        P = np.array([[1.0, -1.0]])
        Q = np.array([0.25])  # Outlandish view

        # Very low confidence (0.01)
        res = get_black_litterman_fn()(cov, w_mkt, P, Q, views_confidences=np.array([0.01]))

        opt_w = res["optimal_weights"]
        # Tilt should be heavily damped
        assert abs(opt_w[0] - w_mkt[0]) < 0.10

    def test_f6_boundary_extreme_risk_aversion(self):
        """Extreme risk aversion (lambda = 50.0) scales prior returns proportionally without breakdown."""
        cov = np.diag([0.04, 0.06])
        w_mkt = np.array([0.6, 0.4])
        P = np.array([[1.0, 0.0]])
        Q = np.array([0.05])

        res = get_black_litterman_fn()(cov, w_mkt, P, Q, risk_aversion=50.0)

        assert not any(math.isnan(x) for x in res["optimal_weights"])
        assert math.isclose(sum(res["optimal_weights"]), 1.0, abs_tol=1e-4)

    def test_f6_boundary_extreme_negative_views(self):
        """Severe negative tactical view (-80% return) maintains long-only boundary (weights >= 0)."""
        cov = np.diag([0.04, 0.04])
        w_mkt = np.array([0.5, 0.5])
        P = np.array([[1.0, 0.0]])
        Q = np.array([-0.80])  # Catastrophic view on asset 0

        res = get_black_litterman_fn()(cov, w_mkt, P, Q)

        opt_w = res["optimal_weights"]
        assert opt_w[0] >= 0.0
        assert opt_w[1] > 0.90  # Shifted to asset 1

    def test_f6_boundary_zero_uncertainty_tau(self):
        """Uninformative view with near-zero confidence preserves prior market weights."""
        cov = np.diag([0.04, 0.04])
        w_mkt = np.array([0.7, 0.3])
        P = np.array([[0.0, 1.0]])
        Q = np.array([0.15])

        # Extremely low confidence in the view (0.001) leaves prior intact
        res = get_black_litterman_fn()(cov, w_mkt, P, Q, views_confidences=np.array([0.001]))

        opt_w = res["optimal_weights"]
        assert abs(opt_w[0] - w_mkt[0]) < 0.05


# =====================================================================
# Feature 7: Risk Budgeting & Concentration Boundaries (5 tests)
# =====================================================================

class TestTier2Feature7RiskBudgetingBoundaries:
    """Boundary conditions for risk budgeting, ENCB, and HHI."""

    def test_f7_boundary_single_asset_enc_and_encb(self):
        """Single asset portfolio has HHI = 1.0, ENC = 1.0, and ENCB = 1.0."""
        weights = np.array([1.0])
        cov = np.array([[0.04]])
        names = ["Solo"]

        res = get_risk_budgeting_fn()(weights, cov, names)

        assert res["hhi"] == 1.0
        assert res["effective_number_of_constituents"] == 1.0
        assert res["effective_number_of_correlated_bets"] == 1.0

    def test_f7_boundary_zero_weight_asset(self):
        """Asset with zero weight has exactly zero risk contribution and 0% percentage contribution."""
        weights = np.array([1.0, 0.0])
        cov = np.array([[0.04, 0.01], [0.01, 0.09]])
        names = ["Active", "Excluded"]

        res = get_risk_budgeting_fn()(weights, cov, names)

        assert res["risk_contributions"]["Excluded"] == 0.0
        assert res["percentage_risk_contributions"]["Excluded"] == 0.0
        assert res["effective_number_of_constituents"] == 1.0

    def test_f7_boundary_perfectly_correlated_cluster(self):
        """Highly correlated cluster of assets reduces ENCB far below nominal asset count."""
        # 4 identical correlated assets
        n = 4
        weights = np.ones(n) / n
        cov = np.full((n, n), 0.0396) + np.eye(n) * 0.0004  # ~99% correlation
        names = [f"Asset_{i}" for i in range(n)]

        res = get_risk_budgeting_fn()(weights, cov, names)

        assert res["effective_number_of_constituents"] == 4.0
        # ENCB captures true lack of diversification
        assert res["effective_number_of_correlated_bets"] <= 4.0

    def test_f7_boundary_all_assets_equal_risk_contributions(self):
        """Equal risk parity allocation produces equal percentage risk contributions (1/N each)."""
        # Uncorrelated assets with inverse-vol weights
        vols = np.array([0.1, 0.2, 0.3])
        inv_vols = 1.0 / vols
        weights = inv_vols / np.sum(inv_vols)
        cov = np.diag(vols**2)
        names = ["A", "B", "C"]

        res = get_risk_budgeting_fn()(weights, cov, names)

        prc = res["percentage_risk_contributions"]
        for asset in names:
            assert math.isclose(prc[asset], 100.0 / 3.0, abs_tol=0.1)

    def test_f7_boundary_near_zero_portfolio_volatility(self):
        """Near-zero portfolio volatility (sigma_p -> 1e-6) does not trigger ZeroDivisionError in MRC."""
        weights = np.array([0.5, 0.5])
        cov = np.eye(2) * 1e-12  # Very tiny risk
        names = ["Cash1", "Cash2"]

        res = get_risk_budgeting_fn()(weights, cov, names)

        assert not math.isnan(res["portfolio_volatility"])
        assert not any(math.isnan(v) for v in res["marginal_risk_contributions"].values())
