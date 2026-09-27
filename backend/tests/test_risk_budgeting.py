"""Unit & Numerical Precision Tests for Risk Budgeting & Concentration Metrics.
Verifies Euler decomposition, HHI, ENC and ENCB
against institutional mathematical standards to 4 decimal places.
"""

import math
import numpy as np

from app.risk_budgeting import compute_risk_budgeting


class TestRiskBudgetingPrecision:
    """Test suite verifying mathematical precision of risk budgeting & concentration."""

    def test_euler_risk_decomposition_analytical_equality(self):
        """Euler's theorem: sum(RC_i) = sigma_p and sum(Percentage_RC_i) = 100.0% to 4 decimal places."""
        weights = np.array([0.5, 0.3, 0.2])
        cov = np.array([
            [0.04, 0.012, 0.008],
            [0.012, 0.0625, 0.015],
            [0.008, 0.015, 0.09],
        ])
        names = ["EquityLarge", "EquityMid", "DebtShort"]

        # Analytical sigma_p:
        # cov @ w = [0.04*0.5 + 0.012*0.3 + 0.008*0.2,
        #            0.012*0.5 + 0.0625*0.3 + 0.015*0.2,
        #            0.008*0.5 + 0.015*0.3 + 0.09*0.2]
        #         = [0.0252, 0.02775, 0.0265]
        # port_var = w @ (cov @ w) = 0.5*0.0252 + 0.3*0.02775 + 0.2*0.0265
        #          = 0.0126 + 0.008325 + 0.0053 = 0.026225
        # port_vol = sqrt(0.026225) = 0.1619413474
        expected_vol = math.sqrt(0.026225)

        # Analytical MRC_i = (cov @ w)_i / port_vol:
        # MRC_0 = 0.0252 / 0.1619413 = 0.155612
        # MRC_1 = 0.02775 / 0.1619413 = 0.171358
        # MRC_2 = 0.0265 / 0.1619413 = 0.163640
        # Analytical RC_i = w_i * MRC_i:
        # RC_0 = 0.5 * 0.155612 = 0.077806
        # RC_1 = 0.3 * 0.171358 = 0.051408
        # RC_2 = 0.2 * 0.163640 = 0.032728
        # sum(RC_i) = 0.077806 + 0.051408 + 0.032728 = 0.161942 == port_vol!
        res = compute_risk_budgeting(weights, cov, names)

        assert math.isclose(res["portfolio_volatility"], expected_vol, abs_tol=1e-5)
        sum_rc = sum(res["risk_contributions"].values())
        assert math.isclose(sum_rc, res["portfolio_volatility"], abs_tol=1e-5)

        sum_prc = sum(res["percentage_risk_contributions"].values())
        assert math.isclose(sum_prc, 100.0, abs_tol=1e-3)

    def test_hhi_and_normalized_hhi_analytical_bounds(self):
        """HHI strictly bounded in [1/N, 1.0], normalized HHI in [0.0, 1.0]."""
        n = 4
        cov = np.eye(n) * 0.04
        names = [f"Asset_{i}" for i in range(n)]

        # Equal weight
        w_eq = np.ones(n) / n
        res_eq = compute_risk_budgeting(w_eq, cov, names)
        assert math.isclose(res_eq["hhi"], 0.250000, abs_tol=1e-5)
        assert math.isclose(res_eq["normalized_hhi"], 0.000000, abs_tol=1e-5)
        assert math.isclose(res_eq["effective_number_of_constituents"], 4.00, abs_tol=1e-2)

        # Concentrated weight [1.0, 0.0, 0.0, 0.0]
        w_conc = np.array([1.0, 0.0, 0.0, 0.0])
        res_conc = compute_risk_budgeting(w_conc, cov, names)
        assert math.isclose(res_conc["hhi"], 1.000000, abs_tol=1e-5)
        assert math.isclose(res_conc["normalized_hhi"], 1.000000, abs_tol=1e-5)
        assert math.isclose(res_conc["effective_number_of_constituents"], 1.00, abs_tol=1e-2)

    def test_encb_decreases_with_correlated_assets(self):
        """Effective Number of Correlated Bets drops significantly when assets are correlated."""
        names = ["A", "B"]
        w = np.array([0.5, 0.5])

        # Uncorrelated assets
        cov_uncorr = np.diag([0.04, 0.04])
        res_uncorr = compute_risk_budgeting(w, cov_uncorr, names)
        assert math.isclose(res_uncorr["effective_number_of_correlated_bets"], 2.0, abs_tol=0.05)

        # Highly correlated assets (rho = 0.99)
        cov_corr = np.array([[0.04, 0.0396], [0.0396, 0.04]])
        res_corr = compute_risk_budgeting(w, cov_corr, names)
        assert res_corr["effective_number_of_constituents"] == 2.0
        assert res_corr["effective_number_of_correlated_bets"] <= 2.0
        assert res_corr["effective_number_of_correlated_bets"] < res_corr["effective_number_of_constituents"]

        # Perfectly correlated assets (rho = 1.0, Meucci 2009 PCA factor decomposition)
        cov_perf = np.array([[0.04, 0.04], [0.04, 0.04]])
        res_perf = compute_risk_budgeting(w, cov_perf, names)
        assert res_perf["effective_number_of_constituents"] == 2.0
        assert res_perf["effective_number_of_correlated_bets"] == 1.0
        assert res_perf["effective_number_of_correlated_bets"] < res_perf["effective_number_of_constituents"]

        # Unequal weights under perfect correlation (w = [0.7, 0.3])
        w_unequal = np.array([0.7, 0.3])
        res_unequal = compute_risk_budgeting(w_unequal, cov_perf, names)
        assert res_unequal["effective_number_of_correlated_bets"] == 1.0
        assert res_unequal["effective_number_of_correlated_bets"] < res_unequal["effective_number_of_constituents"]

    def test_equal_risk_parity_analytical_contributions(self):
        """Under inverse-volatility weighting on uncorrelated assets, each asset contributes exactly 1/N risk."""
        vols = np.array([0.10, 0.20, 0.40])
        inv_vols = 1.0 / vols
        weights = inv_vols / np.sum(inv_vols)
        cov = np.diag(vols**2)
        names = ["Bond", "Balanced", "SmallCap"]

        res = compute_risk_budgeting(weights, cov, names)
        expected_pct = 100.0 / 3.0  # 33.3333%

        for name in names:
            assert math.isclose(res["percentage_risk_contributions"][name], expected_pct, abs_tol=1e-3)

    def test_zero_weight_asset_zero_contribution(self):
        """Asset with 0% weight has exactly 0.0 risk contribution and 0.0% percentage contribution."""
        weights = np.array([0.7, 0.3, 0.0])
        cov = np.array([
            [0.04, 0.01, 0.01],
            [0.01, 0.09, 0.02],
            [0.01, 0.02, 0.16],
        ])
        names = ["Active1", "Active2", "ZeroWeight"]

        res = compute_risk_budgeting(weights, cov, names)
        assert res["risk_contributions"]["ZeroWeight"] == 0.0
        assert res["percentage_risk_contributions"]["ZeroWeight"] == 0.0
        assert math.isclose(sum(res["percentage_risk_contributions"].values()), 100.0, abs_tol=1e-3)

    def test_dict_weights_input_convenience(self):
        """compute_risk_budgeting accepts dict of weights directly."""
        w_dict = {"Gold": 0.3, "Equity": 0.7}
        cov = np.diag([0.02, 0.05])
        res = compute_risk_budgeting(w_dict, cov)

        assert "Gold" in res["risk_contributions"]
        assert "Equity" in res["risk_contributions"]
        assert math.isclose(sum(res["risk_contributions"].values()), res["portfolio_volatility"], abs_tol=1e-5)
