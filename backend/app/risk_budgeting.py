"""Risk Budgeting, Concentration Metrics, and Efficient Frontier Overlays.

Implements:
1. Herfindahl-Hirschman Index (HHI) and Normalized HHI
2. Effective Number of Constituents (ENC = 1 / sum(w_i^2))
3. Marginal Risk Contribution (MRC_i = (Sigma * w)_i / sigma_p)
4. Component Risk Contribution (RC_i = w_i * MRC_i)
5. Percentage Risk Contribution (p_i = RC_i / sigma_p, verifying Euler sum = 100%)
6. Effective Number of Correlated Bets (ENCB via Attilio Meucci 2009 PCA factor decomposition)
7. Markowitz Efficient Frontier curve with Iso-Sharpe and Iso-Sortino overlays.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Union
import numpy as np
import scipy.optimize as sco


def compute_risk_budgeting(
    weights: Union[np.ndarray, List[float], Dict[str, float]],
    cov_matrix: Union[np.ndarray, List[List[float]]],
    asset_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Calculates Marginal Risk Contribution (MRC), Component Risk Contribution (RC),
    Percentage Risk Contribution (PRC), HHI, ENC, and ENCB.
    
    Verifies Euler decomposition: sum(RC_i) = sigma_p and sum(PRC_i) = 100%.
    """
    if isinstance(weights, dict):
        if asset_names is None:
            asset_names = list(weights.keys())
        w_arr = np.array([float(weights[k]) for k in asset_names], dtype=float)
    else:
        w_arr = np.asarray(weights, dtype=float)

    cov = np.asarray(cov_matrix, dtype=float)
    n = len(w_arr)
    if asset_names is None:
        asset_names = [f"Asset_{i}" for i in range(n)]

    if n == 1:
        port_var = float(cov[0, 0])
        port_vol = math.sqrt(max(1e-12, port_var))
        return {
            "portfolio_volatility": round(port_vol, 6),
            "marginal_risk_contributions": {asset_names[0]: round(port_vol, 6)},
            "risk_contributions": {asset_names[0]: round(port_vol, 6)},
            "percentage_risk_contributions": {asset_names[0]: 100.0},
            "hhi": 1.0,
            "normalized_hhi": 1.0,
            "effective_number_of_constituents": 1.0,
            "effective_number_of_correlated_bets": 1.0,
            "encb_inverse_hhi": 1.0,
        }

    sum_w = np.sum(w_arr)
    w_norm = w_arr / sum_w if abs(sum_w - 1.0) > 1e-6 and sum_w > 1e-12 else w_arr

    port_var = float(w_norm @ cov @ w_norm)
    port_vol = math.sqrt(max(1e-12, port_var))

    # Marginal Risk Contribution: MRC = (cov @ w) / port_vol
    cov_w = cov @ w_norm
    mrc = cov_w / port_vol

    # Component Risk Contribution: RC_i = w_i * MRC_i
    rc = w_norm * mrc
    # Zero out for zero weight assets to prevent numerical noise
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

    # Effective Number of Correlated Bets (ENCB): Attilio Meucci (2009, "Managing Diversification")
    # Orthogonal factor decomposition via eigendecomposition of covariance matrix
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


def compute_efficient_frontier(
    expected_returns: Union[np.ndarray, List[float]],
    cov_matrix: Union[np.ndarray, List[List[float]]],
    asset_names: List[str],
    rf: float = 0.065,
    n_points: int = 50,
    downside_dev_vec: Optional[Union[np.ndarray, List[float]]] = None,
) -> Dict[str, Any]:
    """Generates the Markowitz Efficient Frontier curve with Iso-Sharpe and Iso-Sortino overlays.
    
    Solves min w^T Sigma w subject to w^T R = r_target, sum(w) = 1, w_i >= 0.
    """
    er = np.asarray(expected_returns, dtype=float)
    cov = np.asarray(cov_matrix, dtype=float)
    n = len(er)

    # 1. Minimum Variance Portfolio
    bounds = tuple((0.0, 1.0) for _ in range(n))
    eq_sum = {"type": "eq", "fun": lambda w: np.sum(w) - 1.0}
    x0 = np.ones(n) / n

    min_vol_res = sco.minimize(
        lambda w: float(w @ cov @ w),
        x0,
        method="SLSQP",
        bounds=bounds,
        constraints=[eq_sum],
    )
    w_min_vol = min_vol_res.x if min_vol_res.success else x0
    r_min_vol = float(w_min_vol @ er)
    v_min_vol = float(math.sqrt(max(1e-12, w_min_vol @ cov @ w_min_vol)))

    # 2. Max return asset
    r_max = float(np.max(er))
    max_idx = int(np.argmax(er))
    v_max = float(math.sqrt(max(1e-12, cov[max_idx, max_idx])))

    # 3. Frontier points
    if r_max <= r_min_vol:
        target_returns = [r_min_vol]
    else:
        target_returns = np.linspace(r_min_vol, r_max, n_points).tolist()

    frontier_points: List[Dict[str, Any]] = []
    for r_t in target_returns:
        eq_ret = {"type": "eq", "fun": lambda w, rt=r_t: float(w @ er - rt)}
        res = sco.minimize(
            lambda w: float(w @ cov @ w),
            x0,
            method="SLSQP",
            bounds=bounds,
            constraints=[eq_sum, eq_ret],
            options={"maxiter": 300},
        )
        if res.success:
            w_pt = res.x
            vol_pt = float(math.sqrt(max(1e-12, w_pt @ cov @ w_pt)))
            sharpe_pt = (r_t - rf) / vol_pt if vol_pt > 1e-8 else 0.0
            sortino_pt = sharpe_pt
            if downside_dev_vec is not None:
                d_vec = np.asarray(downside_dev_vec, dtype=float)
                d_vol = float(np.dot(w_pt, d_vec))
                if d_vol > 1e-8:
                    sortino_pt = (r_t - rf) / d_vol

            frontier_points.append({
                "volatility": round(vol_pt, 6),
                "expected_return": round(float(r_t), 6),
                "sharpe_ratio": round(float(sharpe_pt), 4),
                "sortino_ratio": round(float(sortino_pt), 4),
                "weights": {asset_names[i]: round(float(w_pt[i]), 4) for i in range(n)},
            })

    # Sort frontier by volatility
    frontier_points.sort(key=lambda p: p["volatility"])

    # Best Sharpe point
    max_sharpe_pt = max(frontier_points, key=lambda p: p["sharpe_ratio"]) if frontier_points else {
        "volatility": round(v_min_vol, 6),
        "expected_return": round(r_min_vol, 6),
        "sharpe_ratio": round((r_min_vol - rf) / v_min_vol, 4) if v_min_vol > 0 else 0.0,
        "weights": {asset_names[i]: round(float(w_min_vol[i]), 4) for i in range(n)},
    }

    # Minimum Vol point
    min_vol_pt = min(frontier_points, key=lambda p: p["volatility"]) if frontier_points else max_sharpe_pt

    # Overlays: Iso-Sharpe rays
    max_vol_frontier = max([p["volatility"] for p in frontier_points]) if frontier_points else 0.30
    vol_axis = [0.0, max_vol_frontier * 1.2]
    iso_sharpe_rays = []
    for s in [0.5, 1.0, 1.5, 2.0]:
        iso_sharpe_rays.append({
            "sharpe": s,
            "points": [
                {"volatility": vol_axis[0], "return": round(rf + s * vol_axis[0], 6)},
                {"volatility": vol_axis[1], "return": round(rf + s * vol_axis[1], 6)},
            ],
        })

    # Overlays: Iso-Sortino rays
    iso_sortino_rays = []
    for sort_val in [0.5, 1.0, 1.5, 2.0]:
        iso_sortino_rays.append({
            "sortino": sort_val,
            "points": [
                {"downside_volatility": vol_axis[0], "return": round(rf + sort_val * vol_axis[0], 6)},
                {"downside_volatility": vol_axis[1], "return": round(rf + sort_val * vol_axis[1], 6)},
            ],
        })

    return {
        "frontier_points": frontier_points,
        "min_vol_portfolio": min_vol_pt,
        "max_sharpe_portfolio": max_sharpe_pt,
        "risk_free_rate": rf,
        "iso_sharpe_rays": iso_sharpe_rays,
        "iso_sortino_rays": iso_sortino_rays,
    }
