"""Unit & Numerical Precision Tests for Black-Litterman Portfolio Allocation.
Verifies Bayesian prior blending, tactical view tilts, Idzorek confidence scaling,
and optimal weight determination to 4 decimal places.
"""

import math
import numpy as np
import pytest

from app.portfolio_black_litterman import optimize_black_litterman


class TestBlackLittermanPrecision:
    """Test suite verifying mathematical precision of Black-Litterman model."""

    def test_bl_analytical_implied_prior_returns(self):
        """Analytical equilibrium returns: Pi = lambda * Sigma * w_mkt to 4 decimal places."""
        # 3 assets
        cov = np.array([
            [0.04, 0.012, 0.008],
            [0.012, 0.0625, 0.015],
            [0.008, 0.015, 0.09],
        ])
        w_mkt = np.array([0.5, 0.3, 0.2])
        lmbda = 2.5

        # Analytical Pi calculation:
        # Pi_0 = 2.5 * (0.04*0.5 + 0.012*0.3 + 0.008*0.2) = 2.5 * (0.02 + 0.0036 + 0.0016) = 2.5 * 0.0252 = 0.063000
        # Pi_1 = 2.5 * (0.012*0.5 + 0.0625*0.3 + 0.015*0.2) = 2.5 * (0.006 + 0.01875 + 0.003) = 2.5 * 0.02775 = 0.069375
        # Pi_2 = 2.5 * (0.008*0.5 + 0.015*0.3 + 0.09*0.2) = 2.5 * (0.004 + 0.0045 + 0.018) = 2.5 * 0.0265 = 0.066250
        expected_pi = [0.063000, 0.069375, 0.066250]

        P = np.array([[1.0, 0.0, 0.0]])
        Q = np.array([expected_pi[0]])

        res = optimize_black_litterman(cov, w_mkt, P, Q, risk_aversion=lmbda)
        for i in range(3):
            assert math.isclose(res["prior_returns"][i], expected_pi[i], abs_tol=1e-4)

    def test_bl_weights_sum_to_one_exact(self):
        """Optimal weights must sum to 1.0 within 1e-5."""
        cov = np.diag([0.04, 0.09, 0.16])
        w_mkt = np.array([0.4, 0.35, 0.25])
        P = np.array([[1.0, -1.0, 0.0], [0.0, 1.0, -1.0]])
        Q = np.array([0.05, -0.02])

        res = optimize_black_litterman(cov, w_mkt, P, Q)
        total_w = sum(res["optimal_weights"])
        assert math.isclose(total_w, 1.0, abs_tol=1e-5)
        for w in res["optimal_weights"]:
            assert w >= 0.0

    def test_bl_bullish_relative_view_shifts_weights(self):
        """Bullish view on Asset A over Asset B strictly tilts weight towards Asset A."""
        cov = np.array([[0.04, 0.0], [0.0, 0.04]])
        w_mkt = np.array([0.5, 0.5])
        # Asset 0 outperforms Asset 1 by 8%
        P = np.array([[1.0, -1.0]])
        Q = np.array([0.08])

        res = optimize_black_litterman(cov, w_mkt, P, Q, risk_aversion=2.5, tau=0.05)
        opt_w = res["optimal_weights"]
        assert opt_w[0] > 0.50
        assert opt_w[1] < 0.50
        assert opt_w[0] > opt_w[1]

    def test_bl_confidence_scaling_idzorek_method(self):
        """High investor confidence produces larger return shift than low confidence."""
        cov = np.diag([0.04, 0.04])
        w_mkt = np.array([0.5, 0.5])
        P = np.array([[1.0, 0.0]])
        Q = np.array([0.15])

        res_high = optimize_black_litterman(cov, w_mkt, P, Q, views_confidences=np.array([0.95]))
        res_low = optimize_black_litterman(cov, w_mkt, P, Q, views_confidences=np.array([0.10]))

        er_high = res_high["posterior_expected_returns"][0]
        er_low = res_low["posterior_expected_returns"][0]
        assert er_high > er_low
        assert math.isclose(er_high, 0.15, abs_tol=0.05)

    def test_bl_posterior_covariance_symmetry_and_positive_definiteness(self):
        """Posterior covariance matrix must be strictly symmetric and positive definite."""
        cov = np.array([
            [0.05, 0.015, 0.01],
            [0.015, 0.06, 0.02],
            [0.01, 0.02, 0.08],
        ])
        w_mkt = np.array([0.4, 0.3, 0.3])
        P = np.array([[1.0, 0.0, -1.0]])
        Q = np.array([0.04])

        res = optimize_black_litterman(cov, w_mkt, P, Q)
        post_cov = np.array(res["posterior_cov_matrix"])

        # Check symmetry
        for i in range(3):
            for j in range(3):
                assert math.isclose(post_cov[i, j], post_cov[j, i], abs_tol=1e-6)

        # Check positive eigenvalues
        eigenvals = np.linalg.eigvalsh(post_cov)
        assert all(ev > 0 for ev in eigenvals)

    def test_bl_boundary_contradictory_views_blend_consistently(self):
        """Two conflicting views on the same asset blend consistently without matrix inversion collapse."""
        cov = np.diag([0.04, 0.04])
        w_mkt = np.array([0.5, 0.5])
        P = np.array([[1.0, 0.0], [1.0, 0.0]])
        Q = np.array([0.10, -0.10])

        res = optimize_black_litterman(cov, w_mkt, P, Q)
        opt_w = res["optimal_weights"]
        assert math.isclose(sum(opt_w), 1.0, abs_tol=1e-5)
        assert all(w >= 0.0 for w in opt_w)

    def test_bl_boundary_extreme_negative_view_long_only_clamp(self):
        """Extreme negative view (-70%) pushes asset weight to 0.0 (long-only, no shorting)."""
        cov = np.diag([0.04, 0.04])
        w_mkt = np.array([0.5, 0.5])
        P = np.array([[1.0, 0.0]])
        Q = np.array([-0.70])

        res = optimize_black_litterman(cov, w_mkt, P, Q)
        opt_w = res["optimal_weights"]
        assert math.isclose(opt_w[0], 0.0, abs_tol=1e-4)
        assert math.isclose(opt_w[1], 1.0, abs_tol=1e-4)

    def test_bl_with_asset_names_metadata(self):
        """Supplying asset_names returns convenient dictionary lookups."""
        cov = np.diag([0.04, 0.06])
        w_mkt = np.array([0.6, 0.4])
        P = np.array([[1.0, -1.0]])
        Q = np.array([0.03])
        names = ["Nifty50", "MidCap150"]

        res = optimize_black_litterman(cov, w_mkt, P, Q, asset_names=names)
        assert "weights_by_asset" in res
        assert "posterior_returns_by_asset" in res
        assert "Nifty50" in res["weights_by_asset"]
        assert "MidCap150" in res["weights_by_asset"]

    def test_bl_router_post_endpoint_success(self):
        """POST /api/portfolio-advisor/black-litterman executes successfully with cov matrix."""
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app)
        payload = {
            "cov_matrix": [[0.04, 0.01], [0.01, 0.09]],
            "prior_weights": [0.6, 0.4],
            "views_matrix_P": [[1.0, -1.0]],
            "views_returns_Q": [0.05],
            "asset_names": ["FundA", "FundB"],
            "risk_aversion": 2.5,
            "tau": 0.05,
        }
        resp = client.post("/api/portfolio-advisor/black-litterman", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert "prior_returns" in data
        assert "posterior_expected_returns" in data
        assert "optimal_weights" in data
        assert "posterior_cov_matrix" in data
        assert "risk_budgeting" in data
        assert "weights_by_asset" in data
        assert math.isclose(sum(data["optimal_weights"]), 1.0, abs_tol=1e-4)

    def test_bl_router_post_endpoint_dimension_mismatch_400(self):
        """POST /api/portfolio-advisor/black-litterman returns 400 when dimensions mismatch."""
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app)
        payload = {
            "cov_matrix": [[0.04, 0.01], [0.01, 0.09]],
            "prior_weights": [0.6, 0.4],
            "views_matrix_P": [[1.0, -1.0, 0.5]],  # 3 columns for 2 assets
            "views_returns_Q": [0.05],
        }
        resp = client.post("/api/portfolio-advisor/black-litterman", json=payload)
        assert resp.status_code == 400



# --- Degenerate inputs (ported from the retired adversarial suite) ------------------------

@pytest.mark.parametrize("cov", [np.full((3, 3), 0.04), np.zeros((3, 3))], ids=["rank-1", "all-zero"])
def test_singular_covariance_still_yields_long_only_weights(cov):
    res = optimize_black_litterman(cov, np.array([0.33, 0.33, 0.34]), np.array([[1.0, -1.0, 0.0]]), np.array([0.05]))
    w = res["optimal_weights"]
    assert math.isclose(sum(w), 1.0, abs_tol=1e-5) and all(x >= 0.0 for x in w)
    post = np.array(res["posterior_cov_matrix"])
    assert np.allclose(post, post.T, atol=1e-6)


def test_vanishing_confidence_reverts_monotonically_to_equilibrium():
    cov = np.array([[0.04, 0.01], [0.01, 0.09]])
    w_mkt = np.array([0.6, 0.4])
    pi = 2.5 * (cov @ w_mkt)
    diffs = [
        abs(optimize_black_litterman(cov, w_mkt, np.array([[1.0, 0.0]]), np.array([0.50]), risk_aversion=2.5,
                                     views_confidences=[c])["posterior_expected_returns"][0] - pi[0])
        for c in (0.1, 0.01, 1e-3, 1e-4)
    ]
    assert all(a > b for a, b in zip(diffs, diffs[1:])) and diffs[-1] < 1e-4