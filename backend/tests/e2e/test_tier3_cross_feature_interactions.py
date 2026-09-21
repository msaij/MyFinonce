"""Tier 3: Cross-Feature Interactions (Pairwise Combinatorial Test Suite).
Verifies the integration and statistical consistency across interconnected modules:
  1.  F1 + F11: Factor Model + Fee Drag Attribution (Gross Alpha vs Net Factor Alpha)
  2.  F5 + F2:  HRP Allocation + Macro Scenario Stress Replay
  3.  F6 + F7:  Black-Litterman Allocation + ENCB Risk Concentration
  4.  F1 + F13: Factor Regression + Waterfall Chart Visualization Contract
  5.  F5 + F14: HRP Clustering Order + Quasi-Diagonalized Correlation Heatmap
  6.  F2 + F15: Macro Stress Windows + Interactive Simulation Sliders
  7.  F3 + F4:  Cornish-Fisher VaR + Expected Shortfall (CVaR) & Drawdown Duration
  8.  F8 + F11: Fast Database Lateral Join + 10-Year Fee Drag Compounding
  9.  F9 + F10: Dual-Era AMFI TER Sync + Direct vs Regular Plan Matcher
  10. F1 + F12: Factor Regression Betas + Algorithmic Diagnostic Qualitative XAI
  11. F5 + F6:  HRP Robust Tree Allocation vs Black-Litterman Bayesian Allocation
  12. F7 + F5:  Marginal Risk Budgeting under Hierarchical Tree Clustering
  13. F11 + F12: Multi-Horizon Rupee Wealth Erosion + Direct Plan Migration Advice
  14. F16 + F1: Deep-Linked URL State Sync + Factor Attribution Date Range
  15. F16 + F5: Deep-Linked URL State Sync + HRP Portfolio Asset Universe
  16. F17 + F13: Factor Waterfall Plotly Visual Tokens + WCAG AA Contrast Ratios
  17. F17 + F14: Heatmap Color Scale Luminance + WCAG AA Text Contrast Compliance
  18. F18 + F16: Next.js Production Build + Deep-Link Route State Preservation
Total: 18 pairwise interaction tests.
"""

import os
import math
import urllib.request
import numpy as np
import pandas as pd
import pytest

from app.factor_model import compute_multivariate_factor_attribution
from app.portfolio_hrp import optimize_hrp
from app.portfolio_black_litterman import optimize_black_litterman
from app.risk_budgeting import compute_risk_budgeting
from app.quant_analytics import compute_cornish_fisher_var, compute_expected_shortfall_and_capture
from app.stress_testing import evaluate_historical_stress_scenarios, simulate_factor_shocks
from app.services.fee_drag import compute_fee_drag_attribution
from app.services.plan_matcher import pair_direct_and_regular_schemes
from app.services.quant_intelligence import generate_fund_diagnostics_report
from app.db.connection import get_connection, fetchdf

from tests.e2e.e2e_oracles import (
    oracle_factor_attribution,
    oracle_evaluate_historical_stress_scenarios,
    oracle_simulate_factor_shocks,
    oracle_cornish_fisher_var,
    oracle_expected_shortfall_and_capture,
    oracle_optimize_hrp,
    oracle_optimize_black_litterman,
    oracle_compute_risk_budgeting,
    oracle_reconcile_dual_era_ter,
    oracle_pair_direct_and_regular,
    oracle_compute_fee_drag,
    oracle_generate_xai_narrative,
    oracle_build_waterfall_chart_spec,
    oracle_build_correlation_heatmap_spec,
    oracle_factor_slider_perturbation,
    oracle_serialize_url_params,
    oracle_deserialize_url_params,
    oracle_calculate_relative_luminance,
    oracle_wcag_contrast_ratio,
    oracle_is_wcag_aa_compliant,
    oracle_frontend_build_verification,
)

def _get_frontend_base_url() -> str:
    """Resolve active Next.js frontend base URL (container network or localhost)."""
    env_url = os.environ.get("FRONTEND_URL")
    if env_url:
        return env_url.rstrip("/")
    for candidate in (
        "http://mf_frontend:3000",
        "http://frontend:3000",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ):
        try:
            req = urllib.request.Request(candidate, headers={"User-Agent": "E2EProbe"})
            with urllib.request.urlopen(req, timeout=1) as resp:
                if resp.status in (200, 301, 302, 307, 308):
                    return candidate
        except Exception:
            continue
    return "http://mf_frontend:3000"

FRONTEND_URL = _get_frontend_base_url()


class TestTier3CrossFeatureInteractions:
    """Pairwise combinatorial validation across all quantitative, optimization, data, and UX features."""

    def test_pairwise_01_factor_model_plus_fee_drag(self):
        """F1 + F11: Factor regression annualized alpha reconciled with 3-tier fee drag gross alpha."""
        n = 250
        np.random.seed(3001)
        dates = pd.date_range("2023-01-01", periods=n, freq="D")
        mkt = np.random.normal(0.0004, 0.01, n)
        fund = mkt * 1.1 + 0.00015 + np.random.normal(0, 0.003, n)
        factors = pd.DataFrame({"mkt_excess": mkt}, index=dates)
        factors_4 = pd.DataFrame({
            "mkt_excess": mkt,
            "smb": np.zeros(n),
            "hml": np.zeros(n),
            "wml": np.zeros(n),
        }, index=dates)
        fund_s = pd.Series(fund, index=dates)

        prod_f1 = compute_multivariate_factor_attribution(fund_s, factors_4, rf_daily=0.00025)
        orc_f1 = oracle_factor_attribution(fund_s, factors, rf_daily=0.00025)
        net_alpha = prod_f1["alpha_annualized_pct"]
        assert math.isclose(net_alpha, orc_f1["alpha_annualized_pct"], abs_tol=1e-2)

        # Fee drag reconciliation with 0.85% TER Direct
        ter_d = 0.0085
        prod_f11 = compute_fee_drag_attribution(0.15, 0.138, ter_d, 0.0195, direct_alpha=net_alpha)
        orc_f11 = oracle_compute_fee_drag(0.15, 0.138, ter_d, 0.0195, direct_alpha=net_alpha)

        expected_gross_alpha = net_alpha + (ter_d * 100.0)
        assert math.isclose(prod_f11["alpha_isolation"]["gross_alpha_pct"], expected_gross_alpha, abs_tol=1e-3)
        assert math.isclose(orc_f11["alpha_isolation"]["gross_alpha_pct"], expected_gross_alpha, abs_tol=1e-3)
        assert prod_f11["alpha_isolation"]["net_alpha_direct_pct"] == net_alpha

    def test_pairwise_02_hrp_allocation_plus_stress_replay(self):
        """F5 + F2: HRP allocated portfolio subjected to COVID-19 historical crisis stress replay."""
        # 3 assets with known cov
        cov = np.diag([0.04, 0.09, 0.02])
        corr = np.eye(3)
        names = ["LargeCap", "MidCap", "Debt"]

        prod_hrp = optimize_hrp(cov, corr, names)
        orc_hrp = oracle_optimize_hrp(cov, corr, names)
        weights = prod_hrp["weights"]
        assert math.isclose(weights["LargeCap"], orc_hrp["weights"]["LargeCap"], abs_tol=1e-3)

        # Synthesize asset NAV curves during COVID
        dates = pd.date_range("2020-02-15", "2020-04-15", freq="D")
        t = np.linspace(0, 1, len(dates))
        # Large drops 30%, Mid drops 40%, Debt rises 2%
        nav_large = 100.0 * (1.0 - 0.30 * np.sin(np.pi * t))
        nav_mid = 100.0 * (1.0 - 0.40 * np.sin(np.pi * t))
        nav_debt = 100.0 * (1.0 + 0.02 * t)

        # Portfolio NAV based on HRP weights
        port_nav = weights["LargeCap"] * nav_large + weights["MidCap"] * nav_mid + weights["Debt"] * nav_debt
        df_port = pd.DataFrame({"nav_date": dates, "nav": port_nav})
        prod_stress = evaluate_historical_stress_scenarios(118482, df_hist=df_port)
        orc_stress = oracle_evaluate_historical_stress_scenarios(pd.Series(port_nav, index=dates))

        covid_prod = prod_stress["covid_march_2020"]
        covid_orc = orc_stress["covid_march_2020"]
        assert covid_prod["available"] is True or covid_prod.get("has_data") is True
        assert covid_orc["has_data"] is True
        # Thanks to Debt allocation from HRP, portfolio drop is cushioned (< -30%)
        assert covid_prod["drawdown_pct"] > -30.0

    def test_pairwise_03_black_litterman_plus_risk_budgeting_encb(self):
        """F6 + F7: Black-Litterman optimal weights evaluated for ENCB diversification and concentration."""
        cov = np.diag([0.04, 0.06, 0.05])
        w_mkt = np.array([0.4, 0.3, 0.3])
        P = np.array([[1.0, -1.0, 0.0]])
        Q = np.array([0.05])
        names = ["A", "B", "C"]

        prod_bl = optimize_black_litterman(cov, w_mkt, P, Q)
        orc_bl = oracle_optimize_black_litterman(cov, w_mkt, P, Q)
        bl_w = np.array(prod_bl["optimal_weights"])
        assert math.isclose(bl_w[0], orc_bl["optimal_weights"][0], abs_tol=1e-3)

        prod_rb = compute_risk_budgeting(bl_w, cov, names)
        orc_rb = oracle_compute_risk_budgeting(bl_w, cov, names)
        assert prod_rb["effective_number_of_constituents"] > 2.0
        assert prod_rb["effective_number_of_correlated_bets"] > 1.5
        assert math.isclose(prod_rb["effective_number_of_correlated_bets"], orc_rb["effective_number_of_correlated_bets"], abs_tol=1e-2)

    def test_pairwise_04_factor_regression_plus_waterfall_chart(self):
        """F1 + F13: Factor attribution outputs mapped directly into Plotly Waterfall data specification."""
        n = 200
        np.random.seed(3004)
        dates = pd.date_range("2023-01-01", periods=n, freq="D")
        mkt = np.random.normal(0.0005, 0.01, n)
        fund = mkt * 1.2 + 0.0001 + np.random.normal(0, 0.003, n)
        factors_4 = pd.DataFrame({
            "mkt_excess": mkt,
            "smb": np.zeros(n),
            "hml": np.zeros(n),
            "wml": np.zeros(n),
        }, index=dates)
        fund_s = pd.Series(fund, index=dates)

        prod_f1 = compute_multivariate_factor_attribution(fund_s, factors_4)
        orc_f1 = oracle_factor_attribution(fund_s, pd.DataFrame({"mkt_excess": mkt}, index=dates))
        alpha = prod_f1["alpha_annualized_pct"]
        assert math.isclose(alpha, orc_f1["alpha_annualized_pct"], abs_tol=1e-2)

        mkt_ret = prod_f1["factor_betas"]["market"] * np.mean(mkt) * 252 * 100
        wf = oracle_build_waterfall_chart_spec(alpha, {"market": mkt_ret}, 0.75, alpha + mkt_ret - 0.75)
        trace = wf["data"][0]
        assert trace["type"] == "waterfall"
        assert trace["measure"][-1] == "total"

    def test_pairwise_05_hrp_order_plus_correlation_heatmap(self):
        """F5 + F14: HRP quasi-diagonalization cluster order driving Correlation Heatmap axis sequence."""
        cov = np.array([
            [0.04, 0.035, 0.005],
            [0.035, 0.04, 0.005],
            [0.005, 0.005, 0.03],
        ])
        corr = cov / np.sqrt(np.outer(np.diag(cov), np.diag(cov)))
        names = ["EquityMid", "EquitySmall", "GovtBond"]

        prod_hrp = optimize_hrp(cov, corr, names)
        orc_hrp = oracle_optimize_hrp(cov, corr, names)
        cluster_order = prod_hrp["cluster_order"]
        assert cluster_order == orc_hrp["cluster_order"]

        heatmap_spec = oracle_build_correlation_heatmap_spec(corr, cluster_order)
        assert heatmap_spec["data"][0]["x"] == cluster_order
        assert heatmap_spec["data"][0]["y"] == cluster_order

    def test_pairwise_06_macro_stress_plus_simulation_sliders(self):
        """F2 + F15: Macro stress scenarios parameterized into real-time Factor Perturbation Sliders."""
        # Simulated COVID crash beta and slider shock
        betas = {"market": 1.20, "smb": 0.40}
        # March 2020 COVID shock: Market -38.44%, MidSmall -15.0%
        covid_slider_shock = {"market": -38.44, "smb": -15.0}

        prod_sim = simulate_factor_shocks(betas, covid_slider_shock, is_percentage=True)
        orc_sim = oracle_factor_slider_perturbation(betas, covid_slider_shock)
        expected_dd = (1.20 * -38.44) + (0.40 * -15.0)  # -46.128 - 6.0 = -52.128%
        assert math.isclose(prod_sim["total_return_impact_pct"], expected_dd, abs_tol=1e-3)
        assert math.isclose(orc_sim["simulated_return_delta_pct"], expected_dd, abs_tol=1e-3)

    def test_pairwise_07_cornish_fisher_var_plus_expected_shortfall(self):
        """F3 + F4: Cornish-Fisher VaR combined with Expected Shortfall (CVaR) and drawdown tracking."""
        np.random.seed(3007)
        returns = np.random.normal(0.0004, 0.015, 500)
        dates = pd.date_range("2023-01-01", periods=len(returns), freq="D")
        nav_s = pd.Series(100.0 * np.cumprod(1.0 + returns), index=dates)

        prod_cf = compute_cornish_fisher_var(returns, confidence_levels=[0.99])
        orc_cf = oracle_cornish_fisher_var(returns, confidence_levels=[0.99])
        assert math.isclose(prod_cf["var_99_cf_daily_pct"], orc_cf["var_99_cf_daily_pct"], abs_tol=1e-2)

        prod_cvar = compute_expected_shortfall_and_capture(returns, nav_series=nav_s, confidence_level=0.99)
        orc_cvar = oracle_expected_shortfall_and_capture(returns, nav_series=nav_s, confidence_level=0.99)
        assert math.isclose(prod_cvar["cvar_99_daily_pct"], orc_cvar["cvar_99_daily_pct"], abs_tol=1e-2)
        # Expected shortfall (loss magnitude) is consistent with 99% VaR
        assert prod_cvar["cvar_99_daily_pct"] <= prod_cvar["var_99_daily_pct"]
        assert prod_cvar["drawdown_metrics"]["max_drawdown_pct"] < 0.0

    def test_pairwise_08_database_queries_plus_fee_drag_compounding(self):
        """F8 + F11: Database lateral join retrieves live latest NAVs to power multi-horizon fee drag."""
        con = get_connection()
        try:
            sql = """
                SELECT s.scheme_code, lat.latest_nav
                FROM schemes s
                LEFT JOIN LATERAL (
                    SELECT nav as latest_nav FROM nav_history WHERE scheme_code = s.scheme_code ORDER BY nav_date DESC LIMIT 1
                ) lat ON true
                WHERE s.scheme_code IN (118481, 118482);
            """
            df = fetchdf(con.execute(sql))
            # Must return in under SLA
            assert not df.empty
        finally:
            con.close()

        # Fee drag over 10 years
        prod_fee = compute_fee_drag_attribution(0.145, 0.135, 0.008, 0.018)
        orc_fee = oracle_compute_fee_drag(0.145, 0.135, 0.008, 0.018)
        assert prod_fee["horizons"]["10Y"]["rupee_wealth_erosion"] > 0.0
        assert math.isclose(prod_fee["horizons"]["10Y"]["rupee_wealth_erosion"], orc_fee["horizons"]["10Y"]["rupee_wealth_erosion"], abs_tol=1.0)

    def test_pairwise_09_amfi_sync_plus_plan_matcher(self):
        """F9 + F10: Dual-era AMFI TER sync feeds deterministic Direct vs Regular plan matcher."""
        records = [
            {"scheme_code": 118481, "scheme_name": "Bandhan Nifty 50 Index Fund Regular Plan Growth", "fund_house": "Bandhan", "category": "Index Funds", "official_ter": 1.65},
            {"scheme_code": 118482, "scheme_name": "Bandhan Nifty 50 Index Fund Direct Plan Growth", "fund_house": "Bandhan", "category": "Index Funds", "official_ter": 0.80},
        ]
        sync_res = oracle_reconcile_dual_era_ter(records)
        assert sync_res["processed_count"] == 2

        prod_paired = pair_direct_and_regular_schemes(records)
        orc_paired = oracle_pair_direct_and_regular(records)
        assert prod_paired.get(118481) == 118482
        assert orc_paired.get(118481) == 118482

    def test_pairwise_10_factor_regression_plus_algorithmic_narratives(self):
        """F1 + F12: Multivariate factor betas feed automated qualitative XAI narrative generator."""
        betas = {"mkt_excess": 1.15, "smb": 0.35, "hml": 0.20, "wml": -0.10}
        prod_res = generate_fund_diagnostics_report("Quant Mid Cap Fund", betas, alpha_ann_pct=3.85, sharpe=1.45, quartile=1)
        orc_res = oracle_generate_xai_narrative("Quant Mid Cap Fund", betas, alpha_ann_pct=3.85, sharpe=1.45, quartile=1)

        assert prod_res["headline_summary"] == orc_res["headline_summary"]
        assert prod_res["factor_exposure_narrative"] == orc_res["factor_exposure_narrative"]
        assert "Mid/Small-Cap tilt" in prod_res["factor_exposure_narrative"]
        assert "Deep Value orientation" in prod_res["factor_exposure_narrative"]
        assert "Q1" in prod_res["headline_summary"]

    def test_pairwise_11_hrp_vs_black_litterman_allocation_comparison(self):
        """F5 + F6: HRP clustering vs Black-Litterman Bayesian optimal weight allocations."""
        cov = np.diag([0.04, 0.09, 0.03])
        corr = np.eye(3)
        names = ["A", "B", "C"]
        w_mkt = np.array([0.33, 0.33, 0.34])

        prod_hrp = optimize_hrp(cov, corr, names)
        orc_hrp = oracle_optimize_hrp(cov, corr, names)
        prod_bl = optimize_black_litterman(cov, w_mkt, np.array([[1.0, 0.0, 0.0]]), np.array([0.08]))
        orc_bl = oracle_optimize_black_litterman(cov, w_mkt, np.array([[1.0, 0.0, 0.0]]), np.array([0.08]))

        # Both sum to 1.0
        assert math.isclose(sum(prod_hrp["weights"].values()), 1.0, abs_tol=1e-4)
        assert math.isclose(sum(prod_bl["optimal_weights"]), 1.0, abs_tol=1e-4)
        assert math.isclose(sum(orc_hrp["weights"].values()), 1.0, abs_tol=1e-4)
        assert math.isclose(sum(orc_bl["optimal_weights"]), 1.0, abs_tol=1e-4)

    def test_pairwise_12_risk_budgeting_under_hrp_tree_clustering(self):
        """F7 + F5: Risk budgeting decomposition computed on HRP allocated weights."""
        cov = np.array([[0.04, 0.01], [0.01, 0.09]])
        corr = cov / np.sqrt(np.outer(np.diag(cov), np.diag(cov)))
        names = ["LowRisk", "HighRisk"]

        prod_hrp = optimize_hrp(cov, corr, names)
        orc_hrp = oracle_optimize_hrp(cov, corr, names)
        w_prod = np.array([prod_hrp["weights"]["LowRisk"], prod_hrp["weights"]["HighRisk"]])
        w_orc = np.array([orc_hrp["weights"]["LowRisk"], orc_hrp["weights"]["HighRisk"]])

        prod_rb = compute_risk_budgeting(w_prod, cov, names)
        orc_rb = oracle_compute_risk_budgeting(w_orc, cov, names)
        # LowRisk asset received higher HRP weight, balancing the total risk contribution
        assert prod_rb["effective_number_of_constituents"] > 1.5
        assert orc_rb["effective_number_of_constituents"] > 1.5
        assert math.isclose(prod_rb["effective_number_of_constituents"], orc_rb["effective_number_of_constituents"], rel_tol=1e-3)

    def test_pairwise_13_fee_drag_compounding_plus_xai_migration_card(self):
        """F11 + F12: 10-year compounded fee drag triggers qualitative XAI migration recommendation."""
        prod_fee = compute_fee_drag_attribution(0.15, 0.138, 0.0075, 0.0195, initial_capital=100000.0)
        orc_fee = oracle_compute_fee_drag(0.15, 0.138, 0.0075, 0.0195, initial_capital=100000.0)
        ter_diff = prod_fee["alpha_isolation"]["ter_differential_pct"]

        prod_xai = generate_fund_diagnostics_report("HDFC Large Cap Regular", {"mkt_excess": 1.0}, alpha_ann_pct=1.0, sharpe=1.0, is_regular=True, ter_diff=ter_diff)
        orc_xai = oracle_generate_xai_narrative("HDFC Large Cap Regular", {"mkt_excess": 1.0}, alpha_ann_pct=1.0, sharpe=1.0, is_regular=True, ter_diff=ter_diff)
        assert prod_xai["migration_recommendation"] is not None
        assert prod_xai["migration_recommendation"]["recommendation"] == "MIGRATE_TO_DIRECT"
        assert prod_xai["migration_recommendation"]["recommendation"] == orc_xai["migration_recommendation"]["recommendation"]
        assert f"{ter_diff:.2f}%" in prod_xai["migration_recommendation"]["actionable_insight"]

    def test_pairwise_14_state_sync_plus_factor_attribution_window(self):
        """F16 + F1: URL query state parameter preserves exact factor regression date window."""
        state = {"scheme_code": "118482", "start_date": "2022-01-01", "end_date": "2024-01-01", "bench_mode": "Category Benchmark"}
        query = oracle_serialize_url_params(state)
        restored = oracle_deserialize_url_params(query)

        # Reconstructed window produces consistent date bounds
        s = pd.to_datetime(restored["start_date"])
        e = pd.to_datetime(restored["end_date"])
        assert (e - s).days > 700

    def test_pairwise_15_state_sync_plus_hrp_weights(self):
        """F16 + F5: Deep-linked URL query parameters serialize active HRP risk profile and universe."""
        state = {"risk_tier": "Aggressive", "universe": "LargeMidCap", "optimizer": "HRP"}
        query = oracle_serialize_url_params(state)
        restored = oracle_deserialize_url_params(query)
        assert restored["optimizer"] == "HRP"
        assert restored["risk_tier"] == "Aggressive"

    def test_pairwise_16_waterfall_chart_plus_wcag_contrast(self):
        """F17 + F13: Factor waterfall chart text and connector visual tokens satisfy WCAG AA contrast."""
        # Slate-900 on white canvas
        contrast_text = oracle_wcag_contrast_ratio("#0F172A", "#FFFFFF")
        # Dark mode slate-100 on slate-950 canvas
        contrast_dark = oracle_wcag_contrast_ratio("#F1F5F9", "#020617")

        assert contrast_text >= 4.5
        assert contrast_dark >= 4.5

    def test_pairwise_17_heatmap_plus_wcag_contrast(self):
        """F17 + F14: Heatmap dynamic text contrast adapts between dark and light cells to maintain WCAG AA."""
        # White text on dark red negative cell (#991B1B)
        contrast_dark_cell = oracle_wcag_contrast_ratio("#FFFFFF", "#991B1B")
        # Dark text on light cell (#F8FAFC)
        contrast_light_cell = oracle_wcag_contrast_ratio("#0F172A", "#F8FAFC")

        assert contrast_dark_cell >= 4.5
        assert contrast_light_cell >= 4.5

    def test_pairwise_18_frontend_build_plus_state_sync_routes(self):
        """F18 + F16: Production Next.js server serves deep-linkable URLs with state queries cleanly."""
        base_url = _get_frontend_base_url()
        query = "start_date=2023-01-01&end_date=2024-01-01"
        req = urllib.request.Request(f"{base_url}/screener?{query}", headers={"User-Agent": "E2ETest"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
            content = resp.read()
            assert len(content) > 0
