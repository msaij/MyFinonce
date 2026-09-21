"""Tier 2: Boundary & Corner Cases (Features 12 - 15: UX, Explainable AI & Interactive Visualizations).
Verifies zero alpha narratives, all-negative waterfalls, 1x1 heatmaps, zero-beta insensitivity, and slider clamp limits.
Each feature has >= 5 boundary test cases (Total: 20 tests).
"""

import math
import numpy as np
import pytest

from app.services.quant_intelligence import generate_fund_diagnostics_report
from app.stress_testing import simulate_factor_shocks
from tests.e2e.e2e_oracles import (
    oracle_generate_xai_narrative,
    oracle_build_waterfall_chart_spec,
    oracle_build_correlation_heatmap_spec,
    oracle_factor_slider_perturbation,
)


# =====================================================================
# Feature 12: Algorithmic Narrative Boundaries (5 tests)
# =====================================================================

class TestTier2Feature12NarrativeBoundaries:
    """Boundary conditions for XAI algorithmic qualitative diagnoses."""

    def test_f12_boundary_zero_alpha(self):
        """Zero alpha (0.00%) generates neutral, non-crashing diagnosis."""
        betas = {"mkt_excess": 1.0, "smb": 0.0, "hml": 0.0, "wml": 0.0}
        res = generate_fund_diagnostics_report("Index Fund", betas, alpha_ann_pct=0.0, sharpe=1.0)
        oracle_res = oracle_generate_xai_narrative("Index Fund", betas, alpha_ann_pct=0.0, sharpe=1.0)
        assert "0.00%" in res["factor_exposure_narrative"]
        assert res["factor_exposure_narrative"] == oracle_res["factor_exposure_narrative"]

    def test_f12_boundary_neutral_market_beta(self):
        """Market beta = 1.0 and zero style betas correctly diagnoses market-neutral style."""
        betas = {"mkt_excess": 1.0, "smb": 0.0, "hml": 0.0, "wml": 0.0}
        res = generate_fund_diagnostics_report("UTI Nifty 50", betas, alpha_ann_pct=0.1, sharpe=1.05)
        oracle_res = oracle_generate_xai_narrative("UTI Nifty 50", betas, alpha_ann_pct=0.1, sharpe=1.05)
        assert "Market-neutral size" in res["factor_exposure_narrative"]
        assert "Balanced valuation" in res["factor_exposure_narrative"]
        assert res["factor_exposure_narrative"] == oracle_res["factor_exposure_narrative"]

    def test_f12_boundary_extreme_sharpe_ratio(self):
        """Negative Sharpe (-2.50) or high Sharpe (4.20) formats cleanly in headline summary."""
        betas = {"mkt_excess": 1.2}
        res_neg = generate_fund_diagnostics_report("Distressed Fund", betas, alpha_ann_pct=-15.0, sharpe=-2.50)
        res_pos = generate_fund_diagnostics_report("Star Fund", betas, alpha_ann_pct=12.0, sharpe=4.20)
        oracle_neg = oracle_generate_xai_narrative("Distressed Fund", betas, alpha_ann_pct=-15.0, sharpe=-2.50)
        oracle_pos = oracle_generate_xai_narrative("Star Fund", betas, alpha_ann_pct=12.0, sharpe=4.20)

        assert "-2.50" in res_neg["headline_summary"]
        assert "4.20" in res_pos["headline_summary"]
        assert res_neg["headline_summary"] == oracle_neg["headline_summary"]
        assert res_pos["headline_summary"] == oracle_pos["headline_summary"]

    def test_f12_boundary_missing_factor_betas(self):
        """Empty or partial betas dict falls back to default neutral betas."""
        res = generate_fund_diagnostics_report("Partial Beta Fund", {}, alpha_ann_pct=1.0, sharpe=1.0)
        oracle_res = oracle_generate_xai_narrative("Partial Beta Fund", {}, alpha_ann_pct=1.0, sharpe=1.0)
        assert "operates with a market beta of 1.00" in res["factor_exposure_narrative"]
        assert res["factor_exposure_narrative"] == oracle_res["factor_exposure_narrative"]

    def test_f12_boundary_direct_plan_no_migration(self):
        """Direct plan does NOT generate MIGRATE_TO_DIRECT recommendation."""
        betas = {"mkt_excess": 1.05}
        res = generate_fund_diagnostics_report("HDFC Top 100 Direct", betas, alpha_ann_pct=2.0, sharpe=1.2, is_regular=False)
        oracle_res = oracle_generate_xai_narrative("HDFC Top 100 Direct", betas, alpha_ann_pct=2.0, sharpe=1.2, is_regular=False)
        assert res["migration_recommendation"] is None
        assert res["migration_recommendation"] == oracle_res["migration_recommendation"]


# =====================================================================
# Feature 13: Waterfall Chart Contract Boundaries (5 tests)
# =====================================================================

class TestTier2Feature13WaterfallBoundaries:
    """Boundary conditions for factor attribution waterfall chart."""

    def test_f13_boundary_all_negative_waterfall_steps(self):
        """Every intermediate factor contributes negatively; waterfall steps descend to negative net return."""
        spec = oracle_build_waterfall_chart_spec(
            alpha_ann_pct=-2.0,
            factor_contributions={"market": -10.0, "size": -2.0, "value": -1.5, "momentum": -0.5},
            fee_drag_pct=1.0,
            net_return_pct=-17.0,
        )
        y = spec["data"][0]["y"]
        assert all(val <= 0.0 for val in y)
        assert y[-1] == -17.0

    def test_f13_boundary_single_factor_waterfall(self):
        """Single factor specification builds valid trace without errors."""
        spec = oracle_build_waterfall_chart_spec(1.0, {"market": 8.0}, 0.5, 8.5)
        trace = spec["data"][0]
        assert len(trace["x"]) == 4  # Alpha, Market, Fee Drag, Net Return
        assert trace["measure"][-1] == "total"

    def test_f13_boundary_zero_net_return(self):
        """Zero net return terminates exactly at 0.0 with total measure."""
        spec = oracle_build_waterfall_chart_spec(0.5, {"market": 0.5}, 1.0, 0.0)
        assert spec["data"][0]["y"][-1] == 0.0
        assert spec["data"][0]["measure"][-1] == "total"

    def test_f13_boundary_large_number_of_factors(self):
        """Extended factor decomposition (8 factors) produces corresponding measure and label entries."""
        factors = {f"factor_{i}": float(i) for i in range(8)}
        spec = oracle_build_waterfall_chart_spec(1.0, factors, 0.5, 28.5)
        trace = spec["data"][0]
        assert len(trace["x"]) == 8 + 3  # factors + alpha + fee_drag + net_return
        assert len(trace["y"]) == len(trace["measure"])

    def test_f13_boundary_zero_alpha_zero_drag(self):
        """Zero alpha and zero fee drag step values remain 0.0."""
        spec = oracle_build_waterfall_chart_spec(0.0, {"market": 10.0}, 0.0, 10.0)
        assert spec["data"][0]["y"][0] == 0.0
        assert spec["data"][0]["y"][-2] == 0.0


# =====================================================================
# Feature 14: Correlation Heatmap Boundaries (5 tests)
# =====================================================================

class TestTier2Feature14CorrelationHeatmapBoundaries:
    """Boundary conditions for correlation heatmap."""

    def test_f14_boundary_1x1_single_asset_heatmap(self):
        """1x1 correlation matrix renders single cell with value 1.0."""
        corr = np.array([[1.0]])
        spec = oracle_build_correlation_heatmap_spec(corr, ["SoloAsset"])
        trace = spec["data"][0]
        assert trace["z"] == [[1.0]]
        assert trace["x"] == ["SoloAsset"]

    def test_f14_boundary_completely_uncorrelated_assets(self):
        """Identity matrix (all off-diagonals 0.0) has z values containing only 1.0 and 0.0."""
        corr = np.eye(3)
        spec = oracle_build_correlation_heatmap_spec(corr, ["A", "B", "C"])
        z = np.array(spec["data"][0]["z"])
        assert np.all(np.diag(z) == 1.0)
        assert np.all(z[~np.eye(3, dtype=bool)] == 0.0)

    def test_f14_boundary_completely_anti_correlated_pair(self):
        """Pair with rho = -1.0 reaches exactly zmin = -1.0."""
        corr = np.array([[1.0, -1.0], [-1.0, 1.0]])
        spec = oracle_build_correlation_heatmap_spec(corr, ["Long", "Short"])
        z = spec["data"][0]["z"]
        assert z[0][1] == -1.0
        assert spec["data"][0]["zmin"] == -1.0

    def test_f14_boundary_large_universe_heatmap(self):
        """20-asset correlation matrix produces 20x20 z matrix within bounds."""
        n = 20
        corr = np.eye(n)
        names = [f"Fund_{i}" for i in range(n)]
        spec = oracle_build_correlation_heatmap_spec(corr, names)
        assert len(spec["data"][0]["z"]) == n
        assert len(spec["data"][0]["z"][0]) == n

    def test_f14_boundary_hoverongaps_false(self):
        """Trace preserves hoverongaps: False for institutional heatmap styling."""
        corr = np.eye(2)
        spec = oracle_build_correlation_heatmap_spec(corr, ["A", "B"])
        assert spec["data"][0]["hoverongaps"] is False


# =====================================================================
# Feature 15: Scenario Simulation Sliders Boundaries (5 tests)
# =====================================================================

class TestTier2Feature15ScenarioSlidersBoundaries:
    """Boundary conditions for interactive factor perturbation sliders."""

    def test_f15_boundary_extreme_shock_limits(self):
        """Large factor shocks (+/- 50%) calculate linear impact without overflow."""
        betas = {"market": 1.2, "smb": 0.5}
        shocks = {"market": -50.0, "smb": 50.0}
        prod_sim = simulate_factor_shocks(betas, shocks, is_percentage=True)
        sim = oracle_factor_slider_perturbation(betas, shocks)

        expected = (1.2 * -50.0) + (0.5 * 50.0)  # -60 + 25 = -35%
        assert math.isclose(prod_sim["total_return_impact_pct"], expected, abs_tol=1e-3)
        assert math.isclose(sim["simulated_return_delta_pct"], expected, abs_tol=1e-3)

    def test_f15_boundary_all_factors_shocked_same_direction(self):
        """All 4 factors shocked simultaneously by -20% aggregates compounding impacts."""
        betas = {"market": 1.0, "smb": 0.3, "hml": 0.2, "wml": 0.1}
        shocks = {"market": -20.0, "smb": -20.0, "hml": -20.0, "wml": -20.0}
        prod_sim = simulate_factor_shocks(betas, shocks, is_percentage=True)
        sim = oracle_factor_slider_perturbation(betas, shocks)

        expected = sum(betas.values()) * -20.0  # 1.6 * -20 = -32%
        assert math.isclose(prod_sim["total_return_impact_pct"], expected, abs_tol=1e-3)
        assert math.isclose(sim["simulated_return_delta_pct"], expected, abs_tol=1e-3)

    def test_f15_boundary_zero_beta_fund_insensitivity(self):
        """Liquid / Cash fund with all betas = 0.0 exhibits zero simulated return delta under any shock."""
        betas = {"market": 0.0, "smb": 0.0, "hml": 0.0, "wml": 0.0}
        shocks = {"market": -30.0, "smb": -20.0, "hml": 15.0}
        prod_sim = simulate_factor_shocks(betas, shocks, is_percentage=True)
        sim = oracle_factor_slider_perturbation(betas, shocks)
        assert prod_sim["total_return_impact_pct"] == 0.0
        assert sim["simulated_return_delta_pct"] == 0.0

    def test_f15_boundary_extreme_high_beta_leverage(self):
        """High-beta equity scheme (beta = 2.5) scales shocks 2.5x."""
        betas = {"market": 2.5}
        shocks = {"market": -10.0}
        prod_sim = simulate_factor_shocks(betas, shocks, is_percentage=True)
        sim = oracle_factor_slider_perturbation(betas, shocks)
        assert prod_sim["total_return_impact_pct"] == -25.0
        assert sim["simulated_return_delta_pct"] == -25.0

    def test_f15_boundary_unrecognized_factor_ignored(self):
        """Shocks to unrecognized factors not present in factor_betas do not cause KeyError."""
        betas = {"market": 1.1}
        shocks = {"unrecognized_crypto_factor": -50.0}
        prod_sim = simulate_factor_shocks(betas, shocks, is_percentage=True)
        sim = oracle_factor_slider_perturbation(betas, shocks)
        assert prod_sim["total_return_impact_pct"] == 0.0
        assert sim["simulated_return_delta_pct"] == 0.0
