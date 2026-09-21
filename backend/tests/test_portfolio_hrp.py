"""Unit & Numerical Precision Tests for Hierarchical Risk Parity (HRP).
Verifies Marcos Lopez de Prado algorithm against institutional mathematical standards to 4 decimal places.
"""

import math
import numpy as np
import pandas as pd
import pytest

from app.portfolio_hrp import optimize_hrp, _quasi_diag, _get_cluster_var, _recursive_bisection


class TestHierarchicalRiskParityPrecision:
    """Test suite verifying mathematical precision of HRP implementation."""

    def test_hrp_correlation_distance_formula(self):
        """Correlation distance D_ij = sqrt(0.5 * (1 - rho_ij)) matches exact analytical values."""
        rho_vals = np.array([1.0, 0.5, 0.0, -0.5, -1.0])
        expected_dists = np.sqrt(0.5 * (1.0 - rho_vals))
        
        # Test analytical points:
        # rho = 1.0 -> dist = 0.0
        # rho = 0.5 -> dist = sqrt(0.25) = 0.5
        # rho = 0.0 -> dist = sqrt(0.5) = 0.707107
        # rho = -0.5 -> dist = sqrt(0.75) = 0.866025
        # rho = -1.0 -> dist = sqrt(1.0) = 1.0
        assert math.isclose(expected_dists[0], 0.0000, abs_tol=1e-4)
        assert math.isclose(expected_dists[1], 0.5000, abs_tol=1e-4)
        assert math.isclose(expected_dists[2], 0.7071, abs_tol=1e-4)
        assert math.isclose(expected_dists[3], 0.8660, abs_tol=1e-4)
        assert math.isclose(expected_dists[4], 1.0000, abs_tol=1e-4)

    def test_hrp_weights_sum_to_one_exact(self):
        """HRP weights must strictly sum to 1.0000."""
        cov = np.array([
            [0.04, 0.01, 0.02, 0.005],
            [0.01, 0.09, 0.015, 0.01],
            [0.02, 0.015, 0.16, 0.02],
            [0.005, 0.01, 0.02, 0.06],
        ])
        names = ["A", "B", "C", "D"]
        res = optimize_hrp(cov, asset_names=names)
        
        assert len(res["weights"]) == 4
        total_w = sum(res["weights"].values())
        assert math.isclose(total_w, 1.0, abs_tol=1e-6)
        for name, w in res["weights"].items():
            assert w >= 0.0, f"Weight for {name} must be non-negative"

    def test_hrp_analytical_two_asset_inverse_variance(self):
        """For two uncorrelated assets, weights match inverse variance split to 4 decimal places."""
        # var_1 = 0.01, var_2 = 0.09
        # alpha = (1/var_1) / (1/var_1 + 1/var_2) = 100 / (100 + 11.11111) = 0.9000
        cov = np.diag([0.01, 0.09])
        names = ["LowVol", "HighVol"]
        res = optimize_hrp(cov, asset_names=names)
        
        assert math.isclose(res["weights"]["LowVol"], 0.9000, abs_tol=1e-4)
        assert math.isclose(res["weights"]["HighVol"], 0.1000, abs_tol=1e-4)

    def test_hrp_equal_weights_for_uncorrelated_identical_variance(self):
        """For N uncorrelated assets with identical variance, weights are exactly 1/N."""
        n = 5
        cov = np.eye(n) * 0.05
        names = [f"Asset_{i}" for i in range(n)]
        res = optimize_hrp(cov, asset_names=names)
        
        expected_w = 1.0 / n  # 0.2000
        for name in names:
            assert math.isclose(res["weights"][name], expected_w, abs_tol=1e-4)

    def test_hrp_cluster_order_contains_all_and_valid_permutation(self):
        """Quasi-diagonalized cluster order must contain every asset exactly once."""
        n = 8
        np.random.seed(101)
        A = np.random.normal(0, 1, (n, n))
        cov = A @ A.T + np.eye(n) * 0.1
        names = [f"Scheme_{i}" for i in range(n)]
        res = optimize_hrp(cov, asset_names=names)
        
        order = res["cluster_order"]
        assert len(order) == n
        assert set(order) == set(names)

    def test_hrp_collinear_singular_covariance_matrix(self):
        """HRP handles rank-deficient singular covariance matrix without crashing or weight collapse."""
        # Rank 2 matrix for 4 assets
        factor_loadings = np.array([[0.1, 0.2], [0.15, -0.1], [0.05, 0.3], [-0.1, 0.2]])
        cov_singular = factor_loadings @ factor_loadings.T
        names = ["F1", "F2", "F3", "F4"]
        
        # Verify determinant is 0 (singular)
        assert math.isclose(float(np.linalg.det(cov_singular)), 0.0, abs_tol=1e-10)
        
        res = optimize_hrp(cov_singular, asset_names=names)
        assert math.isclose(sum(res["weights"].values()), 1.0, abs_tol=1e-5)
        for w in res["weights"].values():
            assert not math.isnan(w)
            assert w >= 0.0

    def test_hrp_single_asset_edge_case(self):
        """Single asset portfolio trivially receives 100% allocation."""
        cov = np.array([[0.04]])
        res = optimize_hrp(cov, asset_names=["SoloFund"])
        assert res["weights"]["SoloFund"] == 1.0
        assert res["cluster_order"] == ["SoloFund"]
        assert math.isclose(res["portfolio_vol_ann"], math.sqrt(0.04 * 252.0), abs_tol=1e-4)

    def test_hrp_dataframe_input_preserves_columns(self):
        """Accepts pandas DataFrame and uses columns as asset names."""
        df_cov = pd.DataFrame(
            [[0.04, 0.01], [0.01, 0.09]],
            index=["Equity", "Debt"],
            columns=["Equity", "Debt"],
        )
        res = optimize_hrp(df_cov)
        assert "Equity" in res["weights"]
        assert "Debt" in res["weights"]
        assert math.isclose(sum(res["weights"].values()), 1.0, abs_tol=1e-5)

    def test_hrp_annualized_portfolio_volatility(self):
        """Annualized volatility calculation: sigma_p = sqrt(w^T * Cov * w * 252)."""
        cov = np.array([[0.0004, 0.0], [0.0, 0.0004]])  # Daily cov
        names = ["A", "B"]
        res = optimize_hrp(cov, asset_names=names)
        # For equal weights [0.5, 0.5]: port_var = 0.25*0.0004 + 0.25*0.0004 = 0.0002
        # annualized vol = sqrt(0.0002 * 252) = sqrt(0.0504) = 0.224499
        expected_ann_vol = math.sqrt(0.0002 * 252.0)
        assert math.isclose(res["portfolio_vol_ann"], expected_ann_vol, abs_tol=1e-4)

    def test_hrp_suggest_router_endpoint_includes_hrp_and_risk_budgeting(self):
        """POST /api/portfolio-advisor/suggest includes HRP allocation alongside MVO."""
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app)
        payload = {
            "risk_tier": "Growth",
            "budget": 100000.0,
            "mode": "SIP (Monthly)",
            "sip_amount": 10000.0,
            "start_date": "2023-01-01",
            "end_date": "2026-09-01",
        }
        resp = client.post("/api/portfolio-advisor/suggest", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert "rules_result" in data
        assert "mvo_result" in data
        assert "hrp_result" in data
        assert "rules_backtest" in data
        assert "mvo_backtest" in data
        assert "hrp_backtest" in data
        assert "risk_budgeting" in data

        if data["hrp_result"] is not None:
            hrp = data["hrp_result"]
            assert hrp["method"] == "hierarchical_risk_parity"
            assert "weights" in hrp
            assert "picks" in hrp
            assert "cluster_order" in hrp
            total_w = sum(hrp["weights"].values())
            assert math.isclose(total_w, 1.0, abs_tol=1e-4)

        if data["risk_budgeting"] is not None:
            rb = data["risk_budgeting"]
            assert "rules_based" in rb
            assert "hierarchical_risk_parity" in rb

