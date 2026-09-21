"""Authoritative mathematical oracles and production adapters for E2E testing.
Conforms strictly to specifications and interface contracts defined in
PROJECT.md, ORIGINAL_REQUEST.md, and TEST_INFRA.md.
"""

from __future__ import annotations

import datetime
import importlib
import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats
from scipy.cluster.hierarchy import linkage


# =====================================================================
# Feature 1: Multi-Factor Risk Decomposition Oracle & Adapter
# =====================================================================

def oracle_factor_attribution(
    fund_returns: pd.Series,
    factor_returns: pd.DataFrame,  # columns: ['mkt_excess', 'smb', 'hml', 'wml']
    rf_daily: float = 0.00025,
) -> Dict[str, Any]:
    """OLS multivariate regression: R_i - Rf = alpha + beta_mkt*MKT + beta_smb*SMB + beta_hml*HML + beta_wml*WML + eps."""
    aligned = pd.concat([fund_returns.rename("fund"), factor_returns], axis=1).dropna()
    if len(aligned) < 5:
        raise ValueError("Insufficient data points for 4-factor regression (need >= 5)")

    y = aligned["fund"].values - rf_daily
    factors = [c for c in ["mkt_excess", "smb", "hml", "wml"] if c in aligned.columns]
    X_raw = aligned[factors].values
    n, k = X_raw.shape

    # Design matrix with intercept
    X = np.column_stack([np.ones(n), X_raw])

    # OLS estimation via pseudo-inverse to handle collinearity gracefully
    beta_hat = np.linalg.pinv(X.T @ X) @ (X.T @ y)
    y_pred = X @ beta_hat
    residuals = y - y_pred

    # Degrees of freedom and variance of residuals
    df_resid = max(1, n - (k + 1))
    s2 = np.sum(residuals**2) / df_resid
    try:
        var_covar_beta = s2 * np.linalg.inv(X.T @ X)
        se_beta = np.sqrt(np.diag(var_covar_beta))
    except np.linalg.LinAlgError:
        var_covar_beta = s2 * np.linalg.pinv(X.T @ X)
        se_beta = np.sqrt(np.maximum(1e-12, np.diag(var_covar_beta)))

    t_stats = beta_hat / np.where(se_beta > 1e-12, se_beta, 1e-12)
    p_values = [float(2 * (1 - stats.t.cdf(np.abs(t), df=df_resid))) for t in t_stats]

    ss_tot = np.sum((y - np.mean(y)) ** 2)
    ss_res = np.sum(residuals**2)
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 1e-12 else 0.0
    adj_r2 = 1.0 - (1.0 - r2) * (n - 1) / df_resid if df_resid > 0 else r2

    alpha_daily = float(beta_hat[0])
    alpha_ann_pct = float(alpha_daily * 252 * 100)

    factor_betas = {factors[i]: float(beta_hat[i + 1]) for i in range(k)}
    t_stat_dict = {"alpha": float(t_stats[0])}
    t_stat_dict.update({factors[i]: float(t_stats[i + 1]) for i in range(k)})
    p_val_dict = {"alpha": float(p_values[0])}
    p_val_dict.update({factors[i]: float(p_values[i + 1]) for i in range(k)})

    # Variance decomposition
    total_var = np.var(y, ddof=1) if len(y) > 1 else 1e-6
    resid_var = np.var(residuals, ddof=1) if len(residuals) > 1 else 0.0
    sys_var = max(0.0, total_var - resid_var)
    idiosyncratic_risk_pct = float((resid_var / total_var) * 100.0) if total_var > 1e-12 else 0.0

    # Variance explained by each factor
    var_decomp = {}
    if sys_var > 1e-12:
        cov_factors = np.cov(X_raw.T) if k > 1 else np.array([[np.var(X_raw, ddof=1)]])
        if cov_factors.ndim == 0:
            cov_factors = np.array([[float(cov_factors)]])
        factor_b = beta_hat[1:]
        marginal_vars = factor_b * (cov_factors @ factor_b)
        sum_marginal = np.sum(marginal_vars) if np.sum(marginal_vars) > 1e-12 else 1.0
        for i, fac in enumerate(factors):
            var_decomp[fac] = float(max(0.0, marginal_vars[i] / sum_marginal * 100.0))
    else:
        for fac in factors:
            var_decomp[fac] = 0.0

    # Waterfall data: components
    waterfall_measures = ["relative"] * (k + 2) + ["total"]
    waterfall_x = ["Alpha"] + [f.upper() for f in factors] + ["Fee Drag", "Net Return"]
    mean_factors = np.mean(X_raw, axis=0) if n > 0 else np.zeros(k)
    factor_contribs = [float(beta_hat[i + 1] * mean_factors[i] * 252 * 100) for i in range(k)]
    fee_drag_est = -0.75  # ~75 bps benchmark drag
    net_return_ann = alpha_ann_pct + sum(factor_contribs) + fee_drag_est
    waterfall_y = [alpha_ann_pct] + factor_contribs + [fee_drag_est, net_return_ann]

    return {
        "alpha_annualized_pct": round(alpha_ann_pct, 4),
        "t_stats": {k: round(v, 4) for k, v in t_stat_dict.items()},
        "p_values": {k: round(v, 4) for k, v in p_val_dict.items()},
        "r_squared": round(float(r2), 4),
        "adj_r_squared": round(float(adj_r2), 4),
        "factor_betas": {k: round(v, 4) for k, v in factor_betas.items()},
        "variance_decomposition": {k: round(v, 2) for k, v in var_decomp.items()},
        "idiosyncratic_risk_pct": round(idiosyncratic_risk_pct, 2),
        "waterfall_data": {
            "x": waterfall_x,
            "y": [round(val, 4) for val in waterfall_y],
            "measure": waterfall_measures,
        },
    }


def get_factor_attribution_fn():
    mod = importlib.import_module("app.factor_model")
    return getattr(mod, "compute_multivariate_factor_attribution")


# =====================================================================
# Feature 2: Macro Scenario Stress Testing Oracle & Adapter
# =====================================================================

HISTORICAL_SCENARIOS = {
    "covid_march_2020": {
        "name": "COVID-19 Market Crash (March 2020)",
        "start_date": "2020-02-15",
        "end_date": "2020-04-15",
        "peak_date": "2020-01-20",
        "trough_date": "2020-03-23",
        "benchmark_mdd_pct": -38.44,
    },
    "rate_hike_2022": {
        "name": "Global Rate Hike & Inflation Shock (2022)",
        "start_date": "2022-01-03",
        "end_date": "2022-06-20",
        "peak_date": "2022-01-17",
        "trough_date": "2022-06-17",
        "benchmark_mdd_pct": -17.80,
    },
    "volatility_spike_2024": {
        "name": "Lok Sabha Election Volatility Spike (June 2024)",
        "start_date": "2024-05-23",
        "end_date": "2024-06-04",
        "peak_date": "2024-06-03",
        "trough_date": "2024-06-04",
        "benchmark_mdd_pct": -5.93,
    },
}


def oracle_evaluate_historical_stress_scenarios(
    nav_series: pd.Series,  # Index must be DatetimeIndex or date string
    benchmark_series: Optional[pd.Series] = None,
) -> Dict[str, Any]:
    """Replays scheme drawdown and recovery against historical macroeconomic crisis windows."""
    results = {}
    df = pd.DataFrame({"nav": nav_series})
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()

    bench_df = None
    if benchmark_series is not None:
        bench_df = pd.DataFrame({"bench": benchmark_series})
        bench_df.index = pd.to_datetime(bench_df.index)
        bench_df = bench_df.sort_index()

    for key, sc in HISTORICAL_SCENARIOS.items():
        s_date = pd.to_datetime(sc["start_date"])
        e_date = pd.to_datetime(sc["end_date"])
        sub = df.loc[(df.index >= s_date) & (df.index <= e_date)]

        if len(sub) < 2:
            results[key] = {
                "scenario": sc["name"],
                "has_data": False,
                "drawdown_pct": None,
                "recovery_days": None,
                "benchmark_mdd_pct": sc["benchmark_mdd_pct"],
                "downside_beta": None,
            }
            continue

        navs = sub["nav"].values
        peak = np.maximum.accumulate(navs)
        dd = (navs - peak) / peak
        mdd_pct = float(np.min(dd) * 100.0)

        # Recovery duration
        trough_idx = int(np.argmin(dd))
        peak_idx = int(np.argmax(navs[: trough_idx + 1])) if trough_idx > 0 else 0
        peak_val = navs[peak_idx]

        recovery_days = None
        for i in range(trough_idx, len(navs)):
            if navs[i] >= peak_val:
                recovery_days = (sub.index[i] - sub.index[trough_idx]).days
                break

        # Downside beta vs benchmark if provided
        downside_beta = 1.0
        if bench_df is not None:
            sub_b = bench_df.loc[(bench_df.index >= s_date) & (bench_df.index <= e_date)]
            merged = pd.concat([sub["nav"].pct_change(), sub_b["bench"].pct_change()], axis=1).dropna()
            if len(merged) > 3:
                down_days = merged[merged.iloc[:, 1] < 0]
                if len(down_days) > 2 and np.var(down_days.iloc[:, 1]) > 1e-8:
                    downside_beta = float(
                        np.cov(down_days.iloc[:, 0], down_days.iloc[:, 1])[0, 1] / np.var(down_days.iloc[:, 1])
                    )

        results[key] = {
            "scenario": sc["name"],
            "has_data": True,
            "drawdown_pct": round(mdd_pct, 2),
            "recovery_days": recovery_days,
            "benchmark_mdd_pct": sc["benchmark_mdd_pct"],
            "downside_beta": round(downside_beta, 4),
        }

    return results


def oracle_simulate_factor_shocks(
    factor_betas: Dict[str, float],
    shocks: Dict[str, float],
) -> Dict[str, float]:
    """Calculates expected return impact: Delta P = sum(beta_k * Delta F_k)."""
    total_impact = 0.0
    factor_impacts = {}
    for factor, shock in shocks.items():
        beta = factor_betas.get(factor, 0.0)
        impact = beta * shock
        factor_impacts[f"{factor}_impact_pct"] = round(impact * 100.0, 4)
        total_impact += impact

    factor_impacts["portfolio_impact_pct"] = round(total_impact * 100.0, 4)
    return factor_impacts


def get_stress_testing_fn():
    mod = importlib.import_module("app.stress_testing")
    orig = getattr(mod, "evaluate_historical_stress_scenarios")

    def wrapper(nav_or_code, benchmark_series=None, df_hist=None):
        if isinstance(nav_or_code, (pd.Series, pd.DataFrame)):
            if isinstance(nav_or_code, pd.Series):
                dates = pd.to_datetime(nav_or_code.index)
                df_h = pd.DataFrame({"nav_date": dates, "nav": nav_or_code.values.astype(float)})
            else:
                df_h = nav_or_code.copy()
                if "nav_date" not in df_h.columns and isinstance(df_h.index, pd.DatetimeIndex):
                    df_h["nav_date"] = pd.to_datetime(df_h.index)
            raw = orig(118482, df_hist=df_h, benchmark_series=benchmark_series)
        else:
            raw = orig(nav_or_code, benchmark_series=benchmark_series)
        if isinstance(raw, dict) and "scenarios" in raw:
            scenarios = raw["scenarios"]
            keyed = dict(raw)
            if isinstance(scenarios, list):
                for s in scenarios:
                    sid = s.get("scenario_id") or s.get("id")
                    if sid:
                        keyed[sid] = s
            elif isinstance(scenarios, dict):
                keyed.update(scenarios)
            return keyed
        return raw

    return wrapper


def get_factor_shocks_fn():
    try:
        mod = importlib.import_module("app.stress_testing")
        if hasattr(mod, "simulate_factor_shocks"):
            orig = getattr(mod, "simulate_factor_shocks")

            def wrapper(factor_betas, shocks, initial_capital=100000.0):
                res = orig(factor_betas, shocks, initial_capital=initial_capital)
                if isinstance(res, dict):
                    if "total_return_impact_pct" in res and "portfolio_impact_pct" not in res:
                        res["portfolio_impact_pct"] = res["total_return_impact_pct"]
                    if "factor_impacts_pct" in res:
                        for k, v in res["factor_impacts_pct"].items():
                            res[f"{k}_impact_pct"] = v
                return res

            return wrapper
    except ImportError:
        raise


# =====================================================================
# Feature 3: Cornish-Fisher VaR Oracle & Adapter
# =====================================================================

def oracle_cornish_fisher_var(
    returns: np.ndarray,
    confidence_levels: List[float] = [0.95, 0.99],
) -> Dict[str, float]:
    """Cornish-Fisher expansion adjusting VaR for skewness and excess kurtosis."""
    clean = returns[~np.isnan(returns)]
    if len(clean) < 5:
        raise ValueError("Insufficient return data for Cornish-Fisher VaR calculation (need >= 5)")

    mu = float(np.mean(clean))
    sigma = float(np.std(clean, ddof=1))
    if sigma < 1e-12:
        return {f"var_{int(c*100)}_cf_daily_pct": 0.0 for c in confidence_levels}

    s = float(stats.skew(clean))
    k = float(stats.kurtosis(clean))  # Excess kurtosis (normal = 0)

    out = {
        "skewness": round(s, 4),
        "kurtosis": round(k, 4),
        "mean_daily_pct": round(mu * 100.0, 4),
        "vol_daily_pct": round(sigma * 100.0, 4),
    }

    for alpha_conf in confidence_levels:
        tag = int(round(alpha_conf * 100))
        # Standard normal quantile for the lower tail (1 - confidence)
        p = 1.0 - alpha_conf
        z = stats.norm.ppf(p)  # e.g., -1.64485 for 95%, -2.32635 for 99%

        # Cornish-Fisher expansion for the z quantile
        w = (
            z
            + (1.0 / 6.0) * (z**2 - 1.0) * s
            + (1.0 / 24.0) * (z**3 - 3.0 * z) * k
            - (1.0 / 36.0) * (2.0 * z**3 - 5.0 * z) * (s**2)
        )

        # VaR as daily return loss: VaR = - (mu + w * sigma) or expressed as negative return
        var_cf_daily = -(mu + w * sigma)
        var_cf_ann = var_cf_daily * math.sqrt(252.0)

        # Also compute standard Gaussian VaR for comparison
        var_gaussian = -(mu + z * sigma)

        out[f"var_{tag}_cf_daily_pct"] = round(float(var_cf_daily * 100.0), 4)
        out[f"var_{tag}_cf_ann_pct"] = round(float(var_cf_ann * 100.0), 4)
        out[f"var_{tag}_gaussian_daily_pct"] = round(float(var_gaussian * 100.0), 4)
        out[f"var_{tag}_conservative_premium_pct"] = round(float((var_cf_daily - var_gaussian) * 100.0), 4)

    return out


def get_cornish_fisher_var_fn():
    mod = importlib.import_module("app.quant_analytics")
    return getattr(mod, "compute_cornish_fisher_var")


# =====================================================================
# Feature 4: Expected Shortfall (CVaR) & Capture Analytics Oracle
# =====================================================================

def oracle_expected_shortfall_and_capture(
    fund_returns: np.ndarray,
    bench_returns: Optional[np.ndarray] = None,
    nav_series: Optional[pd.Series] = None,
    confidence_level: float = 0.99,
) -> Dict[str, Any]:
    """Calculates 99% CVaR (Expected Shortfall), drawdown duration/recovery, and downside capture efficiency."""
    clean_f = fund_returns[~np.isnan(fund_returns)]
    if len(clean_f) < 5:
        raise ValueError("Insufficient data points for CVaR")

    # 1. 99% CVaR / Expected Shortfall
    p_cutoff = (1.0 - confidence_level) * 100.0
    var_threshold = np.percentile(clean_f, p_cutoff)
    tail = clean_f[clean_f <= var_threshold]
    cvar_daily = float(np.mean(tail)) if len(tail) > 0 else float(var_threshold)
    cvar_ann = cvar_daily * math.sqrt(252.0)

    # 2. Maximum Drawdown duration & recovery if nav_series given
    dd_metrics = {
        "max_drawdown_pct": 0.0,
        "drawdown_duration_days": 0,
        "recovery_duration_days": None,
        "peak_date": None,
        "trough_date": None,
    }
    if nav_series is not None and len(nav_series) > 2:
        s = pd.Series(nav_series).dropna()
        s.index = pd.to_datetime(s.index)
        s = s.sort_index()
        navs = s.values
        peaks = np.maximum.accumulate(navs)
        drawdowns = (navs - peaks) / peaks
        trough_idx = int(np.argmin(drawdowns))
        mdd_pct = float(drawdowns[trough_idx] * 100.0)

        peak_idx = int(np.argmax(navs[: trough_idx + 1])) if trough_idx > 0 else 0
        peak_val = navs[peak_idx]

        recovery_idx = None
        for i in range(trough_idx + 1, len(navs)):
            if navs[i] >= peak_val:
                recovery_idx = i
                break

        dd_duration = (s.index[trough_idx] - s.index[peak_idx]).days
        rec_duration = (s.index[recovery_idx] - s.index[trough_idx]).days if recovery_idx is not None else None

        dd_metrics = {
            "max_drawdown_pct": round(mdd_pct, 4),
            "drawdown_duration_days": int(dd_duration),
            "recovery_duration_days": int(rec_duration) if rec_duration is not None else None,
            "peak_date": str(s.index[peak_idx].date()),
            "trough_date": str(s.index[trough_idx].date()),
        }

    # 3. Downside capture efficiency
    capture_metrics = {
        "downside_capture_ratio": 1.0,
        "upside_capture_ratio": 1.0,
        "capture_efficiency": 1.0,
    }
    if bench_returns is not None and len(bench_returns) == len(clean_f):
        df_cb = pd.DataFrame({"fund": clean_f, "bench": bench_returns}).dropna()
        down_days = df_cb[df_cb["bench"] < 0]
        up_days = df_cb[df_cb["bench"] > 0]

        if len(down_days) > 0:
            fund_down_comp = float(np.prod(1.0 + down_days["fund"]) - 1.0)
            bench_down_comp = float(np.prod(1.0 + down_days["bench"]) - 1.0)
            if abs(bench_down_comp) > 1e-8:
                downside_capture = (fund_down_comp / bench_down_comp) * 100.0
                capture_metrics["downside_capture_ratio"] = round(downside_capture, 2)

        if len(up_days) > 0:
            fund_up_comp = float(np.prod(1.0 + up_days["fund"]) - 1.0)
            bench_up_comp = float(np.prod(1.0 + up_days["bench"]) - 1.0)
            if abs(bench_up_comp) > 1e-8:
                upside_capture = (fund_up_comp / bench_up_comp) * 100.0
                capture_metrics["upside_capture_ratio"] = round(upside_capture, 2)

        if capture_metrics["downside_capture_ratio"] > 1e-4:
            capture_metrics["capture_efficiency"] = round(
                capture_metrics["upside_capture_ratio"] / capture_metrics["downside_capture_ratio"], 4
            )

    return {
        "cvar_99_daily_pct": round(float(cvar_daily * 100.0), 4),
        "cvar_99_ann_pct": round(float(cvar_ann * 100.0), 4),
        "var_99_daily_pct": round(float(var_threshold * 100.0), 4),
        "drawdown_metrics": dd_metrics,
        "capture_metrics": capture_metrics,
    }


def get_cvar_and_drawdown_fn():
    mod = importlib.import_module("app.quant_analytics")
    return getattr(mod, "compute_expected_shortfall_and_capture")


# =====================================================================
# Feature 5: Hierarchical Risk Parity (HRP) Oracle & Adapter
# =====================================================================

def _quasi_diag(linkage_matrix: np.ndarray) -> List[int]:
    """Quasi-diagonalization: reorders asset indices to place correlated assets adjacent."""
    link = linkage_matrix.astype(int)
    num_leaves = link[-1, 3]
    order = [link[-1, 0], link[-1, 1]]
    while max(order) >= num_leaves:
        new_order = []
        for item in order:
            if item >= num_leaves:
                row = item - num_leaves
                new_order.append(link[row, 0])
                new_order.append(link[row, 1])
            else:
                new_order.append(item)
        order = new_order
    return order


def _get_cluster_var(cov: np.ndarray, cluster_items: List[int]) -> float:
    """Calculates variance of an inverse-variance weighted sub-cluster."""
    sub_cov = cov[np.ix_(cluster_items, cluster_items)]
    inv_diag = 1.0 / np.diag(sub_cov)
    inv_diag[np.isnan(inv_diag) | np.isinf(inv_diag)] = 0.0
    sum_inv = np.sum(inv_diag)
    w = (inv_diag / sum_inv) if sum_inv > 1e-12 else np.ones(len(cluster_items)) / len(cluster_items)
    return float(w @ sub_cov @ w)


def _recursive_bisection(cov: np.ndarray, ordered_items: List[int]) -> pd.Series:
    """Recursive bisection distributing risk inverse to cluster variance."""
    weights = pd.Series(1.0, index=ordered_items)
    clusters = [ordered_items]

    while len(clusters) > 0:
        next_clusters = []
        for cluster in clusters:
            if len(cluster) > 1:
                mid = len(cluster) // 2
                left = cluster[:mid]
                right = cluster[mid:]

                var_l = _get_cluster_var(cov, left)
                var_r = _get_cluster_var(cov, right)

                total_var = var_l + var_r
                alpha = 1.0 - (var_l / total_var) if total_var > 1e-12 else 0.5

                weights[left] *= alpha
                weights[right] *= 1.0 - alpha

                if len(left) > 1:
                    next_clusters.append(left)
                if len(right) > 1:
                    next_clusters.append(right)
        clusters = next_clusters

    return weights


def oracle_optimize_hrp(
    cov_matrix: np.ndarray,
    corr_matrix: np.ndarray,
    asset_names: List[str],
) -> Dict[str, Any]:
    """Hierarchical Risk Parity (HRP) tree clustering, quasi-diagonalization, and recursive bisection."""
    n = len(asset_names)
    if n == 1:
        return {
            "weights": {asset_names[0]: 1.0},
            "cluster_order": asset_names,
            "portfolio_vol_ann": float(math.sqrt(cov_matrix[0, 0] * 252.0)),
        }

    # 1. Distance matrix: D_ij = sqrt(0.5 * (1 - rho_ij))
    dist = np.sqrt(np.clip(0.5 * (1.0 - corr_matrix), 0.0, 1.0))
    # Condensed distance for scipy linkage
    condensed = dist[np.triu_indices(n, k=1)]

    # 2. Single-linkage clustering
    link = linkage(condensed, method="single")

    # 3. Quasi-diagonalization
    order_indices = _quasi_diag(link)
    cluster_order = [asset_names[i] for i in order_indices]

    # 4. Recursive bisection
    w_series = _recursive_bisection(cov_matrix, order_indices)
    weights = {asset_names[i]: float(w_series[i]) for i in range(n)}

    # Ensure exact sum to 1.0
    total_w = sum(weights.values())
    weights = {k: float(v / total_w) for k, v in weights.items()}

    # Compute portfolio volatility
    w_arr = np.array([weights[a] for a in asset_names])
    port_var = float(w_arr @ cov_matrix @ w_arr)
    port_vol_ann = math.sqrt(max(0.0, port_var * 252.0))

    return {
        "weights": {k: round(v, 6) for k, v in weights.items()},
        "cluster_order": cluster_order,
        "portfolio_vol_ann": round(port_vol_ann, 4),
    }


def get_hrp_fn():
    mod = importlib.import_module("app.portfolio_hrp")
    return getattr(mod, "optimize_hrp")


# =====================================================================
# Feature 6: Black-Litterman Allocation Oracle & Adapter
# =====================================================================

def oracle_optimize_black_litterman(
    cov_matrix: np.ndarray,
    prior_weights: np.ndarray,
    views_matrix_P: np.ndarray,
    views_returns_Q: np.ndarray,
    risk_aversion: float = 2.5,
    tau: float = 0.05,
    views_confidences: Optional[np.ndarray] = None,
) -> Dict[str, Any]:
    """Bayesian Black-Litterman optimization blending equilibrium prior returns with investor tactical views."""
    cov = np.asarray(cov_matrix, dtype=float)
    w_mkt = np.asarray(prior_weights, dtype=float)
    P = np.asarray(views_matrix_P, dtype=float)
    Q = np.asarray(views_returns_Q, dtype=float)

    n = len(w_mkt)

    # Implied equilibrium market prior returns: Pi = lambda * Sigma * w_mkt
    pi = risk_aversion * (cov @ w_mkt)

    # Views uncertainty matrix Omega
    if views_confidences is not None:
        omega_diag = [
            float((1.0 - conf) / max(conf, 1e-4) * (P[k] @ (tau * cov) @ P[k]))
            for k, conf in enumerate(views_confidences)
        ]
        omega = np.diag(np.maximum(omega_diag, 1e-6))
    else:
        # Default He & Litterman specification: Omega = diag(P * (tau * Sigma) * P^T)
        omega = np.diag(np.diag(P @ (tau * cov) @ P.T))
        omega = np.maximum(omega, 1e-6 * np.eye(len(Q)))

    # Posterior expected returns:
    # E[R] = [ (tau*Sigma)^-1 + P^T * Omega^-1 * P ]^-1 * [ (tau*Sigma)^-1 * Pi + P^T * Omega^-1 * Q ]
    tau_cov = tau * cov
    inv_tau_cov = np.linalg.pinv(tau_cov)
    inv_omega = np.linalg.pinv(omega)

    m1 = inv_tau_cov + P.T @ inv_omega @ P
    inv_m1 = np.linalg.pinv(m1)
    m2 = inv_tau_cov @ pi + P.T @ inv_omega @ Q

    er_posterior = inv_m1 @ m2

    # Posterior covariance: Sigma_post = Sigma + [ (tau*Sigma)^-1 + P^T * Omega^-1 * P ]^-1
    cov_posterior = cov + inv_m1

    # Optimal unconstrained weights: w* = (lambda * cov_posterior)^-1 * er_posterior
    inv_cov_post = np.linalg.pinv(risk_aversion * cov_posterior)
    raw_weights = inv_cov_post @ er_posterior

    # Long-only projection (non-negative, sum to 1.0)
    clamped_weights = np.maximum(0.0, raw_weights)
    sum_w = np.sum(clamped_weights)
    optimal_weights = clamped_weights / sum_w if sum_w > 1e-12 else np.ones(n) / n

    return {
        "prior_returns": [round(float(x), 6) for x in pi],
        "posterior_expected_returns": [round(float(x), 6) for x in er_posterior],
        "optimal_weights": [round(float(x), 6) for x in optimal_weights],
        "posterior_cov_matrix": cov_posterior.tolist(),
    }


def get_black_litterman_fn():
    mod = importlib.import_module("app.portfolio_black_litterman")
    return getattr(mod, "optimize_black_litterman")


# =====================================================================
# Feature 7: Risk Budgeting & Concentration Oracle & Adapter
# =====================================================================

def oracle_compute_risk_budgeting(
    weights: np.ndarray,
    cov_matrix: np.ndarray,
    asset_names: List[str],
) -> Dict[str, Any]:
    """Calculates Marginal Risk Contribution (MRC), Component Risk Contribution (RC), HHI, ENC,
    and Attilio Meucci (2009) PCA factor ENCB."""
    w = np.asarray(weights, dtype=float)
    cov = np.asarray(cov_matrix, dtype=float)
    n = len(asset_names)

    sum_w = np.sum(w)
    w_norm = w / sum_w if abs(sum_w - 1.0) > 1e-6 and sum_w > 1e-12 else w

    port_var = float(w_norm @ cov @ w_norm)
    port_vol = math.sqrt(max(1e-12, port_var))

    # Marginal Risk Contribution: MRC = (cov @ w_norm) / port_vol
    mrc = (cov @ w_norm) / port_vol

    # Component Risk Contribution: RC_i = w_i * MRC_i
    rc = w_norm * mrc
    for i in range(n):
        if w_norm[i] <= 1e-12:
            rc[i] = 0.0

    # Percentage Risk Contribution (PRC): p_i = RC_i / port_vol (sums to 1.0)
    prc = rc / port_vol
    for i in range(n):
        if w_norm[i] <= 1e-12:
            prc[i] = 0.0

    # Herfindahl-Hirschman Index (HHI): sum(w_i^2)
    hhi = float(np.sum(w_norm**2))
    norm_hhi = float((hhi - 1.0 / n) / (1.0 - 1.0 / n)) if n > 1 else 1.0
    norm_hhi = max(0.0, min(1.0, norm_hhi))

    # Effective Number of Constituents (ENC): 1 / HHI
    enc = float(1.0 / hhi) if hhi > 1e-12 else 1.0

    # Effective Number of Correlated Bets (ENCB): Attilio Meucci (2009) PCA orthogonal factor decomposition
    active_indices = [i for i in range(n) if w_norm[i] > 1e-12]
    if len(active_indices) <= 1:
        encb = 1.0
        encb_inv_hhi = 1.0
    else:
        w_active = w_norm[active_indices]
        sum_w_act = float(np.sum(w_active))
        if sum_w_act > 1e-12:
            w_active = w_active / sum_w_act
        cov_active = cov[np.ix_(active_indices, active_indices)]

        eigvals, V = np.linalg.eigh(cov_active)
        idx = np.argsort(eigvals)[::-1]
        eigvals = np.maximum(0.0, eigvals[idx])
        V = V[:, idx]

        w_f = V.T @ w_active
        var_f = eigvals * (w_f ** 2)
        sum_var_f = float(np.sum(var_f))

        if sum_var_f > 1e-12:
            p_star = np.clip(var_f / sum_var_f, 1e-12, 1.0)
            p_star /= np.sum(p_star)
            entropy = -float(np.sum(p_star * np.log(p_star)))
            encb = float(math.exp(entropy))
            encb_inv_hhi = float(1.0 / np.sum(p_star ** 2))
            encb = min(float(len(active_indices)), max(1.0, encb))
            encb_inv_hhi = min(float(len(active_indices)), max(1.0, encb_inv_hhi))
        else:
            encb = 1.0
            encb_inv_hhi = 1.0

    return {
        "portfolio_volatility": round(port_vol, 6),
        "marginal_risk_contributions": {asset_names[i]: round(float(mrc[i]), 6) for i in range(n)},
        "risk_contributions": {asset_names[i]: round(float(rc[i]), 6) for i in range(n)},
        "percentage_risk_contributions": {asset_names[i]: round(float(prc[i] * 100.0), 4) for i in range(n)},
        "hhi": round(hhi, 6),
        "normalized_hhi": round(norm_hhi, 6),
        "effective_number_of_constituents": round(enc, 2),
        "effective_number_of_correlated_bets": round(encb, 2),
        "encb_inverse_hhi": round(encb_inv_hhi, 2),
    }


def get_risk_budgeting_fn():
    mod = importlib.import_module("app.risk_budgeting")
    return getattr(mod, "compute_risk_budgeting")


# =====================================================================
# Feature 8: Database Lateral Join Query Oracle
# =====================================================================

def oracle_query_benchmark_metrics() -> Dict[str, Any]:
    """Performance SLA contract verification: Lateral joins on 10Y rolling window must complete under 1,000ms."""
    return {
        "sla_threshold_ms": 1000,
        "covering_index_name": "idx_nav_history_cov",
        "index_definition": "ON nav_history(scheme_code, nav_date) INCLUDE (nav)",
        "summary_table_columns": ["return_1y", "return_3y", "return_5y", "return_10y"],
    }


# =====================================================================
# Feature 9: Dual-Era AMFI TER Synchronization Oracle
# =====================================================================

def oracle_reconcile_dual_era_ter(
    records: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Reconciles pre-2024 unstandardized AMFI formats and 2026 standardized schemas."""
    parsed = []
    for r in records:
        # Pre-2024 format: base_ter, additional_expenses, gst
        # Post-2026 format: official_ter, gross_ter, distribution_commission
        raw_ter = r.get("ter") or r.get("base_ter") or r.get("official_ter") or 0.0
        parsed.append({
            "scheme_code": int(r["scheme_code"]),
            "ter_date": str(r.get("ter_date") or r.get("date")),
            "official_ter": round(float(raw_ter), 4),
            "scheme_type": "Direct" if "direct" in str(r.get("scheme_name", "")).lower() else "Regular",
        })

    return {
        "processed_count": len(parsed),
        "checkpoint_status": "COMMITTED",
        "schemas_supported": ["pre_2024_reg52", "2026_standardized"],
        "sample": parsed[:3],
    }


# =====================================================================
# Feature 10: Direct vs Regular Plan Pairing Oracle & Adapter
# =====================================================================

def oracle_pair_direct_and_regular(
    schemes: List[Dict[str, Any]],
) -> Dict[int, int]:
    """Deterministic Direct vs Regular plan matcher by AMC, category, and option type."""
    # Group schemes by (fund_house, category, option_type)
    pairs = {}
    groups: Dict[Tuple[str, str, str], Dict[str, int]] = {}

    for s in schemes:
        code = int(s["scheme_code"])
        name = str(s.get("scheme_name", "")).lower()
        amc = str(s.get("fund_house", s.get("amc", ""))).strip().lower()
        cat = str(s.get("category", "")).strip().lower()

        # Option type: Growth vs IDCW/Dividend
        is_idcw = "idcw" in name or "dividend" in name
        opt = "idcw" if is_idcw else "growth"

        key = (amc, cat, opt)
        if key not in groups:
            groups[key] = {}

        if "direct" in name:
            groups[key]["direct"] = code
        else:
            groups[key]["regular"] = code

    for key, codes in groups.items():
        if "regular" in codes and "direct" in codes:
            pairs[codes["regular"]] = codes["direct"]

    return pairs


def get_plan_matcher_fn():
    mod = importlib.import_module("app.services.plan_matcher")
    return getattr(mod, "pair_direct_and_regular_schemes")


# =====================================================================
# Feature 11: Fee Drag Attribution Modeling Oracle & Adapter
# =====================================================================

def oracle_compute_fee_drag(
    direct_cagr: float,
    regular_cagr: float,
    ter_direct: float,
    ter_regular: float,
    direct_alpha: float = 0.0,
    regular_alpha: float = 0.0,
    horizons: List[str] = ["1Y", "3Y", "5Y", "10Y"],
    initial_capital: float = 100000.0,
) -> Dict[str, Any]:
    """Exact multi-horizon compounding fee drag and 3-tier gross vs net alpha isolation."""
    horizon_years = {"1Y": 1.0, "3Y": 3.0, "5Y": 5.0, "10Y": 10.0}

    horizon_results = {}
    for h in horizons:
        years = horizon_years.get(h, 1.0)
        # Final wealth compounding
        w_d = initial_capital * ((1.0 + direct_cagr) ** years)
        w_r = initial_capital * ((1.0 + regular_cagr) ** years)

        rupee_erosion = max(0.0, w_d - w_r)
        cum_drag_pct = ((w_d - w_r) / w_d) * 100.0 if w_d > 0 else 0.0

        horizon_results[h] = {
            "wealth_direct": round(w_d, 2),
            "wealth_regular": round(w_r, 2),
            "rupee_wealth_erosion": round(rupee_erosion, 2),
            "cumulative_drag_pct": round(cum_drag_pct, 2),
            "cagr_spread_pct": round((direct_cagr - regular_cagr) * 100.0, 4),
        }

    # Gross vs Net Alpha Isolation
    gross_alpha = direct_alpha + (ter_direct * 100.0)
    net_alpha_d = direct_alpha
    net_alpha_r = regular_alpha
    ter_differential = (ter_regular - ter_direct) * 100.0

    return {
        "horizons": horizon_results,
        "alpha_isolation": {
            "gross_alpha_pct": round(gross_alpha, 4),
            "net_alpha_direct_pct": round(net_alpha_d, 4),
            "net_alpha_regular_pct": round(net_alpha_r, 4),
            "ter_differential_pct": round(ter_differential, 4),
            "operating_drag_pct": round(ter_direct * 100.0, 4),
            "distribution_drag_pct": round(ter_differential, 4),
        },
    }


def get_fee_drag_fn():
    mod = importlib.import_module("app.services.fee_drag")
    return getattr(mod, "compute_fee_drag_attribution")


# =====================================================================
# Feature 12: Algorithmic Diagnostic Narratives Oracle
# =====================================================================

def oracle_generate_xai_narrative(
    scheme_name: str,
    factor_betas: Dict[str, float],
    alpha_ann_pct: float,
    sharpe: float,
    quartile: int = 1,
    is_regular: bool = False,
    ter_diff: float = 0.85,
) -> Dict[str, Any]:
    """Generates deterministic qualitative natural language diagnosis as structured JSON."""
    mkt_b = factor_betas.get("mkt_excess", factor_betas.get("market", 1.0))
    smb_b = factor_betas.get("smb", factor_betas.get("size", 0.0))
    hml_b = factor_betas.get("hml", factor_betas.get("value", 0.0))
    wml_b = factor_betas.get("wml", factor_betas.get("momentum", 0.0))

    # Style diagnosis
    size_style = "Mid/Small-Cap tilt" if smb_b > 0.15 else ("Large-Cap core" if smb_b < -0.15 else "Market-neutral size")
    value_style = "Deep Value orientation" if hml_b > 0.15 else ("Growth/Quality tilt" if hml_b < -0.15 else "Balanced valuation")
    mom_style = "Aggressive Momentum factor rider" if wml_b > 0.15 else "Contrarian / low momentum"

    alpha_diag = (
        f"Delivered institutional annualized manager alpha of {alpha_ann_pct:+.2f}%, demonstrating true security selection skill."
        if alpha_ann_pct > 1.0
        else f"Generated subdued annualized alpha of {alpha_ann_pct:+.2f}%, lagging benchmark after factor attribution."
    )

    factor_narrative = (
        f"{scheme_name} operates with a market beta of {mkt_b:.2f}. The fund displays a {size_style}, "
        f"a {value_style}, and acts as a {mom_style}. {alpha_diag}"
    )

    style_drift = {
        "factor_tilts": {"size": size_style, "value": value_style, "momentum": mom_style},
        "drift_detected": abs(smb_b) > 0.35 or abs(hml_b) > 0.35,
        "drift_severity": "Moderate" if abs(smb_b) > 0.35 else "Low",
    }

    migration = None
    if is_regular:
        migration = {
            "recommendation": "MIGRATE_TO_DIRECT",
            "actionable_insight": f"Switching to the Direct plan saves an estimated {ter_diff:.2f}% p.a. in distribution commission drag.",
        }

    return {
        "scheme_name": scheme_name,
        "headline_summary": f"Ranked Q{quartile} in peer category with Sharpe ratio of {sharpe:.2f}.",
        "factor_exposure_narrative": factor_narrative,
        "style_drift": style_drift,
        "peer_ranking": {
            "quartile": quartile,
            "badge": f"Q{quartile} (Top {(quartile)*25}%)",
            "sharpe": sharpe,
        },
        "migration_recommendation": migration,
    }


# =====================================================================
# Feature 13: Factor Exposure Waterfall Chart Oracle
# =====================================================================

def oracle_build_waterfall_chart_spec(
    alpha_ann_pct: float,
    factor_contributions: Dict[str, float],
    fee_drag_pct: float,
    net_return_pct: float,
) -> Dict[str, Any]:
    """Validates Plotly waterfall chart contract."""
    x = ["Manager Alpha"]
    y = [round(alpha_ann_pct, 4)]
    measures = ["relative"]

    for fac, contrib in factor_contributions.items():
        x.append(f"{fac.upper()} Return")
        y.append(round(contrib, 4))
        measures.append("relative")

    x.append("TER Fee Drag")
    y.append(round(-abs(fee_drag_pct), 4))
    measures.append("relative")

    x.append("Net Return")
    y.append(round(net_return_pct, 4))
    measures.append("total")

    trace = {
        "type": "waterfall",
        "orientation": "v",
        "measure": measures,
        "x": x,
        "y": y,
        "connector": {"line": {"color": "rgb(63, 63, 63)"}},
    }
    return {"data": [trace], "layout": {"title": "Institutional Factor Attribution Waterfall"}}


# =====================================================================
# Feature 14: Correlation Matrix Heatmap Oracle
# =====================================================================

def oracle_build_correlation_heatmap_spec(
    corr_matrix: np.ndarray,
    ordered_asset_names: List[str],
) -> Dict[str, Any]:
    """Validates Plotly heatmap trace ordered by HRP dendrogram cluster sequence."""
    z_values = np.round(corr_matrix, 4).tolist()
    trace = {
        "type": "heatmap",
        "z": z_values,
        "x": ordered_asset_names,
        "y": ordered_asset_names,
        "zmin": -1.0,
        "zmax": 1.0,
        "colorscale": "RdBu",
        "hoverongaps": False,
    }
    return {"data": [trace], "layout": {"title": "Quasi-Diagonalized Correlation Heatmap"}}


# =====================================================================
# Feature 15: Scenario Simulation Sliders Oracle
# =====================================================================

def oracle_factor_slider_perturbation(
    factor_betas: Dict[str, float],
    slider_shocks_pct: Dict[str, float],  # e.g. {"market": -15.0, "smb": -5.0}
) -> Dict[str, float]:
    """Real-time client-side factor perturbation calculation."""
    expected_drawdown_pct = 0.0
    for factor, shock_pct in slider_shocks_pct.items():
        beta = factor_betas.get(factor, 0.0)
        expected_drawdown_pct += beta * shock_pct

    return {
        "simulated_return_delta_pct": round(expected_drawdown_pct, 4),
        "stressed_portfolio_cagr_impact": round(expected_drawdown_pct, 4),
    }


# =====================================================================
# Feature 16: Complete State Synchronization Oracle
# =====================================================================

def oracle_serialize_url_params(state: Dict[str, Any]) -> str:
    """Serializes UI state into deep-linkable URL search parameters."""
    items = []
    for k in sorted(state.keys()):
        v = state[k]
        if v is not None:
            items.append(f"{k}={v}")
    return "&".join(items)


def oracle_deserialize_url_params(query_str: str) -> Dict[str, str]:
    """Deserializes URL search parameters back into state dict."""
    clean = query_str.lstrip("?")
    if not clean:
        return {}
    pairs = clean.split("&")
    res = {}
    for p in pairs:
        if "=" in p:
            k, v = p.split("=", 1)
            res[k] = v
    return res


# =====================================================================
# Feature 17: WCAG AA Contrast Compliance Oracle
# =====================================================================

def _srgb_channel_luminance(c_byte: int) -> float:
    c = c_byte / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def oracle_calculate_relative_luminance(rgb_hex: str) -> float:
    """Calculates relative luminance according to WCAG 2.1 specification."""
    hex_clean = rgb_hex.lstrip("#")
    r = int(hex_clean[0:2], 16)
    g = int(hex_clean[2:4], 16)
    b = int(hex_clean[4:6], 16)
    return 0.2126 * _srgb_channel_luminance(r) + 0.7152 * _srgb_channel_luminance(g) + 0.0722 * _srgb_channel_luminance(b)


def oracle_wcag_contrast_ratio(fg_hex: str, bg_hex: str) -> float:
    """Computes contrast ratio: (L1 + 0.05) / (L2 + 0.05)."""
    l1 = oracle_calculate_relative_luminance(fg_hex)
    l2 = oracle_calculate_relative_luminance(bg_hex)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def oracle_is_wcag_aa_compliant(fg_hex: str, bg_hex: str, is_large_text: bool = False) -> bool:
    """WCAG AA requires >= 4.5:1 for normal text and >= 3.0:1 for large text/graphical elements."""
    ratio = oracle_wcag_contrast_ratio(fg_hex, bg_hex)
    threshold = 3.0 if is_large_text else 4.5
    return ratio >= threshold


# =====================================================================
# Feature 18: Frontend Build & Lint Oracle
# =====================================================================

def oracle_frontend_build_verification() -> Dict[str, Any]:
    """Validates frontend build manifest expectations."""
    return {
        "required_routes": ["/quant", "/portfolio", "/screener", "/leaders", "/admin"],
        "min_nextjs_version": "14.2.0",
        "strict_typecheck": True,
        "eslint_enabled": True,
    }
