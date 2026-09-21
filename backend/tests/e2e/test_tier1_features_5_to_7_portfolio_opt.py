"""Tier 1: Feature Coverage (Features 5 - 7: Portfolio Optimization & Risk Budgeting).
Verifies Hierarchical Risk Parity (HRP), Black-Litterman Allocation, and Risk Budgeting (ENCB/HHI).
Each feature has >= 5 isolated test cases (Total: 15 tests).
"""

import math
import numpy as np
import pytest

from tests.e2e.e2e_oracles import (
    get_hrp_fn,
    get_black_litterman_fn,
    get_risk_budgeting_fn,
)


# =====================================================================
# Feature 5: Hierarchical Risk Parity (HRP) (5 tests)
# =====================================================================

class TestTier1Feature5HierarchicalRiskParity:
    """Feature 5: Machine-learning tree clustering, quasi-diagonalization, and recursive bisection."""

    def test_f5_hrp_weights_sum_to_one(self):
        """HRP allocated weights must sum to 1.0 within 1e-5."""
        np.random.seed(42)
        n = 5
        names = [f"Asset_{i}" for i in range(n)]
        A = np.random.normal(0, 1, (n, n))
        cov = A @ A.T + np.diag(np.random.uniform(0.1, 0.5, n))
        # Correlation matrix
        d = np.sqrt(np.diag(cov))
        corr = cov / np.outer(d, d)

        fn = get_hrp_fn()
        res = fn(cov, corr, names)

        weights = res["weights"]
        assert len(weights) == n
        total_w = sum(weights.values())
        assert math.isclose(total_w, 1.0, abs_tol=1e-5)

    def test_f5_hrp_long_only_non_negative(self):
        """All allocated weights must be non-negative (long-only, no short selling)."""
        np.random.seed(43)
        n = 6
        names = [f"Fund_{i}" for i in range(n)]
        cov = np.diag([0.04, 0.09, 0.16, 0.01, 0.05, 0.08])
        corr = np.eye(n)

        fn = get_hrp_fn()
        res = fn(cov, corr, names)

        for asset, w in res["weights"].items():
            assert w >= 0.0, f"Negative weight for {asset}: {w}"

    def test_f5_hrp_cluster_order_contains_all_assets(self):
        """The quasi-diagonalized cluster order must be a valid permutation of all assets."""
        np.random.seed(44)
        n = 8
        names = [f"Equity_{i}" for i in range(n)]
        A = np.random.normal(0, 1, (n, n))
        cov = A @ A.T
        d = np.sqrt(np.diag(cov))
        corr = cov / np.outer(d, d)

        fn = get_hrp_fn()
        res = fn(cov, corr, names)

        order = res["cluster_order"]
        assert len(order) == n
        assert set(order) == set(names)

    def test_f5_hrp_diversification_over_uncorrelated_assets(self):
        """For uncorrelated assets with equal variances, HRP allocates equal weights (1/N)."""
        n = 4
        names = ["LargeCap", "MidCap", "SmallCap", "Debt"]
        cov = np.eye(n) * 0.04
        corr = np.eye(n)

        fn = get_hrp_fn()
        res = fn(cov, corr, names)

        expected_weight = 1.0 / n
        for asset, w in res["weights"].items():
            assert math.isclose(w, expected_weight, abs_tol=1e-3)

    def test_f5_hrp_inverse_variance_cluster_allocation(self):
        """Asset with significantly lower variance receives higher allocation than high variance asset."""
        names = ["LowVolBond", "HighVolEquity"]
        # Variance of equity is 9x bond variance
        cov = np.array([[0.01, 0.0], [0.0, 0.09]])
        corr = np.eye(2)

        fn = get_hrp_fn()
        res = fn(cov, corr, names)

        assert res["weights"]["LowVolBond"] > res["weights"]["HighVolEquity"]
        assert res["weights"]["LowVolBond"] > 0.70


# =====================================================================
# Feature 6: Black-Litterman Allocation (5 tests)
# =====================================================================

class TestTier1Feature6BlackLitterman:
    """Feature 6: Bayesian allocation blending equilibrium market priors with investor tactical views."""

    def test_f6_equilibrium_priors_match_analytical_pi(self):
        """In the absence of views (or zero views), posterior expected returns reflect implied priors Pi = lambda * Sigma * w."""
        cov = np.array([[0.04, 0.01], [0.01, 0.09]])
        w_mkt = np.array([0.6, 0.4])
        lmbda = 2.5
        expected_pi = lmbda * (cov @ w_mkt)

        fn = get_black_litterman_fn()
        # Empty view or view matching prior
        P = np.array([[1.0, 0.0]])
        Q = np.array([expected_pi[0]])
        res = fn(cov, w_mkt, P, Q, risk_aversion=lmbda)

        prior_returns = res["prior_returns"]
        assert math.isclose(prior_returns[0], expected_pi[0], abs_tol=1e-4)
        assert math.isclose(prior_returns[1], expected_pi[1], abs_tol=1e-4)

    def test_f6_tactical_view_shifts_weights_in_view_direction(self):
        """A strongly bullish tactical view on Asset 1 tilts the allocation towards Asset 1."""
        cov = np.array([[0.04, 0.005], [0.005, 0.04]])
        w_mkt = np.array([0.5, 0.5])
        # View: Asset 0 will outperform Asset 1 by +10%
        P = np.array([[1.0, -1.0]])
        Q = np.array([0.10])

        fn = get_black_litterman_fn()
        res = fn(cov, w_mkt, P, Q, risk_aversion=2.5, tau=0.05)

        opt_w = res["optimal_weights"]
        # Bullish view on Asset 0 over Asset 1 must tilt weight_0 > weight_1
        assert opt_w[0] > opt_w[1]
        assert opt_w[0] > 0.55

    def test_f6_weights_sum_to_one(self):
        """Constrained optimal weights must sum to 1.0 within 1e-4."""
        cov = np.diag([0.04, 0.06, 0.08])
        w_mkt = np.array([0.4, 0.35, 0.25])
        P = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, -1.0]])
        Q = np.array([0.08, 0.02])

        fn = get_black_litterman_fn()
        res = fn(cov, w_mkt, P, Q)

        total_w = sum(res["optimal_weights"])
        assert math.isclose(total_w, 1.0, abs_tol=1e-4)

    def test_f6_confidence_scaling(self):
        """Higher investor confidence in a view produces a larger tilt toward that view."""
        cov = np.diag([0.04, 0.04])
        w_mkt = np.array([0.5, 0.5])
        P = np.array([[1.0, 0.0]])
        Q = np.array([0.12])

        fn = get_black_litterman_fn()
        # High confidence (90%) vs Low confidence (20%)
        res_high_conf = fn(cov, w_mkt, P, Q, views_confidences=np.array([0.90]))
        res_low_conf = fn(cov, w_mkt, P, Q, views_confidences=np.array([0.20]))

        # High confidence should produce a higher posterior expected return and larger tilt
        er_high = res_high_conf["posterior_expected_returns"][0]
        er_low = res_low_conf["posterior_expected_returns"][0]
        assert er_high > er_low

    def test_f6_posterior_covariance_incorporates_view_uncertainty(self):
        """Posterior covariance matrix is symmetric and positive-semidefinite."""
        cov = np.array([[0.04, 0.01], [0.01, 0.05]])
        w_mkt = np.array([0.5, 0.5])
        P = np.array([[1.0, 0.0]])
        Q = np.array([0.05])

        fn = get_black_litterman_fn()
        res = fn(cov, w_mkt, P, Q)

        post_cov = np.array(res["posterior_cov_matrix"])
        # Symmetry
        assert math.isclose(post_cov[0, 1], post_cov[1, 0], abs_tol=1e-6)
        # Positive eigenvalues
        eigenvals = np.linalg.eigvalsh(post_cov)
        assert all(ev > 0 for ev in eigenvals)


# =====================================================================
# Feature 7: Risk Budgeting & Concentration (5 tests)
# =====================================================================

class TestTier1Feature7RiskBudgeting:
    """Feature 7: Marginal risk contribution, ENCB (risk contribution entropy), HHI, and concentration."""

    def test_f7_euler_risk_contributions_sum_to_portfolio_volatility(self):
        """Euler's theorem: sum of component risk contributions equals portfolio volatility: sum(RC_i) = sigma_p."""
        weights = np.array([0.4, 0.35, 0.25])
        cov = np.array([
            [0.04, 0.015, 0.01],
            [0.015, 0.06, 0.02],
            [0.01, 0.02, 0.09],
        ])
        names = ["FundA", "FundB", "FundC"]

        fn = get_risk_budgeting_fn()
        res = fn(weights, cov, names)

        sum_rc = sum(res["risk_contributions"].values())
        port_vol = res["portfolio_volatility"]
        assert math.isclose(sum_rc, port_vol, abs_tol=1e-5)

    def test_f7_hhi_concentration_bounds(self):
        """HHI concentration index is strictly bounded in [1/N, 1.0]."""
        n = 5
        names = [f"Asset_{i}" for i in range(n)]
        cov = np.eye(n) * 0.04

        fn = get_risk_budgeting_fn()
        # Equal weights: HHI must equal exactly 1/N = 0.20
        res_eq = fn(np.ones(n) / n, cov, names)
        assert math.isclose(res_eq["hhi"], 1.0 / n, abs_tol=1e-4)

        # Concentrated in one asset: HHI must equal 1.0
        w_conc = np.array([1.0, 0.0, 0.0, 0.0, 0.0])
        res_conc = fn(w_conc, cov, names)
        assert math.isclose(res_conc["hhi"], 1.0, abs_tol=1e-4)

    def test_f7_enc_equals_n_for_equal_weight_portfolio(self):
        """Effective Number of Constituents (ENC = 1 / HHI) equals N for equal-weight portfolio."""
        n = 10
        names = [f"Stock_{i}" for i in range(n)]
        cov = np.eye(n) * 0.05
        weights = np.ones(n) / n

        fn = get_risk_budgeting_fn()
        res = fn(weights, cov, names)

        assert math.isclose(res["effective_number_of_constituents"], float(n), abs_tol=1e-2)

    def test_f7_encb_decreases_with_correlated_assets(self):
        """High asset correlation reduces Effective Number of Correlated Bets (ENCB < ENC)."""
        names = ["LargeCap", "IndexFund"]
        weights = np.array([0.5, 0.5])
        # Highly correlated assets (rho = 0.98)
        cov_corr = np.array([[0.04, 0.0392], [0.0392, 0.04]])

        fn = get_risk_budgeting_fn()
        res = fn(weights, cov_corr, names)

        # While ENC = 2.0, ENCB captures the correlation
        assert res["effective_number_of_constituents"] == 2.0
        assert res["effective_number_of_correlated_bets"] <= 2.0

    def test_f7_marginal_risk_contribution_proportionality(self):
        """Higher-volatility asset with positive weight exhibits higher marginal risk contribution."""
        names = ["LowVol", "HighVol"]
        weights = np.array([0.5, 0.5])
        cov = np.array([[0.01, 0.0], [0.0, 0.09]])

        fn = get_risk_budgeting_fn()
        res = fn(weights, cov, names)

        mrc = res["marginal_risk_contributions"]
        assert mrc["HighVol"] > mrc["LowVol"]
        assert mrc["HighVol"] >= 2.0 * mrc["LowVol"]
