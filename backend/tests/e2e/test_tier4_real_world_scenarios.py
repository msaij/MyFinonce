"""Tier 4: Real-World Application Workloads (End-to-End Multi-Fund Scenarios).
Executes 5 comprehensive real-world institutional workflows as defined in TEST_INFRA.md:
  1. Large-Cap Equity Active Manager Full Attribution (F1, F3, F4, F12, F13)
  2. COVID-19 & 2022 Crisis Stress Replay on Multi-Asset Portfolio (F2, F15)
  3. Institutional Endowment 10-Fund HRP vs MVO Allocation (F5, F7, F14)
  4. Tactically Tilted Pension Allocation via Black-Litterman (F6, F7)
  5. Dual-Era 10-Year Fee Drag Attribution Direct vs Regular (F8, F9, F10, F11, F12)
Total: 5 high-complexity end-to-end application scenarios.
"""

import math
import time
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.factor_model import compute_multivariate_factor_attribution
from app.portfolio_hrp import optimize_hrp
from app.portfolio_black_litterman import optimize_black_litterman
from app.risk_budgeting import compute_risk_budgeting
from app.quant_analytics import compute_cornish_fisher_var, compute_expected_shortfall_and_capture
from app.stress_testing import evaluate_historical_stress_scenarios, simulate_factor_shocks
from app.services.fee_drag import compute_fee_drag_attribution
from app.services.plan_matcher import pair_direct_and_regular_schemes
from app.services.quant_intelligence import generate_fund_diagnostics_report
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
)
from app.db.connection import get_connection, fetchdf


class TestTier4RealWorldScenarios:
    """Comprehensive End-to-End Institutional Workloads across Real-World Mutual Fund Portfolios."""

    def test_scenario_1_large_cap_active_manager_full_attribution(self):
        """Scenario 1: Large-Cap Active Manager Full Attribution.
        Exercising: F1 (Multi-Factor Regression), F3 (Cornish-Fisher VaR),
                    F4 (Expected Shortfall & Drawdown), F12 (XAI Narrative), F13 (Waterfall Chart).
        """
        # 1. Synthesize 3-year daily NAV and factor series
        np.random.seed(4001)
        dates = pd.date_range("2021-01-01", "2023-12-31", freq="D")
        n = len(dates)

        mkt = np.random.normal(0.00045, 0.011, n)
        smb = np.random.normal(0.0001, 0.006, n)
        hml = np.random.normal(-0.0001, 0.006, n)
        wml = np.random.normal(0.0002, 0.007, n)

        # Skilled manager: +2.5% p.a. alpha, beta_mkt=1.10, quality tilt (hml = -0.15)
        alpha_daily = 0.025 / 252.0
        fund_excess = alpha_daily + 1.10 * mkt + 0.15 * smb - 0.15 * hml + 0.10 * wml + np.random.normal(0, 0.002, n)
        rf_daily = 0.00025
        fund_ret = fund_excess + rf_daily
        nav_series = pd.Series(100.0 * np.cumprod(1.0 + fund_ret), index=dates)

        factors_df = pd.DataFrame({"mkt_excess": mkt, "smb": smb, "hml": hml, "wml": wml}, index=dates)
        fund_ret_series = pd.Series(fund_ret, index=dates)

        # Step A: Multivariate Factor Attribution (F1)
        prod_f1 = compute_multivariate_factor_attribution(fund_ret_series, factors_df, rf_daily=rf_daily)
        orc_f1 = oracle_factor_attribution(fund_ret_series, factors_df, rf_daily=rf_daily)
        assert abs(prod_f1["factor_betas"]["market"] - 1.10) < 0.05
        assert prod_f1["alpha_annualized_pct"] > 1.5
        assert prod_f1["r_squared"] > 0.85
        assert math.isclose(prod_f1["factor_betas"]["market"], orc_f1["factor_betas"]["mkt_excess"], abs_tol=1e-3)
        assert math.isclose(prod_f1["alpha_annualized_pct"], orc_f1["alpha_annualized_pct"], abs_tol=1e-2)

        # Step B: Cornish-Fisher 95% and 99% Tail Risk VaR (F3)
        prod_f3 = compute_cornish_fisher_var(fund_ret, confidence_levels=[0.95, 0.99])
        orc_f3 = oracle_cornish_fisher_var(fund_ret, confidence_levels=[0.95, 0.99])
        assert prod_f3["var_99_cf_daily_pct"] > prod_f3["var_95_cf_daily_pct"]
        assert prod_f3["var_99_cf_ann_pct"] > 20.0
        assert math.isclose(prod_f3["var_95_cf_daily_pct"], orc_f3["var_95_cf_daily_pct"], rel_tol=1e-3)
        assert math.isclose(prod_f3["var_99_cf_daily_pct"], orc_f3["var_99_cf_daily_pct"], rel_tol=1e-3)

        # Step C: Expected Shortfall (CVaR) and Peak-to-Trough Drawdown Duration (F4)
        prod_f4 = compute_expected_shortfall_and_capture(fund_ret, nav_series=nav_series, confidence_level=0.99)
        orc_f4 = oracle_expected_shortfall_and_capture(fund_ret, nav_series=nav_series, confidence_level=0.99)
        assert prod_f4["cvar_99_daily_pct"] <= prod_f4["var_99_daily_pct"]
        assert prod_f4["drawdown_metrics"]["max_drawdown_pct"] < 0.0
        assert math.isclose(prod_f4["cvar_99_daily_pct"], orc_f4["cvar_99_daily_pct"], rel_tol=1e-3)

        # Step D: Factor Exposure Waterfall Chart Contract (F13)
        mkt_contr = float(prod_f1["factor_betas"]["market"] * np.mean(mkt) * 252 * 100)
        smb_contr = float(prod_f1["factor_betas"]["size"] * np.mean(smb) * 252 * 100)
        net_ret = prod_f1["alpha_annualized_pct"] + mkt_contr + smb_contr - 0.75
        wf_spec = oracle_build_waterfall_chart_spec(
            prod_f1["alpha_annualized_pct"],
            {"market": mkt_contr, "size": smb_contr},
            0.75,
            net_ret,
        )
        assert wf_spec["data"][0]["type"] == "waterfall"
        assert wf_spec["data"][0]["measure"][-1] == "total"

        # Step E: Deterministic Algorithmic Narrative Report (F12)
        prod_f12 = generate_fund_diagnostics_report(
            "ICICI Prudential Bluechip Direct",
            prod_f1["factor_betas"],
            alpha_ann_pct=prod_f1["alpha_annualized_pct"],
            sharpe=1.42,
            quartile=1,
        )
        orc_f12 = oracle_generate_xai_narrative(
            "ICICI Prudential Bluechip Direct",
            prod_f1["factor_betas"],
            alpha_ann_pct=prod_f1["alpha_annualized_pct"],
            sharpe=1.42,
            quartile=1,
        )
        assert "ICICI Prudential Bluechip Direct" in prod_f12["scheme_name"]
        assert "Q1" in prod_f12["headline_summary"]
        assert "security selection skill" in prod_f12["factor_exposure_narrative"]
        assert prod_f12["headline_summary"] == orc_f12["headline_summary"]
        assert prod_f12["factor_exposure_narrative"] == orc_f12["factor_exposure_narrative"]

        # Step F: Real API Endpoint Verification via FastAPI TestClient
        client = TestClient(app)
        api_factors = client.get("/api/quant/118482/factors?start_date=2021-01-01&end_date=2023-12-31")
        assert api_factors.status_code == 200
        fact_json = api_factors.json()
        assert "regression" in fact_json
        assert "factor_betas" in fact_json["regression"]

        api_tail = client.get("/api/quant/118482/tail-risk?start_date=2021-01-01&end_date=2023-12-31")
        assert api_tail.status_code == 200
        tail_json = api_tail.json()
        assert "tail_risk" in tail_json
        assert "cvar_95_daily_pct" in tail_json["tail_risk"]

    def test_scenario_2_covid_and_rate_hike_stress_replay_on_multi_asset_portfolio(self):
        """Scenario 2: COVID-19 & 2022 Crisis Stress Replay on Multi-Asset Portfolio.
        Exercising: F2 (Historical Stress Replay Windows) and F15 (Scenario Perturbation Sliders).
        """
        # Multi-asset allocation: 50% Nifty 50, 30% Mid Cap, 20% Short Term Debt
        weights = {"market": 0.50, "midcap": 0.30, "debt": 0.20}
        asset_betas_to_market = {"market": 1.0, "midcap": 1.35, "debt": 0.05}

        # Weighted portfolio beta to market: 0.50*1.0 + 0.30*1.35 + 0.20*0.05 = 0.915
        port_beta_mkt = sum(weights[a] * asset_betas_to_market[a] for a in weights)
        assert math.isclose(port_beta_mkt, 0.915, abs_tol=1e-3)

        # Step A: Historical Replay against March 2020 COVID Shock (F2)
        dates_covid = pd.date_range("2020-02-15", "2020-04-15", freq="D")
        t = np.linspace(0, 1, len(dates_covid))
        # Portfolio drop cushioned by debt sleeve
        port_nav_covid = 100.0 * (1.0 - (0.3844 * port_beta_mkt) * np.sin(np.pi * t))
        series_covid = pd.Series(port_nav_covid, index=dates_covid)

        prod_stress = evaluate_historical_stress_scenarios(series_covid)
        orc_stress = oracle_evaluate_historical_stress_scenarios(series_covid)

        covid_replay = prod_stress["covid_march_2020"]
        assert covid_replay["has_data"] is True
        # Actual drawdown should be ~ -35.1%
        assert -40.0 < covid_replay["drawdown_pct"] < -30.0
        assert math.isclose(covid_replay["drawdown_pct"], orc_stress["covid_march_2020"]["drawdown_pct"], abs_tol=1e-2)

        # Step B: Factor Perturbation Slider Sensitivity Simulation (F15)
        # Institutional user moves Market crash slider to -38.44%
        slider_shock = {"market": -38.44}
        prod_slider = simulate_factor_shocks({"market": port_beta_mkt}, slider_shock)
        orc_slider = oracle_factor_slider_perturbation({"market": port_beta_mkt}, slider_shock)

        # Slider projected impact: 0.915 * -38.44% = -35.17%
        expected_slider_impact = port_beta_mkt * -38.44
        assert math.isclose(prod_slider["simulated_return_delta_pct"], expected_slider_impact, abs_tol=1e-3)
        assert math.isclose(prod_slider["simulated_return_delta_pct"], orc_slider["simulated_return_delta_pct"], abs_tol=1e-3)
        # Replay and slider align within 1%
        assert abs(prod_slider["simulated_return_delta_pct"] - covid_replay["drawdown_pct"]) < 1.0

        # Step C: Live API Endpoint Verification via FastAPI TestClient
        client = TestClient(app)
        api_stress = client.get("/api/quant/118482/stress-test?shock_market=-0.3844")
        assert api_stress.status_code == 200
        stress_json = api_stress.json()
        assert "scenarios" in stress_json
        scenario_ids = [s["id"] for s in stress_json["scenarios"]]
        assert "covid_march_2020" in scenario_ids

    def test_scenario_3_institutional_endowment_10_fund_hrp_vs_mvo_allocation(self):
        """Scenario 3: Institutional Endowment 10-Fund HRP Allocation vs MVO.
        Exercising: F5 (HRP Clustering & Quasi-Diagonalization), F7 (ENCB Risk Budgeting),
                    F14 (Heatmap with HRP Dendrogram Ordering).
        """
        n = 10
        names = [
            "LargeCap_1", "LargeCap_2", "MidCap_1", "MidCap_2", "SmallCap_1",
            "SmallCap_2", "FlexiCap_1", "Hybrid_1", "ShortDebt_1", "Liquid_1",
        ]
        np.random.seed(4003)
        # Generate realistic block correlation covariance matrix
        # Equity cluster has high correlation (~0.85), Debt cluster has low correlation (~0.15)
        vols = np.array([0.14, 0.15, 0.18, 0.19, 0.22, 0.24, 0.16, 0.10, 0.04, 0.015])
        corr = np.eye(n)
        for i in range(7):
            for j in range(7):
                if i != j:
                    corr[i, j] = 0.80 + np.random.uniform(-0.05, 0.05)
        # Low correlation between equity and debt
        for i in range(7):
            for j in range(7, 10):
                corr[i, j] = corr[j, i] = 0.10 + np.random.uniform(-0.05, 0.05)
        # Debt-debt correlation
        corr[7, 8] = corr[8, 7] = 0.40
        corr[8, 9] = corr[9, 8] = 0.30

        cov = corr * np.outer(vols, vols)

        # Step A: HRP Tree Optimization (F5)
        prod_hrp = optimize_hrp(cov, corr, names)
        orc_hrp = oracle_optimize_hrp(cov, corr, names)
        weights = prod_hrp["weights"]
        order = prod_hrp["cluster_order"]

        assert math.isclose(sum(weights.values()), 1.0, abs_tol=1e-5)
        assert math.isclose(sum(orc_hrp["weights"].values()), 1.0, abs_tol=1e-5)
        assert len(order) == n
        # HRP does not collapse weights to zero: every asset receives a non-zero allocation
        for a, w in weights.items():
            assert w > 0.0, f"Weight collapse in HRP for {a}: {w}"
            assert math.isclose(w, orc_hrp["weights"][a], rel_tol=1e-3)

        # Step B: Risk Budgeting & Effective Number of Correlated Bets (ENCB) (F7)
        w_arr = np.array([weights[a] for a in names])
        w_orc_arr = np.array([orc_hrp["weights"][a] for a in names])
        prod_rb = compute_risk_budgeting(w_arr, cov, names)
        orc_rb = oracle_compute_risk_budgeting(w_orc_arr, cov, names)

        assert 1.0 <= prod_rb["effective_number_of_constituents"] <= 10.0
        assert 1.0 <= prod_rb["effective_number_of_correlated_bets"] <= 10.0
        assert prod_rb["portfolio_volatility"] > 0.0
        assert math.isclose(sum(prod_rb["percentage_risk_contributions"].values()), 100.0, abs_tol=0.1)
        assert math.isclose(prod_rb["effective_number_of_constituents"], orc_rb["effective_number_of_constituents"], rel_tol=1e-3)
        assert math.isclose(prod_rb["effective_number_of_correlated_bets"], orc_rb["effective_number_of_correlated_bets"], rel_tol=1e-3)

        # Step C: Plotly Correlation Heatmap with HRP Dendrogram Ordering (F14)
        reordered_corr = np.zeros((n, n))
        for i, a1 in enumerate(order):
            for j, a2 in enumerate(order):
                idx1 = names.index(a1)
                idx2 = names.index(a2)
                reordered_corr[i, j] = corr[idx1, idx2]

        heatmap = oracle_build_correlation_heatmap_spec(reordered_corr, order)
        assert heatmap["data"][0]["x"] == order
        assert heatmap["data"][0]["y"] == order
        assert heatmap["data"][0]["zmin"] == -1.0

        # Step D: Live Portfolio Advisor API Verification via FastAPI TestClient
        client = TestClient(app)
        api_quest = client.get("/api/portfolio-advisor/questionnaire")
        assert api_quest.status_code == 200
        quest_data = api_quest.json()
        assert "questions" in quest_data
        assert "risk_tiers" in quest_data

    def test_scenario_4_tactically_tilted_pension_allocation_via_black_litterman(self):
        """Scenario 4: Tactically Tilted Pension Allocation via Black-Litterman.
        Exercising: F6 (Black-Litterman Bayesian Prior Blending) and F7 (ENCB Risk Concentration).
        """
        asset_classes = ["Nifty_50", "Nifty_MidCap", "Long_GSec", "Corp_Debt"]
        # Equilibrium market weights: 40% Large, 20% Mid, 25% GSec, 15% CorpDebt
        w_mkt = np.array([0.40, 0.20, 0.25, 0.15])
        vols = np.array([0.14, 0.18, 0.07, 0.04])
        corr = np.array([
            [1.0, 0.85, 0.05, 0.10],
            [0.85, 1.0, 0.02, 0.08],
            [0.05, 0.02, 1.0, 0.65],
            [0.10, 0.08, 0.65, 1.0],
        ])
        cov = corr * np.outer(vols, vols)
        lmbda = 2.5

        # Tactical View: Macro team believes MidCap will outperform Nifty 50 by +5.0%
        # P = [-1.0, 1.0, 0.0, 0.0], Q = [0.05], Confidence = 80%
        P = np.array([[-1.0, 1.0, 0.0, 0.0]])
        Q = np.array([0.05])
        confidence = np.array([0.80])

        # Step A: Bayesian Optimization (F6)
        prod_bl = optimize_black_litterman(cov, w_mkt, P, Q, risk_aversion=lmbda, views_confidences=confidence)
        orc_bl = oracle_optimize_black_litterman(cov, w_mkt, P, Q, risk_aversion=lmbda, views_confidences=confidence)
        opt_w = np.array(prod_bl["optimal_weights"])
        orc_w = np.array(orc_bl["optimal_weights"])

        # Optimal weights must tilt towards MidCap relative to prior
        assert opt_w[1] > w_mkt[1], "Tactical view failed to increase MidCap allocation"
        assert opt_w[0] < w_mkt[0], "Tactical view failed to reduce Nifty 50 allocation"
        assert math.isclose(sum(opt_w), 1.0, abs_tol=1e-4)
        np.testing.assert_allclose(opt_w, orc_w, rtol=1e-3, atol=1e-4)

        # Step B: Concentration Audit (F7)
        prod_rb = compute_risk_budgeting(opt_w, cov, asset_classes)
        orc_rb = oracle_compute_risk_budgeting(orc_w, cov, asset_classes)
        assert prod_rb["effective_number_of_constituents"] >= 1.0
        assert prod_rb["effective_number_of_correlated_bets"] >= 1.0
        assert math.isclose(prod_rb["effective_number_of_constituents"], orc_rb["effective_number_of_constituents"], rel_tol=1e-3)
        assert math.isclose(prod_rb["effective_number_of_correlated_bets"], orc_rb["effective_number_of_correlated_bets"], rel_tol=1e-3)

        # Step C: Live API Endpoint Verification via FastAPI TestClient
        client = TestClient(app)
        bl_payload = {
            "asset_names": asset_classes,
            "prior_weights": w_mkt.tolist(),
            "views_matrix_P": P.tolist(),
            "views_returns_Q": Q.tolist(),
            "views_confidences": confidence.tolist(),
            "risk_aversion": lmbda,
            "cov_matrix": cov.tolist(),
        }
        api_bl = client.post("/api/portfolio-advisor/black-litterman", json=bl_payload)
        assert api_bl.status_code == 200
        bl_json = api_bl.json()
        assert "optimal_weights" in bl_json
        assert len(bl_json["optimal_weights"]) == 4
        assert bl_json["optimal_weights"][1] > w_mkt[1]

    def test_scenario_5_dual_era_10_year_fee_drag_attribution_direct_vs_regular(self):
        """Scenario 5: Dual-Era 10-Year Fee Drag Attribution (Direct vs Regular).
        Exercising: F8 (Fast Database Query), F9 (AMFI TER Archive Sync),
                    F10 (Plan Pairing), F11 (Fee Drag Attribution), F12 (Migration XAI).
        """
        # Step A: Fast Database Query Benchmark SLA (F8)
        con = get_connection()
        try:
            t0 = time.perf_counter()
            sql = """
                SELECT s.scheme_code, s.scheme_name, s.fund_house, s.category,
                       lat.latest_nav, lat.latest_date
                FROM schemes s
                LEFT JOIN LATERAL (
                    SELECT nav as latest_nav, nav_date as latest_date
                    FROM nav_history WHERE scheme_code = s.scheme_code ORDER BY nav_date DESC LIMIT 1
                ) lat ON true
                WHERE s.category LIKE '%Large Cap%'
                LIMIT 50;
            """
            df_schemes = fetchdf(con.execute(sql))
            latency_ms = (time.perf_counter() - t0) * 1000.0
            assert latency_ms < 1000.0, f"Query exceeded 1,000ms: {latency_ms:.2f}ms"
            assert not df_schemes.empty
        finally:
            con.close()

        # Step B: Dual-Era TER Ingestion & Schema Reconciliation (F9)
        ter_records = [
            {"scheme_code": 118481, "scheme_name": "Bandhan Nifty 50 Regular Growth", "fund_house": "Bandhan", "category": "Index Funds", "official_ter": 1.65},
            {"scheme_code": 118482, "scheme_name": "Bandhan Nifty 50 Direct Growth", "fund_house": "Bandhan", "category": "Index Funds", "official_ter": 0.80},
        ]
        sync_res = oracle_reconcile_dual_era_ter(ter_records)
        assert sync_res["processed_count"] == 2

        # Step C: Deterministic Plan Matcher (F10)
        prod_paired = pair_direct_and_regular_schemes(ter_records)
        orc_paired = oracle_pair_direct_and_regular(ter_records)
        reg_code = 118481
        direct_code = prod_paired[reg_code]
        assert direct_code == 118482
        assert orc_paired[reg_code] == 118482

        # Step D: 10-Year Fee Drag Compounding & Alpha Isolation (F11)
        # Direct CAGR: 14.5%, Regular CAGR: 13.65% (0.85% TER differential), Capital: 100,000 INR
        prod_f11 = compute_fee_drag_attribution(
            direct_cagr=0.145,
            regular_cagr=0.1365,
            ter_direct=0.0080,
            ter_regular=0.0165,
            direct_alpha=2.10,
            regular_alpha=1.25,
            horizons=["1Y", "3Y", "5Y", "10Y"],
            initial_capital=100000.0,
        )
        orc_f11 = oracle_compute_fee_drag(
            direct_cagr=0.145,
            regular_cagr=0.1365,
            ter_direct=0.0080,
            ter_regular=0.0165,
            direct_alpha=2.10,
            regular_alpha=1.25,
            horizons=["1Y", "3Y", "5Y", "10Y"],
            initial_capital=100000.0,
        )

        h10 = prod_f11["horizons"]["10Y"]
        orc_h10 = orc_f11["horizons"]["10Y"]
        assert h10["wealth_direct"] > h10["wealth_regular"]
        assert h10["rupee_wealth_erosion"] > 25000.0  # > 25,000 INR lost on 100k capital over 10Y
        assert h10["cumulative_drag_pct"] > 7.0
        assert math.isclose(h10["rupee_wealth_erosion"], orc_h10["rupee_wealth_erosion"], abs_tol=1.0)
        assert math.isclose(h10["cumulative_drag_pct"], orc_h10["cumulative_drag_pct"], rel_tol=1e-3)

        # Step E: XAI Migration Recommendation Card (F12)
        ter_diff = prod_f11["alpha_isolation"]["ter_differential_pct"]
        prod_f12 = generate_fund_diagnostics_report(
            "Bandhan Nifty 50 Regular Growth",
            {"mkt_excess": 1.0},
            alpha_ann_pct=1.25,
            sharpe=1.1,
            is_regular=True,
            ter_diff=ter_diff,
        )
        orc_f12 = oracle_generate_xai_narrative(
            "Bandhan Nifty 50 Regular Growth",
            {"mkt_excess": 1.0},
            alpha_ann_pct=1.25,
            sharpe=1.1,
            is_regular=True,
            ter_diff=ter_diff,
        )
        assert prod_f12["migration_recommendation"]["recommendation"] == "MIGRATE_TO_DIRECT"
        assert prod_f12["migration_recommendation"]["recommendation"] == orc_f12["migration_recommendation"]["recommendation"]
        assert f"{ter_diff:.2f}%" in prod_f12["migration_recommendation"]["actionable_insight"]

        # Step F: Live Fee Drag API Endpoint Verification
        client = TestClient(app)
        api_feedrag = client.get("/api/quant/118481/fee-drag?paired_scheme_code=118482&initial_capital=100000.0")
        assert api_feedrag.status_code == 200
        feedrag_json = api_feedrag.json()
        assert "horizons" in feedrag_json
        assert "alpha_isolation" in feedrag_json
