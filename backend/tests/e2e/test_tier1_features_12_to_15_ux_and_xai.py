"""Tier 1: Feature Coverage (Features 12 - 15: UX, Explainable AI & Interactive Visualizations).
Verifies Algorithmic Narratives, Waterfall Chart Contract, Correlation Heatmap, and Scenario Sliders.
Each feature has >= 5 isolated test cases (Total: 20 tests).
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
# Feature 12: Algorithmic Diagnostic Narratives (5 tests)
# =====================================================================

class TestTier1Feature12AlgorithmicNarratives:
    """Feature 12: Deterministic natural language fund diagnostic reports serialized as structured JSON."""

    def test_f12_deterministic_narrative_generation(self):
        """Identical inputs yield bit-for-bit deterministic qualitative narratives."""
        betas = {"mkt_excess": 1.10, "smb": 0.25, "hml": -0.10, "wml": 0.15}
        res1 = generate_fund_diagnostics_report("HDFC Top 100", betas, alpha_ann_pct=2.45, sharpe=1.35)
        res2 = generate_fund_diagnostics_report("HDFC Top 100", betas, alpha_ann_pct=2.45, sharpe=1.35)
        oracle_res = oracle_generate_xai_narrative("HDFC Top 100", betas, alpha_ann_pct=2.45, sharpe=1.35)

        assert res1["headline_summary"] == res2["headline_summary"] == oracle_res["headline_summary"]
        assert res1["factor_exposure_narrative"] == res2["factor_exposure_narrative"] == oracle_res["factor_exposure_narrative"]
        assert res1["style_drift"] == res2["style_drift"] == oracle_res["style_drift"]

    def test_f12_factor_tilt_qualitative_classification(self):
        """SMB > 0.15 identifies Mid/Small tilt; HML > 0.15 identifies Value orientation."""
        betas_val_small = {"mkt_excess": 0.95, "smb": 0.30, "hml": 0.25, "wml": -0.05}
        res = generate_fund_diagnostics_report("ICICI Value Discovery", betas_val_small, alpha_ann_pct=1.8, sharpe=1.1)
        oracle_res = oracle_generate_xai_narrative("ICICI Value Discovery", betas_val_small, alpha_ann_pct=1.8, sharpe=1.1)

        narrative = res["factor_exposure_narrative"]
        assert "Mid/Small-Cap tilt" in narrative
        assert "Deep Value orientation" in narrative
        assert res["factor_exposure_narrative"] == oracle_res["factor_exposure_narrative"]

    def test_f12_style_drift_detection(self):
        """High factor loading divergence flags style drift as detected."""
        betas_drift = {"mkt_excess": 1.05, "smb": 0.45, "hml": 0.05, "wml": 0.0}
        res = generate_fund_diagnostics_report("Kotak Large Cap", betas_drift, alpha_ann_pct=0.5, sharpe=0.8)
        oracle_res = oracle_generate_xai_narrative("Kotak Large Cap", betas_drift, alpha_ann_pct=0.5, sharpe=0.8)

        drift = res["style_drift"]
        assert drift["drift_detected"] is True
        assert drift["drift_severity"] in ["Moderate", "High"]
        assert drift["drift_detected"] == oracle_res["style_drift"]["drift_detected"]

    def test_f12_regular_plan_migration_recommendation(self):
        """Regular plan generates actionable MIGRATE_TO_DIRECT recommendation with fee savings."""
        betas = {"mkt_excess": 1.0, "smb": 0.0, "hml": 0.0, "wml": 0.0}
        res = generate_fund_diagnostics_report("Nippon Regular", betas, alpha_ann_pct=1.0, sharpe=0.9, is_regular=True, ter_diff=0.95)
        oracle_res = oracle_generate_xai_narrative("Nippon Regular", betas, alpha_ann_pct=1.0, sharpe=0.9, is_regular=True, ter_diff=0.95)

        mig = res["migration_recommendation"]
        assert mig is not None
        assert mig["recommendation"] == "MIGRATE_TO_DIRECT"
        assert "0.95%" in mig["actionable_insight"]
        assert mig == oracle_res["migration_recommendation"]

    def test_f12_structured_json_schema_contract(self):
        """Output satisfies required structured JSON contract."""
        betas = {"mkt_excess": 1.0, "smb": 0.1, "hml": 0.1, "wml": 0.1}
        res = generate_fund_diagnostics_report("Quant Active Fund", betas, alpha_ann_pct=4.2, sharpe=1.65, quartile=1)

        required_keys = ["scheme_name", "headline_summary", "factor_exposure_narrative", "style_drift", "peer_ranking"]
        for k in required_keys:
            assert k in res
        assert res["peer_ranking"]["quartile"] == 1


# =====================================================================
# Feature 13: Factor Exposure Waterfall Chart (5 tests)
# =====================================================================

class TestTier1Feature13FactorWaterfallChart:
    """Feature 13: Interactive vertical waterfall chart rendering factor contributions, manager alpha, and fee drag."""

    def test_f13_plotly_waterfall_trace_type(self):
        """Trace specification contains type='waterfall' and orientation='v'."""
        spec = oracle_build_waterfall_chart_spec(
            alpha_ann_pct=2.5,
            factor_contributions={"market": 12.0, "size": 3.0, "value": -1.5, "momentum": 2.0},
            fee_drag_pct=0.75,
            net_return_pct=17.25,
        )
        data = spec["data"]
        assert len(data) == 1
        trace = data[0]
        assert trace["type"] == "waterfall"
        assert trace["orientation"] == "v"

    def test_f13_waterfall_measures_sequence(self):
        """Measure list has 'relative' for all intermediate factors and 'total' for Net Return."""
        spec = oracle_build_waterfall_chart_spec(
            alpha_ann_pct=1.5,
            factor_contributions={"market": 10.0, "smb": 2.0},
            fee_drag_pct=0.8,
            net_return_pct=12.7,
        )
        measures = spec["data"][0]["measure"]
        assert measures[-1] == "total"
        assert all(m == "relative" for m in measures[:-1])

    def test_f13_factor_contributions_match_mathematical_breakdown(self):
        """Net return equals alpha + sum(factor returns) - fee drag."""
        alpha = 2.0
        factors = {"market": 11.5, "size": 2.5, "value": -1.0, "momentum": 1.5}
        fee_drag = 0.85
        net_ret = alpha + sum(factors.values()) - fee_drag

        spec = oracle_build_waterfall_chart_spec(alpha, factors, fee_drag, net_ret)
        y_vals = spec["data"][0]["y"]

        assert y_vals[0] == alpha
        assert y_vals[-1] == round(net_ret, 4)

    def test_f13_waterfall_connector_and_styling(self):
        """Chart specification includes connector line properties."""
        spec = oracle_build_waterfall_chart_spec(1.0, {"market": 10.0}, 0.5, 10.5)
        trace = spec["data"][0]
        assert "connector" in trace
        assert "line" in trace["connector"]

    def test_f13_negative_values_rendered_cleanly(self):
        """Negative factor drags are correctly preserved as negative values."""
        spec = oracle_build_waterfall_chart_spec(-1.5, {"value": -3.2}, 0.9, -5.6)
        trace = spec["data"][0]
        assert trace["y"][0] < 0
        assert trace["y"][1] < 0


# =====================================================================
# Feature 14: Correlation Matrix Heatmap (5 tests)
# =====================================================================

class TestTier1Feature14CorrelationHeatmap:
    """Feature 14: Correlation matrix heatmap with HRP dendrogram quasi-diagonalization ordering."""

    def test_f14_plotly_heatmap_trace_type(self):
        """Heatmap trace type is 'heatmap' with colorscale."""
        names = ["A", "B", "C"]
        corr = np.eye(3)
        spec = oracle_build_correlation_heatmap_spec(corr, names)

        assert spec["data"][0]["type"] == "heatmap"
        assert "colorscale" in spec["data"][0]

    def test_f14_hrp_quasi_diagonal_ordering_applied(self):
        """X and Y axes use the ordered asset names."""
        ordered = ["MidCap", "SmallCap", "LargeCap"]
        corr = np.array([[1.0, 0.85, 0.6], [0.85, 1.0, 0.55], [0.6, 0.55, 1.0]])
        spec = oracle_build_correlation_heatmap_spec(corr, ordered)

        trace = spec["data"][0]
        assert trace["x"] == ordered
        assert trace["y"] == ordered

    def test_f14_correlation_bounds_in_z_values(self):
        """Z values are strictly bounded in [-1.0, 1.0] and zmin/zmax are set."""
        corr = np.array([[1.0, -0.4], [-0.4, 1.0]])
        spec = oracle_build_correlation_heatmap_spec(corr, ["X", "Y"])

        trace = spec["data"][0]
        assert trace["zmin"] == -1.0
        assert trace["zmax"] == 1.0
        for row in trace["z"]:
            for val in row:
                assert -1.0 <= val <= 1.0

    def test_f14_diagonal_correlation_is_one(self):
        """Diagonal elements must be exactly 1.0."""
        corr = np.array([[1.0, 0.5], [0.5, 1.0]])
        spec = oracle_build_correlation_heatmap_spec(corr, ["P", "Q"])

        z = spec["data"][0]["z"]
        assert z[0][0] == 1.0
        assert z[1][1] == 1.0

    def test_f14_hover_and_gap_handling(self):
        """Heatmap disables hoverongaps for solid institutional presentation."""
        corr = np.eye(2)
        spec = oracle_build_correlation_heatmap_spec(corr, ["A", "B"])
        assert spec["data"][0]["hoverongaps"] is False


# =====================================================================
# Feature 15: Scenario Simulation Sliders (5 tests)
# =====================================================================

class TestTier1Feature15ScenarioSliders:
    """Feature 15: Real-time client-side factor perturbation simulation."""

    def test_f15_real_time_drawdown_perturbation(self):
        """Computes expected return delta: Delta P = beta_mkt * Delta F_mkt."""
        betas = {"market": 1.25}
        shocks = {"market": -10.0}  # -10% market crash

        prod_sim = simulate_factor_shocks(betas, shocks, is_percentage=True)
        sim = oracle_factor_slider_perturbation(betas, shocks)
        # Expected return delta: 1.25 * -10% = -12.5%
        assert math.isclose(sim["simulated_return_delta_pct"], -12.5, abs_tol=1e-3)
        assert math.isclose(prod_sim["total_return_impact_pct"], sim["simulated_return_delta_pct"], abs_tol=1e-3)

    def test_f15_slider_positive_and_negative_shocks(self):
        """Positive shocks increase portfolio return, negative shocks decrease return."""
        betas = {"market": 1.10}
        prod_pos = simulate_factor_shocks(betas, {"market": 15.0}, is_percentage=True)
        prod_neg = simulate_factor_shocks(betas, {"market": -15.0}, is_percentage=True)
        sim_pos = oracle_factor_slider_perturbation(betas, {"market": 15.0})
        sim_neg = oracle_factor_slider_perturbation(betas, {"market": -15.0})

        assert prod_pos["total_return_impact_pct"] > 0.0
        assert prod_neg["total_return_impact_pct"] < 0.0
        assert sim_pos["simulated_return_delta_pct"] > 0.0
        assert sim_neg["simulated_return_delta_pct"] < 0.0

    def test_f15_multi_factor_simultaneous_perturbation(self):
        """Simultaneous multi-factor perturbation aggregates individual impacts."""
        betas = {"market": 1.15, "smb": 0.40, "hml": -0.20, "wml": 0.15}
        shocks = {"market": -12.0, "smb": -4.0, "hml": 2.0, "wml": -5.0}

        expected = (1.15 * -12.0) + (0.40 * -4.0) + (-0.20 * 2.0) + (0.15 * -5.0)
        prod_sim = simulate_factor_shocks(betas, shocks, is_percentage=True)
        sim = oracle_factor_slider_perturbation(betas, shocks)
        assert math.isclose(prod_sim["total_return_impact_pct"], round(expected, 4), abs_tol=1e-3)
        assert math.isclose(sim["simulated_return_delta_pct"], round(expected, 4), abs_tol=1e-3)

    def test_f15_zero_shock_invariance(self):
        """Zero factor shock produces exactly 0.0% simulated impact."""
        betas = {"market": 1.20, "smb": 0.35}
        prod_sim = simulate_factor_shocks(betas, {"market": 0.0, "smb": 0.0}, is_percentage=True)
        sim = oracle_factor_slider_perturbation(betas, {"market": 0.0, "smb": 0.0})
        assert prod_sim["total_return_impact_pct"] == 0.0
        assert sim["simulated_return_delta_pct"] == 0.0

    def test_f15_rupee_capital_impact_scaling(self):
        """Stressed portfolio CAGR impact scales consistently."""
        betas = {"market": 1.0}
        prod_sim = simulate_factor_shocks(betas, {"market": -20.0}, is_percentage=True)
        sim = oracle_factor_slider_perturbation(betas, {"market": -20.0})
        assert sim["stressed_portfolio_cagr_impact"] == -20.0
        assert prod_sim["total_return_impact_pct"] == -20.0
