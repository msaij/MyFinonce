"""Black-Litterman Bayesian Portfolio Optimization.

Combines CAPM equilibrium market prior returns with investor tactical views,
producing a posterior return distribution and optimal long-only portfolio weights.
Reference: Fischer Black and Robert Litterman (1992),
"Global Portfolio Optimization", Financial Analysts Journal.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Union
import numpy as np


def optimize_black_litterman(
    cov_matrix: Union[np.ndarray, List[List[float]]],
    prior_weights: Union[np.ndarray, List[float]],
    views_matrix_P: Union[np.ndarray, List[List[float]]],
    views_returns_Q: Union[np.ndarray, List[float]],
    risk_aversion: float = 2.5,
    tau: float = 0.05,
    views_confidences: Optional[Union[np.ndarray, List[float]]] = None,
    asset_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Bayesian Black-Litterman optimization blending equilibrium prior returns with investor tactical views.
    
    Parameters:
      cov_matrix: (N x N) Asset return covariance matrix Sigma
      prior_weights: (N,) Benchmark / market equilibrium portfolio weights w_mkt
      views_matrix_P: (K x N) Pick matrix mapping views to assets
      views_returns_Q: (K,) Vector of expected returns for each view
      risk_aversion: Risk aversion parameter lambda (default: 2.5)
      tau: Weight on covariance of prior estimate (default: 0.05)
      views_confidences: Optional (K,) confidence level in [0, 1] per view (Idzorek method)
      asset_names: Optional (N,) list of asset names
      
    Returns:
      Dict with:
        - prior_returns: List[float] implied equilibrium returns Pi = lambda * Sigma * w_mkt
        - posterior_expected_returns: List[float] combined expected returns E(R)
        - optimal_weights: List[float] optimal weights summing to 1.0, w_i >= 0
        - posterior_cov_matrix: List[List[float]] posterior covariance matrix
    """
    cov = np.asarray(cov_matrix, dtype=float)
    w_mkt = np.asarray(prior_weights, dtype=float)
    P = np.asarray(views_matrix_P, dtype=float)
    Q = np.asarray(views_returns_Q, dtype=float)

    n = len(w_mkt)
    if P.size == 0 or Q.size == 0:
        k = 0
    else:
        k = len(Q)

    # Implied equilibrium market prior returns: Pi = lambda * Sigma * w_mkt
    pi = risk_aversion * (cov @ w_mkt)

    w_prior = np.maximum(0.0, w_mkt.astype(float))
    sum_prior = float(np.sum(w_prior))
    w_prior = w_prior / sum_prior if sum_prior > 1e-12 else np.ones(n) / n

    if k == 0:
        prior_list = [round(float(x), 6) for x in pi]
        w_list = [round(float(x), 6) for x in w_prior]
        diff = round(1.0 - sum(w_list), 6)
        if diff != 0.0 and abs(diff) < 1e-4:
            max_idx = int(np.argmax(w_list))
            w_list[max_idx] = round(w_list[max_idx] + diff, 6)
        result: Dict[str, Any] = {
            "prior_returns": prior_list,
            "posterior_expected_returns": list(prior_list),
            "optimal_weights": w_list,
            "posterior_cov_matrix": cov.tolist(),
        }
        if asset_names is not None and len(asset_names) == n:
            result["weights_by_asset"] = {asset_names[i]: w_list[i] for i in range(n)}
            result["posterior_returns_by_asset"] = {asset_names[i]: prior_list[i] for i in range(n)}
        return result

    # Views uncertainty matrix Omega
    tau_cov = tau * cov
    if views_confidences is not None:
        conf_arr = np.asarray(views_confidences, dtype=float)
        omega_diag = [
            float((1.0 - conf_arr[i]) / max(conf_arr[i], 1e-4) * (P[i] @ tau_cov @ P[i]))
            for i in range(k)
        ]
        omega = np.diag(np.maximum(omega_diag, 1e-6))
    else:
        # Default He & Litterman specification: Omega = diag(P * (tau * Sigma) * P^T)
        omega = np.diag(np.diag(P @ tau_cov @ P.T))
        omega = np.maximum(omega, 1e-6 * np.eye(k))

    # Posterior expected returns:
    # E[R] = [ (tau*Sigma)^-1 + P^T * Omega^-1 * P ]^-1 * [ (tau*Sigma)^-1 * Pi + P^T * Omega^-1 * Q ]
    inv_tau_cov = np.linalg.pinv(tau_cov)
    inv_omega = np.linalg.pinv(omega)

    m1 = inv_tau_cov + P.T @ inv_omega @ P
    inv_m1 = np.linalg.pinv(m1)
    m2 = inv_tau_cov @ pi + P.T @ inv_omega @ Q

    er_posterior = inv_m1 @ m2

    # Posterior covariance: Sigma_post = Sigma + [ (tau*Sigma)^-1 + P^T * Omega^-1 * P ]^-1
    cov_posterior = cov + inv_m1
    cov_posterior = 0.5 * (cov_posterior + cov_posterior.T)

    # Optimal unconstrained weights: w* = (lambda * cov_posterior)^-1 * er_posterior
    inv_cov_post = np.linalg.pinv(risk_aversion * cov_posterior)
    raw_weights = inv_cov_post @ er_posterior

    # Long-only projection (non-negative, sum to 1.0)
    clamped_weights = np.maximum(0.0, raw_weights)
    sum_w = np.sum(clamped_weights)
    optimal_weights = clamped_weights / sum_w if sum_w > 1e-12 else np.ones(n) / n

    # Round weights and adjust tiny rounding residue to guarantee sum == 1.000000
    optimal_weights_rounded = [round(float(x), 6) for x in optimal_weights]
    diff = round(1.0 - sum(optimal_weights_rounded), 6)
    if diff != 0.0 and abs(diff) < 1e-4:
        max_idx = int(np.argmax(optimal_weights_rounded))
        optimal_weights_rounded[max_idx] = round(optimal_weights_rounded[max_idx] + diff, 6)

    result: Dict[str, Any] = {
        "prior_returns": [round(float(x), 6) for x in pi],
        "posterior_expected_returns": [round(float(x), 6) for x in er_posterior],
        "optimal_weights": optimal_weights_rounded,
        "posterior_cov_matrix": cov_posterior.tolist(),
    }

    if asset_names is not None and len(asset_names) == n:
        result["weights_by_asset"] = {
            asset_names[i]: optimal_weights_rounded[i] for i in range(n)
        }
        result["posterior_returns_by_asset"] = {
            asset_names[i]: round(float(er_posterior[i]), 6) for i in range(n)
        }

    return result
