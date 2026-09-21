"""Challenger 1 Adversarial Verification & Stress Test Suite for Milestone 4.
Focuses on:
1. Mathematical precision cross-verification: Client-Side Delta R = sum(beta_k * Delta F_k) and Rupee Impact vs Python simulate_factor_shocks.
2. Boundary slider perturbations: -30%, +30%, zero shocks, extreme betas (+/- 5.0, +/- 50.0).
3. Missing factor attribution (422 HTTP response) and graceful fallback handling.
4. Factor waterfall chart trace contract and algebraic consistency.
"""

import math
import random
import pytest
from decimal import Decimal, ROUND_HALF_UP
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db import connection, queries as db_queries
from app.main import app
from app.stress_testing import simulate_factor_shocks, HISTORICAL_SCENARIOS
from tests.e2e.e2e_oracles import (
    oracle_factor_slider_perturbation,
    oracle_build_waterfall_chart_spec,
)


# =====================================================================
# 1. Mathematical Precision & Boundary Slider Stress Testing
# =====================================================================

class TestAdversarialFactorSliderMathematicalPrecision:
    """Stress tests boundary perturbations, extreme betas, and exact mathematical precision."""

    def test_exact_boundary_slider_perturbations(self):
        """Tests maximum boundaries of all 4 sliders (-30% / +30% market, -20% / +20% others)."""
        betas = {"market": 1.2, "size": 0.6, "value": -0.4, "momentum": 0.3}

        # Maximum severe crash boundary
        shocks_crash = {"market": -30.0, "size": -20.0, "value": -20.0, "momentum": -20.0}
        sim_crash = simulate_factor_shocks(betas, shocks_crash, initial_capital=100000.0, is_percentage=True)
        # (1.2 * -30) + (0.6 * -20) + (-0.4 * -20) + (0.3 * -20) = -36 - 12 + 8 - 6 = -46.0%
        assert sim_crash["total_return_impact_pct"] == -46.0
        assert sim_crash["rupee_impact"] == -46000.0
        assert sim_crash["projected_capital"] == 54000.0

        # Maximum bull surge boundary
        shocks_bull = {"market": 30.0, "size": 20.0, "value": 20.0, "momentum": 20.0}
        sim_bull = simulate_factor_shocks(betas, shocks_bull, initial_capital=100000.0, is_percentage=True)
        # (1.2 * 30) + (0.6 * 20) + (-0.4 * 20) + (0.3 * 20) = 36 + 12 - 8 + 6 = +46.0%
        assert sim_bull["total_return_impact_pct"] == 46.0
        assert sim_bull["rupee_impact"] == 46000.0
        assert sim_bull["projected_capital"] == 146000.0

    def test_extreme_beta_magnitudes_plus_minus_5(self):
        """Stress tests extreme betas (beta = 5.0 and beta = -5.0)."""
        betas_high = {"market": 5.0, "size": 5.0, "value": 5.0, "momentum": 5.0}
        shocks = {"market": -30.0, "size": -20.0, "value": -10.0, "momentum": -10.0}

        # 5.0 * (-30 - 20 - 10 - 10) = 5.0 * -70.0 = -350.0%
        sim_high = simulate_factor_shocks(betas_high, shocks, initial_capital=100000.0, is_percentage=True)
        assert sim_high["total_return_impact_pct"] == -350.0
        assert sim_high["rupee_impact"] == -350000.0
        assert sim_high["projected_capital"] == -250000.0

        # Negative extreme beta (inverse fund)
        betas_inverse = {"market": -5.0, "size": -5.0, "value": -5.0, "momentum": -5.0}
        sim_inv = simulate_factor_shocks(betas_inverse, shocks, initial_capital=100000.0, is_percentage=True)
        assert sim_inv["total_return_impact_pct"] == 350.0
        assert sim_inv["rupee_impact"] == 350000.0
        assert sim_inv["projected_capital"] == 450000.0

    def test_zero_shock_invariance(self):
        """Zero shocks must produce exactly 0.0% return impact and 0 rupee capital erosion."""
        extreme_betas = {"market": 4.5, "size": -3.8, "value": 2.9, "momentum": -5.0}
        zero_shocks = {"market": 0.0, "size": 0.0, "value": 0.0, "momentum": 0.0}

        sim = simulate_factor_shocks(extreme_betas, zero_shocks, initial_capital=100000.0, is_percentage=True)
        assert sim["total_return_impact_pct"] == 0.0
        assert sim["rupee_impact"] == 0.0
        assert sim["projected_capital"] == 100000.0

    def test_zero_beta_invariance(self):
        """A fund with 0 betas across all factors has zero return impact under any shock."""
        zero_betas = {"market": 0.0, "size": 0.0, "value": 0.0, "momentum": 0.0}
        shocks = {"market": -30.0, "size": 20.0, "value": -20.0, "momentum": 20.0}

        sim = simulate_factor_shocks(zero_betas, shocks, initial_capital=100000.0, is_percentage=True)
        assert sim["total_return_impact_pct"] == 0.0
        assert sim["rupee_impact"] == 0.0
        assert sim["projected_capital"] == 100000.0

    def test_monte_carlo_client_vs_python_precision_harness(self):
        """Runs 1,000 randomized factor beta & shock vectors to verify floating-point precision parity."""
        rng = random.Random(42)
        base_capital = 100000.0

        for _ in range(1000):
            betas = {
                "market": round(rng.uniform(-5.0, 5.0), 3),
                "size": round(rng.uniform(-5.0, 5.0), 3),
                "value": round(rng.uniform(-5.0, 5.0), 3),
                "momentum": round(rng.uniform(-5.0, 5.0), 3),
            }
            shocks = {
                "market": round(rng.uniform(-30.0, 30.0), 1),
                "size": round(rng.uniform(-20.0, 20.0), 1),
                "value": round(rng.uniform(-20.0, 20.0), 1),
                "momentum": round(rng.uniform(-20.0, 20.0), 1),
            }

            # Python backend calculation
            py_res = simulate_factor_shocks(betas, shocks, initial_capital=base_capital, is_percentage=True)

            # Replicate client-side TS logic exactly:
            # deltaR = sum(beta * shock)
            # roundedDeltaR = Number(deltaR.toFixed(4))
            # rupeeImpact = Number((baseCapital * (roundedDeltaR / 100)).toFixed(2))
            # terminalVal = Number((baseCapital + rupeeImpact).toFixed(2))
            ts_delta_r = sum(betas[k] * shocks[k] for k in ["market", "size", "value", "momentum"])
            ts_rounded_delta_r = round(ts_delta_r, 4)
            ts_rupee_impact = round(base_capital * (ts_rounded_delta_r / 100.0), 2)
            ts_terminal = round(base_capital + ts_rupee_impact, 2)

            # Assert parity within institutional tolerance
            assert math.isclose(py_res["total_return_impact_pct"], ts_rounded_delta_r, abs_tol=1e-3)
            assert math.isclose(py_res["rupee_impact"], ts_rupee_impact, abs_tol=0.05)
            assert math.isclose(py_res["projected_capital"], ts_terminal, abs_tol=0.05)


# =====================================================================
# 2. Missing Factor Attribution (422 Error) & Fallback Verification
# =====================================================================

@pytest.fixture()
def api_client(pg_db, monkeypatch):
    """Test client configured for quant endpoint testing without background sync daemon."""
    monkeypatch.setattr(settings, "enable_sync_daemon", False)
    with TestClient(app) as client:
        yield client


class TestAdversarial422FallbackHandling:
    """Verifies backend 422 HTTP responses on insufficient history and client fallback behavior."""

    def test_insufficient_history_returns_422_with_clear_detail(self, api_client):
        """Requesting factor attribution with < 10 trading days returns 422."""
        con = connection.get_connection()
        try:
            con.execute("INSERT INTO schemes (scheme_code, scheme_name, fund_house, category, plan_type, option_type) VALUES (9001, 'Short History Fund', 'AMC', 'Equity Scheme - Large Cap', 'Direct', 'Growth') ON CONFLICT DO NOTHING;")
            # Insert only 5 trading days (minimum 10 required)
            con.execute("""
                INSERT INTO nav_history (scheme_code, nav_date, nav) VALUES
                (9001, '2025-01-01', 100.0),
                (9001, '2025-01-02', 100.5),
                (9001, '2025-01-03', 101.0),
                (9001, '2025-01-06', 100.8),
                (9001, '2025-01-07', 101.2)
                ON CONFLICT DO NOTHING;
            """)
        finally:
            con.close()

        resp = api_client.get("/api/quant/9001/factors", params={"start": "2025-01-01", "end": "2025-01-07"})
        assert resp.status_code == 422
        body = resp.json()
        detail = body["detail"]
        detail_s = detail if isinstance(detail, str) else str(detail)
        assert "Insufficient trading days" in detail_s or "FACTORS_UNAVAILABLE" in detail_s or "minimum 60" in detail_s

    def test_no_records_in_window_returns_422(self, api_client):
        """Requesting factor attribution outside fund date window returns 422."""
        con = connection.get_connection()
        try:
            con.execute("INSERT INTO schemes (scheme_code, scheme_name, fund_house, category, plan_type, option_type) VALUES (9002, 'Date Filter Scheme', 'AMC', 'Equity Scheme - Large Cap', 'Direct', 'Growth') ON CONFLICT DO NOTHING;")
            con.execute("""
                INSERT INTO nav_history (scheme_code, nav_date, nav) VALUES
                (9002, '2025-01-01', 100.0),
                (9002, '2025-01-02', 100.5),
                (9002, '2025-01-03', 101.0)
                ON CONFLICT DO NOTHING;
            """)
        finally:
            con.close()

        resp = api_client.get("/api/quant/9002/factors", params={"start": "2015-01-01", "end": "2015-01-10"})
        assert resp.status_code == 422
        body = resp.json()
        assert "No NAV records found" in body["detail"]

    def test_client_fallback_scenario_simulation_with_capm_beta(self):
        """When 422 error occurs, frontend falls back to CAPM Beta (e.g. 1.05) and 0 for others."""
        capm_fallback_betas = {"market": 1.05, "size": 0.0, "value": 0.0, "momentum": 0.0}
        covid_shocks = {"market": -30.0, "size": -15.0, "value": -10.0, "momentum": -12.0}

        # Fallback simulation isolates market shock correctly
        sim = simulate_factor_shocks(capm_fallback_betas, covid_shocks, initial_capital=100000.0, is_percentage=True)
        # 1.05 * -30.0 = -31.5%
        assert sim["total_return_impact_pct"] == -31.5
        assert sim["rupee_impact"] == -31500.0
        assert sim["projected_capital"] == 68500.0


# =====================================================================
# 3. Factor Waterfall Chart Contract & Sign Integrity
# =====================================================================

class TestAdversarialWaterfallChartContract:
    """Stress tests waterfall chart oracle and algebraic properties."""

    def test_waterfall_total_net_return_algebraic_identity(self):
        """Guarantees Net Return = Alpha + sum(Factor Returns) - abs(Fee Drag)."""
        alpha = 2.75
        factors = {"market": 14.5, "size": 3.2, "value": -2.1, "momentum": 1.8}
        fee_drag = 0.85

        spec = oracle_build_waterfall_chart_spec(alpha, factors, fee_drag, net_return_pct=alpha + sum(factors.values()) - fee_drag)
        trace = spec["data"][0]

        # First bar: Alpha
        assert trace["x"][0] == "Manager Alpha"
        assert trace["y"][0] == 2.75
        assert trace["measure"][0] == "relative"

        # TER Fee Drag bar: must be negative
        ter_idx = trace["x"].index("TER Fee Drag")
        assert trace["y"][ter_idx] == -0.85
        assert trace["measure"][ter_idx] == "relative"

        # Final bar: Net Return
        assert trace["x"][-1] == "Net Return"
        expected_net = round(alpha + sum(factors.values()) - fee_drag, 4)
        assert trace["y"][-1] == expected_net
        assert trace["measure"][-1] == "total"

    def test_waterfall_fee_drag_sign_robustness(self):
        """Passing negative fee drag still produces negative bar, never double-negative addition."""
        spec_pos = oracle_build_waterfall_chart_spec(1.0, {}, fee_drag_pct=0.75, net_return_pct=0.25)
        spec_neg = oracle_build_waterfall_chart_spec(1.0, {}, fee_drag_pct=-0.75, net_return_pct=0.25)

        ter_idx_pos = spec_pos["data"][0]["x"].index("TER Fee Drag")
        ter_idx_neg = spec_neg["data"][0]["x"].index("TER Fee Drag")

        assert spec_pos["data"][0]["y"][ter_idx_pos] == -0.75
        assert spec_neg["data"][0]["y"][ter_idx_neg] == -0.75


# =====================================================================
# 4. Macro Scenario Historical Crisis Replay Verification
# =====================================================================

class TestAdversarialHistoricalCrisisReplay:
    """Verifies that historical crisis replay definitions and outputs conform to project specifications."""

    def test_historical_scenario_definitions_conform_to_project_spec(self):
        """All 3 required macroeconomic crises are defined with verified parameters."""
        scenario_ids = [s["id"] for s in HISTORICAL_SCENARIOS]
        assert "covid_march_2020" in scenario_ids
        assert "rate_hike_2022" in scenario_ids
        assert "volatility_spike_2024" in scenario_ids

        # Verify COVID crisis parameters
        covid = next(s for s in HISTORICAL_SCENARIOS if s["id"] == "covid_march_2020")
        assert covid["start_date"] == "2020-02-15"
        assert covid["end_date"] == "2020-04-15"
        assert covid["benchmark_mdd_pct"] < -30.0

        # Verify Rate Hike crisis parameters
        rate_hike = next(s for s in HISTORICAL_SCENARIOS if s["id"] == "rate_hike_2022")
        assert rate_hike["start_date"] == "2022-01-03"
        assert rate_hike["end_date"] == "2022-06-20"

        # Verify Volatility Spike parameters
        vol_spike = next(s for s in HISTORICAL_SCENARIOS if s["id"] == "volatility_spike_2024")
        assert vol_spike["start_date"] == "2024-05-23"
        assert vol_spike["end_date"] == "2024-06-04"
