"""
Unit and numerical precision tests for Fama-French-Carhart 4-Factor Risk Decomposition Engine
(backend/app/factor_model.py).

Verifies:
  - OLS parameter recovery against known true parameters within institutional precision tolerances
  - Standard errors, t-statistics, p-values, R-squared, and adjusted R-squared calculation
  - Systematic vs idiosyncratic risk decomposition
  - Euler systematic factor variance decomposition summing to exactly 100.0%
  - Waterfall attribution balancing
  - Edge cases (insufficient data, singular design matrix, missing columns)
"""

import math
import numpy as np
import pandas as pd
import pytest
import scipy.stats as stats

from app.factor_model import (
    compute_multivariate_factor_attribution,
    build_factor_figures,
)


def _generate_synthetic_factor_series(
    n: int = 300,
    seed: int = 42,
    true_alpha_daily: float = 0.0002,
    true_betas: tuple = (1.10, 0.30, -0.20, 0.15),
    noise_std: float = 0.002,
    rf_daily: float = 0.00025,
) -> tuple[pd.Series, pd.DataFrame]:
    """Generates synthetic daily factor returns and fund returns with known parameters."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-01", periods=n, freq="B")

    mkt = rng.normal(0.0006, 0.010, n)
    smb = rng.normal(0.0001, 0.006, n)
    hml = rng.normal(-0.0001, 0.005, n)
    wml = rng.normal(0.0002, 0.007, n)
    noise = rng.normal(0.0, noise_std, n)
    noise = noise - np.mean(noise)

    b_mkt, b_smb, b_hml, b_wml = true_betas
    X_raw = np.column_stack([mkt, smb, hml, wml])
    X = np.column_stack([np.ones(n), X_raw])
    noise = noise - X @ (np.linalg.pinv(X) @ noise)

    excess_fund = true_alpha_daily + b_mkt * mkt + b_smb * smb + b_hml * hml + b_wml * wml + noise
    fund_ret = pd.Series(excess_fund + rf_daily, index=dates)

    factors_df = pd.DataFrame(
        {"mkt_excess": mkt, "smb": smb, "hml": hml, "wml": wml},
        index=dates,
    )
    return fund_ret, factors_df


class TestFactorModelParameterRecovery:
    """Verifies that OLS regression reliably recovers known underlying parameters."""

    def test_parameter_recovery_close_to_ground_truth(self):
        true_alpha = 0.00015  # ~3.78% annualized
        true_betas = (1.15, 0.35, -0.25, 0.20)
        rf_daily = 0.00025

        fund_ret, factors_df = _generate_synthetic_factor_series(
            n=500,
            seed=123,
            true_alpha_daily=true_alpha,
            true_betas=true_betas,
            noise_std=0.0015,
            rf_daily=rf_daily,
        )

        res = compute_multivariate_factor_attribution(fund_ret, factors_df, rf_daily=rf_daily)
        betas = res["factor_betas"]

        assert math.isclose(betas["market"], true_betas[0], abs_tol=0.04)
        assert math.isclose(betas["size"], true_betas[1], abs_tol=0.04)
        assert math.isclose(betas["value"], true_betas[2], abs_tol=0.04)
        assert math.isclose(betas["momentum"], true_betas[3], abs_tol=0.04)

        expected_alpha_ann = true_alpha * 252.0 * 100.0
        assert math.isclose(res["alpha_annualized_pct"], expected_alpha_ann, abs_tol=0.5)

    def test_statistical_diagnostics_precision(self):
        """Checks t-stats, p-values, R-squared formulas against manual numpy/scipy calculation."""
        fund_ret, factors_df = _generate_synthetic_factor_series(n=150, seed=777)
        rf_daily = 0.00025

        res = compute_multivariate_factor_attribution(fund_ret, factors_df, rf_daily=rf_daily)

        # Re-compute manually with direct OLS formulas
        y = (fund_ret.values - rf_daily).astype(float)
        X_raw = factors_df[["mkt_excess", "smb", "hml", "wml"]].values.astype(float)
        X = np.column_stack([np.ones(len(y)), X_raw])

        beta_manual = np.linalg.inv(X.T @ X) @ (X.T @ y)
        residuals = y - (X @ beta_manual)
        sse = np.sum(residuals ** 2)
        df_e = len(y) - 5
        s2 = sse / df_e
        se_manual = np.sqrt(np.diag(s2 * np.linalg.inv(X.T @ X)))
        t_manual = beta_manual / se_manual
        p_manual = 2.0 * stats.t.sf(np.abs(t_manual), df=df_e)

        sst = np.sum((y - np.mean(y)) ** 2)
        r2_manual = 1.0 - (sse / sst)
        adj_r2_manual = 1.0 - (1.0 - r2_manual) * (len(y) - 1) / df_e

        # Verify precision to 4 decimal places
        assert math.isclose(res["alpha_daily"], round(beta_manual[0], 6), abs_tol=1e-5)
        assert math.isclose(res["r_squared"], round(r2_manual, 4), abs_tol=1e-4)
        assert math.isclose(res["adj_r_squared"], round(adj_r2_manual, 4), abs_tol=1e-4)

        assert math.isclose(res["t_stats"]["alpha"], round(t_manual[0], 4), abs_tol=1e-4)
        assert math.isclose(res["t_stats"]["market"], round(t_manual[1], 4), abs_tol=1e-4)
        assert math.isclose(res["p_values"]["market"], round(p_manual[1], 4), abs_tol=1e-4)


class TestFactorVarianceDecomposition:
    """Verifies systematic vs idiosyncratic variance split and factor risk contribution."""

    def test_euler_variance_decomposition_sums_to_100(self):
        fund_ret, factors_df = _generate_synthetic_factor_series(n=250, seed=999)
        res = compute_multivariate_factor_attribution(fund_ret, factors_df)

        decomp = res["variance_decomposition"]
        assert "market" in decomp
        assert "size" in decomp
        assert "value" in decomp
        assert "momentum" in decomp

        total_decomp = sum(decomp.values())
        assert math.isclose(total_decomp, 100.0, abs_tol=1e-2), f"Decomposition sum {total_decomp} != 100%"

    def test_systematic_and_idiosyncratic_risk_sum_to_100(self):
        fund_ret, factors_df = _generate_synthetic_factor_series(n=200, seed=456)
        res = compute_multivariate_factor_attribution(fund_ret, factors_df)

        sys_pct = res["systematic_risk_pct"]
        idio_pct = res["idiosyncratic_risk_pct"]

        assert 0.0 <= sys_pct <= 100.0
        assert 0.0 <= idio_pct <= 100.0
        assert math.isclose(sys_pct + idio_pct, 100.0, abs_tol=1e-3)


class TestWaterfallAttributionStructure:
    """Verifies Plotly-compatible waterfall schema and data integrity."""

    def test_waterfall_balances_and_contains_expected_fields(self):
        fund_ret, factors_df = _generate_synthetic_factor_series(n=180, seed=321)
        res = compute_multivariate_factor_attribution(fund_ret, factors_df)

        wf = res["waterfall_data"]
        assert "labels" in wf
        assert "values" in wf
        assert "measures" in wf
        assert "text" in wf

        labels = wf["labels"]
        values = wf["values"]
        measures = wf["measures"]

        assert len(labels) == 6
        assert len(values) == 6
        assert len(measures) == 6

        assert labels[0] == "Alpha (Manager Skill)"
        assert labels[-1] == "Net Excess Return"
        assert measures[-1] == "total"

        # Check total net excess return matches sum of components within rounding
        sum_components = sum(values[:-1])
        assert math.isclose(sum_components, values[-1], abs_tol=1e-2)

    def test_build_factor_figures_produces_valid_plotly_json(self):
        fund_ret, factors_df = _generate_synthetic_factor_series(n=120, seed=654)
        res = compute_multivariate_factor_attribution(fund_ret, factors_df)

        figs = build_factor_figures(res, "HDFC Top 100 Fund")
        assert "factor_waterfall" in figs
        assert "factor_decomposition" in figs

        wf_fig = figs["factor_waterfall"]
        assert "data" in wf_fig and len(wf_fig["data"]) > 0
        assert "layout" in wf_fig


class TestFactorModelEdgeCases:
    """Verifies edge conditions and error paths."""

    def test_insufficient_data_raises_value_error(self):
        dates = pd.date_range("2024-01-01", periods=8, freq="D")
        fund_ret = pd.Series([0.01] * 8, index=dates)
        factors_df = pd.DataFrame(
            {"mkt_excess": [0.01] * 8, "smb": [0.0] * 8, "hml": [0.0] * 8, "wml": [0.0] * 8},
            index=dates,
        )
        with pytest.raises(ValueError, match="Insufficient overlapping observations"):
            compute_multivariate_factor_attribution(fund_ret, factors_df)

    def test_missing_required_column_raises_value_error(self):
        dates = pd.date_range("2024-01-01", periods=80, freq="D")
        fund_ret = pd.Series([0.01] * 80, index=dates)
        factors_df = pd.DataFrame(
            {"smb": [0.0] * 80, "hml": [0.0] * 80, "wml": [0.0] * 80},  # missing mkt_excess
            index=dates,
        )
        with pytest.raises(ValueError, match="missing required column"):
            compute_multivariate_factor_attribution(fund_ret, factors_df)

    def test_omitted_wml_runs_three_factor_model(self):
        dates = pd.date_range("2024-01-01", periods=80, freq="D")
        fund_ret = pd.Series(np.linspace(0.01, 0.02, 80), index=dates)
        factors_df = pd.DataFrame(
            {
                "mkt_excess": np.linspace(-0.01, 0.01, 80),
                "smb": np.linspace(-0.005, 0.005, 80),
                "hml": np.linspace(0.002, -0.002, 80),
            },
            index=dates,
        )
        res = compute_multivariate_factor_attribution(fund_ret, factors_df)
        assert "momentum" not in res["factor_betas"]
        assert "market" in res["factor_betas"]

    def test_collinear_or_singular_matrix_handled_without_crash(self):
        """Verifies pseudo-inverse fallback when factors are perfectly collinear."""
        dates = pd.date_range("2024-01-01", periods=80, freq="D")
        fund_ret = pd.Series(np.linspace(0.01, 0.02, 80), index=dates)
        collinear_vec = np.linspace(-0.01, 0.01, 80)
        factors_df = pd.DataFrame(
            {
                "mkt_excess": collinear_vec,
                "smb": collinear_vec * 2.0,  # exactly collinear
                "hml": collinear_vec * -1.0,  # exactly collinear
                "wml": collinear_vec * 0.5,   # exactly collinear
            },
            index=dates,
        )
        # Must execute cleanly without unhandled LinAlgError crash
        res = compute_multivariate_factor_attribution(fund_ret, factors_df)
        assert res["r_squared"] >= 0.0
        assert res["n_observations"] == 80
