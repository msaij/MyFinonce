"""Empirical Adversarial Test Suite for Hierarchical Risk Parity (HRP).

Stress tests Marcos Lopez de Prado's HRP implementation against:
1. Rank-deficient and singular covariance matrices (e.g., 5 assets with rank 2, rank 1, zero matrix).
2. Identical 100% correlated assets (rho = 1.0).
3. Highly negative correlation pairs (rho = -0.99, rho = -1.0).
4. Extreme variance disparities (sigma1 = 0.001, sigma2 = 1000.0, ratio 10^12).
5. Boundary case N = 1 asset.
6. High-dimensional asset universes (N = 50, N = 100).
7. Monte Carlo 1,000 trials on random covariance matrices verifying:
   - Weights strictly sum to 1.000000 (+/- 1e-6)
   - All weights w_i >= 0
   - No NaN or Inf in weights or risk metrics
   - HRP produces lower or comparable out-of-sample variance than equal weighting under clustered risk.
"""

import math
import numpy as np
import pandas as pd
import pytest

from app.portfolio_hrp import optimize_hrp, _quasi_diag, _get_cluster_var, _recursive_bisection


class TestAdversarialSingularAndRankDeficient:
    """Stress tests on rank-deficient, collinear, and degenerate covariance matrices."""

    def test_rank_2_five_assets_singular(self):
        """5 assets driven by 2 latent factors: covariance is rank 2, determinant is exactly 0."""
        rng = np.random.RandomState(42)
        factor_loadings = rng.randn(5, 2)
        cov_rank2 = factor_loadings @ factor_loadings.T
        names = [f"Asset_{i}" for i in range(5)]

        # Verify singularity
        det = float(np.linalg.det(cov_rank2))
        assert math.isclose(det, 0.0, abs_tol=1e-8)
        rank = np.linalg.matrix_rank(cov_rank2)
        assert rank <= 2

        res = optimize_hrp(cov_rank2, asset_names=names)
        weights = res["weights"]
        assert len(weights) == 5
        assert math.isclose(sum(weights.values()), 1.0, abs_tol=1e-6)
        for name, w in weights.items():
            assert not math.isnan(w)
            assert not math.isinf(w)
            assert w >= 0.0
        assert not math.isnan(res["portfolio_vol_ann"])
        assert not math.isinf(res["portfolio_vol_ann"])
        assert len(res["cluster_order"]) == 5

    def test_rank_1_ten_assets_extreme_deficiency(self):
        """10 assets driven by a single common factor: covariance is rank 1."""
        rng = np.random.RandomState(101)
        f = rng.randn(10, 1) + 1.0  # avoid zero loadings
        cov_rank1 = f @ f.T
        names = [f"F_{i}" for i in range(10)]

        res = optimize_hrp(cov_rank1, asset_names=names)
        weights = res["weights"]
        assert len(weights) == 10
        assert math.isclose(sum(weights.values()), 1.0, abs_tol=1e-6)
        for w in weights.values():
            assert not math.isnan(w) and w >= 0.0
        assert not math.isnan(res["portfolio_vol_ann"])

    def test_all_zeros_degenerate_covariance(self):
        """Zero covariance matrix: zero volatility across all assets, no divide-by-zero crashes."""
        cov_zero = np.zeros((4, 4))
        res = optimize_hrp(cov_zero)
        weights = res["weights"]
        assert math.isclose(sum(weights.values()), 1.0, abs_tol=1e-6)
        # Should gracefully allocate uniform weights 1/4 = 0.25
        for w in weights.values():
            assert math.isclose(w, 0.25, abs_tol=1e-5)
        assert res["portfolio_vol_ann"] == 0.0

    def test_duplicate_identical_assets_perfect_collinearity(self):
        """Universe containing 2 distinct assets and 2 duplicate copies with identical returns."""
        # Assets 0 and 1 are distinct. Asset 2 is duplicate of Asset 0, Asset 3 is duplicate of Asset 1.
        cov_4 = np.array([
            [0.04, 0.01, 0.04, 0.01],
            [0.01, 0.09, 0.01, 0.09],
            [0.04, 0.01, 0.04, 0.01],
            [0.01, 0.09, 0.01, 0.09],
        ])
        names = ["A", "B", "A_dup", "B_dup"]
        res = optimize_hrp(cov_4, asset_names=names)
        weights = res["weights"]

        assert math.isclose(sum(weights.values()), 1.0, abs_tol=1e-6)
        for w in weights.values():
            assert not math.isnan(w) and w >= 0.0

        # Cluster A + A_dup should receive roughly same total risk budget as A alone
        w_A_group = weights["A"] + weights["A_dup"]
        w_B_group = weights["B"] + weights["B_dup"]
        assert math.isclose(w_A_group + w_B_group, 1.0, abs_tol=1e-6)
        # Low volatility group A (0.04) should receive higher weight than high vol group B (0.09)
        assert w_A_group > w_B_group


class TestAdversarialExtremeCorrelations:
    """Stress tests on extreme correlation boundaries: rho = 1.0, rho = -0.99, rho = -1.0."""

    def test_identical_perfectly_correlated_assets_rho_one(self):
        """N assets with rho = 1.0 and equal variance: HRP assigns equal weights 1/N."""
        n = 4
        cov = np.ones((n, n)) * 0.04
        names = [f"Clone_{i}" for i in range(n)]
        res = optimize_hrp(cov, asset_names=names)

        weights = res["weights"]
        assert math.isclose(sum(weights.values()), 1.0, abs_tol=1e-6)
        for w in weights.values():
            assert math.isclose(w, 1.0 / n, abs_tol=1e-5)

    def test_highly_negative_correlation_pair_rho_minus_99(self):
        """Two assets with rho = -0.99. Weights must be strictly non-negative, summing to 1.0."""
        sigma1, sigma2 = 0.20, 0.30
        rho = -0.99
        cov = np.array([
            [sigma1**2, rho * sigma1 * sigma2],
            [rho * sigma1 * sigma2, sigma2**2],
        ])
        names = ["AssetNeg1", "AssetNeg2"]
        res = optimize_hrp(cov, asset_names=names)

        weights = res["weights"]
        assert math.isclose(sum(weights.values()), 1.0, abs_tol=1e-6)
        assert weights["AssetNeg1"] >= 0.0
        assert weights["AssetNeg2"] >= 0.0
        # Lower variance asset AssetNeg1 (sigma=0.2) should receive higher weight than AssetNeg2 (sigma=0.3)
        assert weights["AssetNeg1"] > weights["AssetNeg2"]
        # Inverse variance ratio: 1/0.04 = 25, 1/0.09 = 11.111 -> w1 = 25/36.111 = 0.692308
        assert math.isclose(weights["AssetNeg1"], 0.692308, abs_tol=1e-4)
        assert math.isclose(weights["AssetNeg2"], 0.307692, abs_tol=1e-4)

    def test_extreme_negative_correlation_boundary_rho_minus_one(self):
        """Exact rho = -1.0 boundary: distance D = sqrt(0.5*(1 - (-1))) = 1.0."""
        sigma1, sigma2 = 0.15, 0.25
        rho = -1.0
        cov = np.array([
            [sigma1**2, rho * sigma1 * sigma2],
            [rho * sigma1 * sigma2, sigma2**2],
        ])
        res = optimize_hrp(cov)
        weights = res["weights"]
        assert math.isclose(sum(weights.values()), 1.0, abs_tol=1e-6)
        for w in weights.values():
            assert not math.isnan(w) and w >= 0.0
        assert not math.isnan(res["portfolio_vol_ann"])


class TestAdversarialExtremeVarianceDisparities:
    """Stress tests on huge variance spans (1e-6 to 1e6) and near-zero variances."""

    def test_extreme_variance_disparity_1e3_vs_1e_minus_3(self):
        """sigma1 = 0.001, sigma2 = 1000.0 (variance ratio 10^12).

        Low-volatility asset receives essentially 100% allocation without underflow or overflow.
        """
        cov = np.diag([0.001**2, 1000.0**2])
        names = ["MicroVol", "MacroVol"]
        res = optimize_hrp(cov, asset_names=names)

        weights = res["weights"]
        assert math.isclose(sum(weights.values()), 1.0, abs_tol=1e-6)
        assert math.isclose(weights["MicroVol"], 1.0, abs_tol=1e-5)
        assert math.isclose(weights["MacroVol"], 0.0, abs_tol=1e-5)
        assert not math.isnan(res["portfolio_vol_ann"])
        assert not math.isinf(res["portfolio_vol_ann"])

    def test_micro_and_macro_variances_multiple_assets(self):
        """4 assets with variances spanning 12 orders of magnitude: 1e-8, 1e-4, 1.0, 1e4."""
        cov = np.diag([1e-8, 1e-4, 1.0, 1e4])
        names = ["V1", "V2", "V3", "V4"]
        res = optimize_hrp(cov, asset_names=names)

        weights = res["weights"]
        assert math.isclose(sum(weights.values()), 1.0, abs_tol=1e-6)
        for w in weights.values():
            assert not math.isnan(w) and w >= 0.0
        # Lower variance assets receive strictly higher weights (down to 6-decimal precision rounding)
        assert weights["V1"] > weights["V2"]
        assert weights["V2"] >= weights["V3"] >= weights["V4"]
        assert weights["V1"] > 0.99
        assert weights["V4"] == 0.0

    def test_zero_variance_asset_alongside_risky_assets(self):
        """Risk-free asset (zero variance) alongside volatile equity assets."""
        cov = np.diag([0.0, 0.04, 0.09])
        names = ["Cash_ZeroVol", "Equity_Mid", "Equity_High"]
        res = optimize_hrp(cov, asset_names=names)

        weights = res["weights"]
        assert math.isclose(sum(weights.values()), 1.0, abs_tol=1e-6)
        # Cash with zero variance dominates inverse-variance allocation
        assert math.isclose(weights["Cash_ZeroVol"], 1.0, abs_tol=1e-4)
        assert math.isclose(weights["Equity_Mid"], 0.0, abs_tol=1e-4)
        assert math.isclose(weights["Equity_High"], 0.0, abs_tol=1e-4)
        assert math.isclose(res["portfolio_vol_ann"], 0.0, abs_tol=1e-4)


class TestAdversarialBoundaryDimensions:
    """Stress tests on boundary dimensions: N = 1, N = 50, N = 100."""

    def test_boundary_n_equals_one(self):
        """N = 1 asset universe: trivially receives 100% allocation."""
        cov = np.array([[0.04]])
        res = optimize_hrp(cov, asset_names=["Solo"])
        assert res["weights"] == {"Solo": 1.0}
        assert res["cluster_order"] == ["Solo"]
        assert math.isclose(res["portfolio_vol_ann"], math.sqrt(0.04 * 252.0), abs_tol=1e-4)

    def test_high_dimensional_universe_n_50(self):
        """High-dimensional asset universe N = 50 with low-rank factor structure + noise."""
        n = 50
        rng = np.random.RandomState(50)
        A = rng.randn(n, 10)
        cov = A @ A.T + np.eye(n) * 0.05
        names = [f"Fund_{i}" for i in range(n)]

        res = optimize_hrp(cov, asset_names=names)
        weights = res["weights"]
        assert len(weights) == n
        assert math.isclose(sum(weights.values()), 1.0, abs_tol=1e-6)
        for w in weights.values():
            assert not math.isnan(w) and w >= 0.0
        assert len(res["cluster_order"]) == n
        assert set(res["cluster_order"]) == set(names)
        assert not math.isnan(res["portfolio_vol_ann"])

    def test_high_dimensional_universe_n_100(self):
        """High-dimensional asset universe N = 100."""
        n = 100
        rng = np.random.RandomState(100)
        A = rng.randn(n, 15)
        cov = A @ A.T + np.eye(n) * 0.02
        names = [f"Scheme_{i}" for i in range(n)]

        res = optimize_hrp(cov, asset_names=names)
        weights = res["weights"]
        assert len(weights) == n
        assert math.isclose(sum(weights.values()), 1.0, abs_tol=1e-6)
        for w in weights.values():
            assert not math.isnan(w) and w >= 0.0
        assert len(res["cluster_order"]) == n
        assert set(res["cluster_order"]) == set(names)
        assert not math.isnan(res["portfolio_vol_ann"])

    def test_odd_dimensions_bisection_trees(self):
        """Tests tree bisection on prime and odd dimensions N = 3, 7, 13, 27."""
        rng = np.random.RandomState(777)
        for n in [3, 7, 13, 27]:
            A = rng.randn(n, n)
            cov = A @ A.T + np.eye(n) * 0.01
            names = [f"Odd_{i}" for i in range(n)]
            res = optimize_hrp(cov, asset_names=names)
            assert len(res["weights"]) == n
            assert math.isclose(sum(res["weights"].values()), 1.0, abs_tol=1e-6)
            for w in res["weights"].values():
                assert not math.isnan(w) and w >= 0.0
            assert len(res["cluster_order"]) == n


class TestMonteCarloStress1000Trials:
    """1,000-Trial Monte Carlo verification harness across random covariance regimes."""

    def test_monte_carlo_1000_trials(self):
        """Executes 1,000 randomized Monte Carlo trials verifying:

        1. Weights strictly sum to 1.000000 (+/- 1e-6)
        2. All weights w_i >= 0
        3. No NaN or Inf in weights or risk metrics
        4. HRP produces lower or comparable out-of-sample variance than equal weighting under clustered risk
        """
        rng = np.random.RandomState(42)
        n_trials = 1000

        violations_sum = 0
        violations_nonneg = 0
        violations_nan = 0
        clustered_risk_trials = 0
        hrp_lower_or_comparable_count = 0
        hrp_strictly_lower_count = 0
        hrp_var_sum = 0.0
        ew_var_sum = 0.0

        for _ in range(n_trials):
            n = rng.randint(3, 26)
            p_regime = rng.rand()

            if p_regime < 0.50:
                # Regime 1: Clustered risk structure (Lopez de Prado benchmark)
                clustered_risk_trials += 1
                n_clusters = rng.randint(2, min(5, n))
                cluster_assignments = rng.randint(0, n_clusters, size=n)
                for c in range(n_clusters):
                    cluster_assignments[c] = c

                cluster_vols = rng.uniform(0.05, 0.40, size=n_clusters)
                asset_vols = cluster_vols[cluster_assignments] * rng.uniform(0.85, 1.15, size=n)

                corr = np.zeros((n, n))
                for i in range(n):
                    for j in range(n):
                        if i == j:
                            corr[i, j] = 1.0
                        elif cluster_assignments[i] == cluster_assignments[j]:
                            corr[i, j] = rng.uniform(0.65, 0.95)
                        else:
                            corr[i, j] = rng.uniform(-0.10, 0.20)
                corr = (corr + corr.T) / 2.0
                np.fill_diagonal(corr, 1.0)

                # Ensure PSD
                eigvals, eigvecs = np.linalg.eigh(corr)
                eigvals = np.maximum(eigvals, 1e-4)
                corr = eigvecs @ np.diag(eigvals) @ eigvecs.T
                d_inv = 1.0 / np.sqrt(np.diag(corr))
                corr = np.outer(d_inv, d_inv) * corr
                np.fill_diagonal(corr, 1.0)

                cov_true = np.outer(asset_vols, asset_vols) * corr

                # Sample empirical observations (noisy sample covariance)
                T = rng.randint(150, 400)
                rets = rng.multivariate_normal(mean=np.zeros(n), cov=cov_true, size=T)
                cov_sample = np.cov(rets, rowvar=False)

                res = optimize_hrp(cov_sample)
                w_hrp = np.array([res["weights"][f"Asset_{i}"] for i in range(n)])
                w_ew = np.ones(n) / n

                # Realized out-of-sample portfolio variance against true covariance
                var_hrp = float(w_hrp @ cov_true @ w_hrp)
                var_ew = float(w_ew @ cov_true @ w_ew)

                hrp_var_sum += var_hrp
                ew_var_sum += var_ew

                if var_hrp <= var_ew:
                    hrp_strictly_lower_count += 1
                # Comparable definition: var_hrp <= var_ew * 1.05 (within 5%)
                if var_hrp <= var_ew * 1.05:
                    hrp_lower_or_comparable_count += 1

            elif p_regime < 0.80:
                # Regime 2: Rank-deficient / collinear covariance
                rank = rng.randint(1, max(2, n // 2))
                F = rng.randn(n, rank)
                cov_sample = F @ F.T
                res = optimize_hrp(cov_sample)

            else:
                # Regime 3: Extreme variance disparity
                A = rng.randn(n, n)
                raw_cov = A @ A.T
                scale = 10.0 ** rng.uniform(-4, 4, size=n)
                cov_sample = raw_cov * np.outer(scale, scale)
                res = optimize_hrp(cov_sample)

            # Invariant checks across all trials
            w_vals = list(res["weights"].values())
            w_sum = sum(w_vals)

            if not math.isclose(w_sum, 1.0, abs_tol=1e-6):
                violations_sum += 1

            if any(w < 0.0 for w in w_vals):
                violations_nonneg += 1

            vol = res["portfolio_vol_ann"]
            if any(math.isnan(w) or math.isinf(w) for w in w_vals) or math.isnan(vol) or math.isinf(vol):
                violations_nan += 1

        # Assert zero invariant violations across 1,000 trials
        assert violations_sum == 0, f"Weight sum violations: {violations_sum}/1000"
        assert violations_nonneg == 0, f"Negative weight violations: {violations_nonneg}/1000"
        assert violations_nan == 0, f"NaN/Inf violations: {violations_nan}/1000"

        # Assert HRP superiority / comparability under clustered risk
        assert clustered_risk_trials > 400, f"Expected >400 clustered trials, got {clustered_risk_trials}"
        comparable_rate = hrp_lower_or_comparable_count / clustered_risk_trials
        strictly_lower_rate = hrp_strictly_lower_count / clustered_risk_trials
        mean_hrp_var = hrp_var_sum / clustered_risk_trials
        mean_ew_var = ew_var_sum / clustered_risk_trials

        # Under clustered risk, HRP must achieve lower average out-of-sample variance
        assert mean_hrp_var < mean_ew_var, (
            f"Mean HRP variance ({mean_hrp_var:.6f}) should be lower than EW variance ({mean_ew_var:.6f})"
        )
        # HRP must be lower or comparable in >= 95% of clustered risk trials
        assert comparable_rate >= 0.95, (
            f"HRP lower/comparable rate ({comparable_rate:.2%}) below required 95.0% threshold"
        )
        # HRP must be strictly lower in >= 90% of clustered risk trials
        assert strictly_lower_rate >= 0.90, (
            f"HRP strictly lower rate ({strictly_lower_rate:.2%}) below required 90.0% threshold"
        )
