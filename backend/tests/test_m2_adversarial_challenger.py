"""Adversarial Verification Test Suite for Milestone 2 (Gen 2).

Adversarially tests:
1. Black-Litterman: Contradictory views (P = [[1, 0], [1, 0]], Q = [0.20, -0.20]).
2. Black-Litterman: Near-zero confidence views (c -> 0) smooth reversion to Pi.
3. Black-Litterman: Extreme risk aversion (lambda = 100.0) & catastrophic negative return views (-90%).
4. Black-Litterman: Singular prior covariance matrix & zero-variance edge cases.
5. Risk Budgeting: Euler decomposition precision across 1,000 random weight vectors (sum(RC_i) == sigma_p).
6. Risk Budgeting: ENCB under identical perfectly correlated assets verifying ENCB strictly less than ENC.
7. Risk Budgeting: Efficient frontier numerical stability with 20 assets.
"""

import math
import numpy as np
import pytest

from app.portfolio_black_litterman import optimize_black_litterman
from app.risk_budgeting import compute_risk_budgeting, compute_efficient_frontier


class TestMilestone2AdversarialChallenger:
    """Adversarial test harness challenging Milestone 2 implementations."""

    # -------------------------------------------------------------------------
    # 1. Black-Litterman: Contradictory Views
    # -------------------------------------------------------------------------
    def test_bl_contradictory_views_blending(self):
        """Contradictory views on the same asset (e.g. +20% and -20%) must cancel out and not explode."""
        cov = np.array([[0.04, 0.0], [0.0, 0.04]])
        w_mkt = np.array([0.5, 0.5])
        P = np.array([[1.0, 0.0], [1.0, 0.0]])
        Q = np.array([0.20, -0.20])

        res = optimize_black_litterman(cov, w_mkt, P, Q, risk_aversion=2.5, tau=0.05)
        
        # Validations
        opt_w = res["optimal_weights"]
        assert math.isclose(sum(opt_w), 1.0, abs_tol=1e-5), f"Weights sum {sum(opt_w)} != 1.0"
        assert all(w >= 0.0 for w in opt_w), "Negative weights present in long-only allocation"
        
        # Expected return of asset 0 should be pulled down from Pi (0.05) towards the view mean (0.0)
        # without NaN or Inf
        er = res["posterior_expected_returns"]
        assert not math.isnan(er[0]) and not math.isinf(er[0])
        assert not math.isnan(er[1]) and not math.isinf(er[1])
        assert er[0] < res["prior_returns"][0]  # Pulled down because net view is 0.0 < prior 0.05
        assert math.isclose(er[1], res["prior_returns"][1], abs_tol=1e-5)  # Asset 1 unaffected

    # -------------------------------------------------------------------------
    # 2. Black-Litterman: Near-Zero Confidence Views
    # -------------------------------------------------------------------------
    def test_bl_near_zero_confidence_reversion_to_pi(self):
        """As confidence c -> 0, posterior expected returns must smoothly revert to equilibrium returns Pi."""
        cov = np.array([[0.04, 0.01], [0.01, 0.09]])
        w_mkt = np.array([0.6, 0.4])
        P = np.array([[1.0, 0.0]])
        Q = np.array([0.50])  # Extreme bullish view

        pi = 2.5 * (cov @ w_mkt)

        # Monotonic convergence test: diff should decrease as confidence c decreases
        diffs = []
        confidences = [0.1, 0.01, 1e-3, 1e-4]
        for c in confidences:
            res = optimize_black_litterman(cov, w_mkt, P, Q, risk_aversion=2.5, views_confidences=[c])
            post_er = res["posterior_expected_returns"]
            diff = abs(post_er[0] - pi[0])
            diffs.append(diff)

        # Verify strict monotonic decrease
        for i in range(len(diffs) - 1):
            assert diffs[i] > diffs[i + 1], f"Non-monotonic reversion: {diffs[i]} <= {diffs[i+1]}"

        # At near-zero confidence c=1e-4, diff must be < 1e-4 (smooth asymptotic reversion to Pi)
        assert diffs[-1] < 1e-4, f"Asymptotic reversion failed at c=1e-4: diff={diffs[-1]}"

        # Weights at c=1e-4 must match prior weights [0.6, 0.4] within 0.001
        res_near_zero = optimize_black_litterman(cov, w_mkt, P, Q, risk_aversion=2.5, views_confidences=[1e-4])
        opt_w = res_near_zero["optimal_weights"]
        assert math.isclose(opt_w[0], 0.60, abs_tol=1e-3)
        assert math.isclose(opt_w[1], 0.40, abs_tol=1e-3)

    # -------------------------------------------------------------------------
    # 3. Black-Litterman: Extreme Risk Aversion & Catastrophic Views
    # -------------------------------------------------------------------------
    def test_bl_extreme_risk_aversion_and_catastrophic_negative_return(self):
        """Extreme risk aversion (lambda = 100.0) with catastrophic view (-90%) must be stable."""
        cov = np.array([[0.04, 0.0], [0.0, 0.04]])
        w_mkt = np.array([0.5, 0.5])
        P = np.array([[1.0, 0.0]])
        Q = np.array([-0.90])

        res = optimize_black_litterman(cov, w_mkt, P, Q, risk_aversion=100.0, views_confidences=[0.99])
        opt_w = res["optimal_weights"]
        
        # Asset 0 with -90% return must be completely clamped to 0.0
        assert math.isclose(opt_w[0], 0.0, abs_tol=1e-4)
        assert math.isclose(opt_w[1], 1.0, abs_tol=1e-4)
        assert math.isclose(sum(opt_w), 1.0, abs_tol=1e-5)

    def test_bl_all_catastrophic_negative_views_fallback(self):
        """When all views are catastrophic (-90% on all assets), fallback avoids division by zero."""
        cov = np.array([[0.04, 0.0], [0.0, 0.04]])
        w_mkt = np.array([0.5, 0.5])
        P = np.array([[1.0, 0.0], [0.0, 1.0]])
        Q = np.array([-0.90, -0.90])

        res = optimize_black_litterman(cov, w_mkt, P, Q, risk_aversion=100.0, views_confidences=[0.99, 0.99])
        opt_w = res["optimal_weights"]
        
        # Should gracefully allocate equal weights [0.5, 0.5] without crashing
        assert math.isclose(opt_w[0], 0.5, abs_tol=1e-4)
        assert math.isclose(opt_w[1], 0.5, abs_tol=1e-4)
        assert math.isclose(sum(opt_w), 1.0, abs_tol=1e-5)

    # -------------------------------------------------------------------------
    # 4. Black-Litterman: Singular Prior Covariance Matrix
    # -------------------------------------------------------------------------
    def test_bl_singular_prior_covariance_matrix(self):
        """Singular (rank-1) covariance matrix does not raise LinAlgError and produces valid portfolio."""
        cov_singular = np.array([
            [0.04, 0.04, 0.04],
            [0.04, 0.04, 0.04],
            [0.04, 0.04, 0.04],
        ])
        w_mkt = np.array([0.33, 0.33, 0.34])
        P = np.array([[1.0, -1.0, 0.0]])
        Q = np.array([0.05])

        res = optimize_black_litterman(cov_singular, w_mkt, P, Q)
        opt_w = res["optimal_weights"]
        
        assert math.isclose(sum(opt_w), 1.0, abs_tol=1e-5)
        assert all(w >= 0.0 for w in opt_w)
        
        # Verify posterior covariance is symmetric
        post_cov = np.array(res["posterior_cov_matrix"])
        assert np.allclose(post_cov, post_cov.T, atol=1e-6)

    def test_bl_all_zero_covariance_matrix(self):
        """All-zero covariance matrix (zero volatility universe) handles smoothly via pinv."""
        cov_zero = np.zeros((3, 3))
        w_mkt = np.array([0.33, 0.33, 0.34])
        P = np.array([[1.0, -1.0, 0.0]])
        Q = np.array([0.05])

        res = optimize_black_litterman(cov_zero, w_mkt, P, Q)
        opt_w = res["optimal_weights"]
        assert math.isclose(sum(opt_w), 1.0, abs_tol=1e-5)
        assert all(w >= 0.0 for w in opt_w)

    # -------------------------------------------------------------------------
    # 5. Risk Budgeting: Euler Decomposition Across 1,000 Random Weight Vectors
    # -------------------------------------------------------------------------
    def test_rb_euler_decomposition_1000_random_weight_vectors(self):
        """Euler decomposition sum(RC_i) == sigma_p holds to 6 decimal places across 1,000 random vectors."""
        np.random.seed(42)
        cov = np.array([
            [0.04, 0.012, 0.008],
            [0.012, 0.0625, 0.015],
            [0.008, 0.015, 0.09],
        ])
        names = ["A", "B", "C"]

        max_diff = 0.0
        for i in range(1000):
            w = np.random.dirichlet(np.ones(3))
            res = compute_risk_budgeting(w, cov, names)
            
            sigma_p = res["portfolio_volatility"]
            sum_rc = sum(res["risk_contributions"].values())
            
            diff = abs(sum_rc - sigma_p)
            if diff > max_diff:
                max_diff = diff
            
            assert math.isclose(sum_rc, sigma_p, abs_tol=1e-5), (
                f"Euler violation at iteration {i}: sum(RC_i)={sum_rc} != sigma_p={sigma_p}, diff={diff}"
            )

        # Max diff across 1,000 runs must not exceed 1e-5
        assert max_diff <= 1e-5, f"Max diff exceeded 1e-5: {max_diff}"

    # -------------------------------------------------------------------------
    # 6. Risk Budgeting: ENCB Under Identical Perfectly Correlated Assets
    # -------------------------------------------------------------------------
    def test_rb_encb_identical_perfectly_correlated_assets_fails_requirement(self):
        """Empirical verification of whether ENCB is strictly less than ENC under perfectly correlated identical assets.
        
        Requirement from Orchestrator:
        "Risk Budgeting: ENCB under identical perfectly correlated assets verifying ENCB strictly less than ENC."
        
        Mathematical analysis:
        For two identical assets with rho = 1.0, the portfolio variance is:
        sigma_p^2 = w1^2*s^2 + 2*w1*w2*s^2 + w2^2*s^2 = s^2*(w1+w2)^2 = s^2.
        MRC_1 = (Sigma w)_1 / sigma_p = s^2 / s = s = sigma_p.
        Component risk contribution RC_1 = w1 * MRC_1 = w1 * sigma_p.
        Percentage risk contribution PRC_1 = RC_1 / sigma_p = w1.
        
        Therefore, PRC is IDENTICAL to w!
        In the current implementation (app/risk_budgeting.py:89-99):
        entropy = -sum(PRC * ln(PRC)) = -sum(w * ln(w)).
        ENCB = exp(entropy).
        For equal weights w = [0.5, 0.5]:
        entropy = ln(2), so ENCB = exp(ln(2)) = 2.0.
        ENC = 1 / (0.5^2 + 0.5^2) = 2.0.
        
        Hence ENCB == ENC == 2.0, and the requirement ENCB < ENC FAILS.
        Furthermore, for unequal weights w = [0.7, 0.3], by Renyi entropy ordering,
        ENCB = 1.84 > ENC = 1.72 (ENCB is strictly GREATER than ENC!).
        """
        cov_corr = np.array([
            [0.04, 0.04],
            [0.04, 0.04],
        ])
        weights = np.array([0.5, 0.5])
        names = ["Fund_1", "Fund_2"]

        res = compute_risk_budgeting(weights, cov_corr, names)
        enc = res["effective_number_of_constituents"]
        encb = res["effective_number_of_correlated_bets"]
        assert enc == 2.0
        assert encb == 1.0
        # Directly testing the requirement ENCB < ENC:
        assert encb < enc, (
            f"Adversarial Bug Found: ENCB ({encb}) is NOT strictly less than ENC ({enc}) "
            f"under perfectly correlated identical assets! Implementation computes entropy of "
            f"asset percentage risk contributions (PRC) rather than orthogonal/uncorrelated bets (Meucci)."
        )

        # Unequal weights under perfect correlation (w = [0.7, 0.3]):
        # Meucci orthogonal factor decomposition assigns 100% variance to the single principal factor
        w_unequal = np.array([0.7, 0.3])
        res_unequal = compute_risk_budgeting(w_unequal, cov_corr, names)
        enc_u = res_unequal["effective_number_of_constituents"]
        encb_u = res_unequal["effective_number_of_correlated_bets"]
        assert encb_u == 1.0
        assert encb_u < enc_u

    # -------------------------------------------------------------------------
    # 7. Risk Budgeting: Efficient Frontier Numerical Stability with 20 Assets
    # -------------------------------------------------------------------------
    def test_rb_efficient_frontier_stability_20_assets(self):
        """Efficient frontier numerical stability with 20 assets solving 50 target return points."""
        np.random.seed(42)
        n = 20
        er = np.random.uniform(0.08, 0.22, n)
        
        # Generate positive definite covariance matrix
        A = np.random.randn(n, n)
        cov = A @ A.T * 0.001 + np.eye(n) * 0.02
        names = [f"Asset_{i}" for i in range(n)]

        res = compute_efficient_frontier(er, cov, names, rf=0.065, n_points=50)
        pts = res["frontier_points"]

        assert len(pts) > 40, f"Expected ~50 frontier points, got {len(pts)}"
        
        # Monotonicity check
        vols = [p["volatility"] for p in pts]
        assert vols == sorted(vols), "Frontier points are not sorted monotonically by volatility"
        
        # Max Sharpe ratio >= Min Vol portfolio Sharpe ratio
        max_sharpe = res["max_sharpe_portfolio"]["sharpe_ratio"]
        min_vol_sharpe = res["min_vol_portfolio"]["sharpe_ratio"]
        assert max_sharpe >= min_vol_sharpe, f"Max Sharpe {max_sharpe} < Min Vol Sharpe {min_vol_sharpe}"

        # Weights sum to 1.0 for each point
        for pt in pts:
            total_w = sum(pt["weights"].values())
            assert math.isclose(total_w, 1.0, abs_tol=1e-3), f"Frontier point weights sum {total_w} != 1.0"
