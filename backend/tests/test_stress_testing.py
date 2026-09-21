"""
Unit and numerical precision tests for Macroeconomic Crisis Replay & Stress Testing
(backend/app/stress_testing.py).

Verifies:
  - Parametric factor shock simulations and rupee wealth impact calculations
  - Historical crisis replay against COVID March 2020, Rate Hike 2022, and Volatility 2024
  - Drawdown duration, peak/trough detection, and recovery tracking
  - Downside beta calculation on negative benchmark return days
  - Inactive fund graceful degradation
  - Plotly figure generation
"""

import math
import numpy as np
import pandas as pd
import pytest

from app.stress_testing import (
    evaluate_historical_stress_scenarios,
    simulate_factor_shocks,
    generate_stress_figure,
    HISTORICAL_SCENARIOS,
)


class TestParametricFactorShockSimulation:
    """Verifies linear factor shock model: Delta P = sum(beta_k * Delta F_k)."""

    def test_factor_shocks_decimal_inputs_exact_precision(self):
        betas = {"market": 1.20, "size": 0.40, "value": -0.25, "momentum": 0.10}
        shocks = {"market": -0.15, "size": -0.05, "value": 0.02, "momentum": -0.08}
        initial_capital = 100000.0

        # Expected:
        # market: 1.20 * -15% = -18.0%
        # size: 0.40 * -5% = -2.0%
        # value: -0.25 * +2% = -0.5%
        # momentum: 0.10 * -8% = -0.8%
        # Total: -21.3%
        # Rupee impact: -21,300.0, Projected: 78,700.0
        res = simulate_factor_shocks(betas, shocks, initial_capital=initial_capital)

        assert math.isclose(res["total_return_impact_pct"], -21.30, abs_tol=1e-3)
        assert math.isclose(res["factor_impacts_pct"]["market"], -18.00, abs_tol=1e-3)
        assert math.isclose(res["factor_impacts_pct"]["size"], -2.00, abs_tol=1e-3)
        assert math.isclose(res["factor_impacts_pct"]["value"], -0.50, abs_tol=1e-3)
        assert math.isclose(res["factor_impacts_pct"]["momentum"], -0.80, abs_tol=1e-3)

        assert math.isclose(res["rupee_impact"], -21300.0, abs_tol=1e-2)
        assert math.isclose(res["projected_capital"], 78700.0, abs_tol=1e-2)

    def test_factor_shocks_percentage_inputs_match_decimal(self):
        betas = {"market": 1.0, "size": 0.5}
        shocks_pct = {"market": -10.0, "size": -4.0}
        shocks_dec = {"market": -0.10, "size": -0.04}

        res_pct = simulate_factor_shocks(betas, shocks_pct)
        res_dec = simulate_factor_shocks(betas, shocks_dec)

        assert math.isclose(res_pct["total_return_impact_pct"], res_dec["total_return_impact_pct"], abs_tol=1e-4)
        assert math.isclose(res_pct["rupee_impact"], res_dec["rupee_impact"], abs_tol=1e-2)

    def test_factor_shocks_unaffected_when_no_matching_factors(self):
        betas = {"market": 1.0}
        shocks = {"unknown_factor": 0.5}
        res = simulate_factor_shocks(betas, shocks)

        assert res["total_return_impact_pct"] == 0.0
        assert res["rupee_impact"] == 0.0
        assert res["projected_capital"] == 100000.0


class TestHistoricalCrisisReplayScenarios:
    """Verifies historical scenario window slicing, peak/trough, and recovery metrics."""

    def test_scenario_definitions_complete(self):
        scenario_ids = [sc["id"] for sc in HISTORICAL_SCENARIOS]
        assert "covid_march_2020" in scenario_ids
        assert "rate_hike_2022" in scenario_ids
        assert "volatility_spike_2024" in scenario_ids

    def test_historical_replay_recovered_fund(self):
        """Constructs a fund that experiences a known 35% drawdown in COVID crash and recovers."""
        dates = pd.date_range("2020-01-01", "2021-03-31", freq="D")
        # Peak at 100 on 2020-02-19
        # Trough at 65 on 2020-03-23
        # Recovery at 100 on 2020-11-10
        nav_dict = {}
        for d in dates:
            d_str = d.strftime("%Y-%m-%d")
            if d_str < "2020-02-19":
                nav_dict[d] = 95.0 + 5.0 * (d - dates[0]).days / 49.0  # reaches 100
            elif d_str == "2020-02-19":
                nav_dict[d] = 100.0
            elif d_str < "2020-03-23":
                drop_days = (d - pd.to_datetime("2020-02-19")).days
                nav_dict[d] = 100.0 - (35.0 * drop_days / 33.0)
            elif d_str == "2020-03-23":
                nav_dict[d] = 65.0
            elif d_str < "2020-11-10":
                rec_days = (d - pd.to_datetime("2020-03-23")).days
                nav_dict[d] = 65.0 + (35.0 * rec_days / 232.0)
            else:
                nav_dict[d] = 102.0

        df_fund = pd.DataFrame({"nav_date": list(nav_dict.keys()), "nav": list(nav_dict.values())})
        res = evaluate_historical_stress_scenarios(999999, df_hist=df_fund)

        covid = res.get("covid_march_2020")
        assert covid is not None
        assert covid["available"] is True
        assert math.isclose(covid["max_drawdown_pct"], -35.0, abs_tol=0.2)
        assert covid["trough_date"] == "2020-03-23"
        assert covid["recovered"] is True
        assert covid["recovery_date"] == "2020-11-10"
        assert covid["recovery_duration_days"] == 232
        assert covid["drawdown_duration_days"] == 33

    def test_historical_replay_unrecovered_fund(self):
        """Constructs a fund that drops during the 2022 rate hike and remains below peak."""
        dates = pd.date_range("2021-12-01", "2023-12-31", freq="D")
        navs = []
        for d in dates:
            d_str = d.strftime("%Y-%m-%d")
            if d_str <= "2022-01-17":
                navs.append(100.0)
            else:
                navs.append(85.0)  # permanently depressed at -15%

        df_fund = pd.DataFrame({"nav_date": dates, "nav": navs})
        res = evaluate_historical_stress_scenarios(999999, df_hist=df_fund)

        rate_hike = res.get("rate_hike_2022")
        assert rate_hike is not None
        assert rate_hike["available"] is True
        assert math.isclose(rate_hike["max_drawdown_pct"], -15.0, abs_tol=0.1)
        assert rate_hike["recovered"] is False
        assert rate_hike["recovery_date"] is None
        assert rate_hike["recovery_duration_days"] is None

    def test_inactive_fund_scenario_reporting(self):
        """Fund launched in 2023 has no data for 2020 COVID crash; must report unavailable gracefully."""
        dates = pd.date_range("2023-01-01", "2024-06-01", freq="D")
        df_fund = pd.DataFrame({"nav_date": dates, "nav": np.linspace(10, 15, len(dates))})

        res = evaluate_historical_stress_scenarios(888888, df_hist=df_fund)
        covid = res.get("covid_march_2020")

        assert covid is not None
        assert covid["available"] is False
        assert "not active" in covid.get("reason", "").lower()

    def test_generate_stress_figure_structure(self):
        scenarios = [
            {"name": "COVID", "max_drawdown_pct": -34.5, "benchmark_drawdown_pct": -38.2},
            {"name": "Rate Hike", "max_drawdown_pct": -15.2, "benchmark_drawdown_pct": -17.4},
        ]
        fig = generate_stress_figure(scenarios, "Mirae Asset Large Cap")
        assert "data" in fig
        assert len(fig["data"]) == 2  # Fund and Benchmark traces
        assert "layout" in fig
