"""Hierarchical Risk Parity (HRP) portfolio optimization.

Based on Marcos Lopez de Prado's algorithm (2016):
"Building Diversified Portfolios that Outperform Out-of-Sample"
The Journal of Portfolio Management.

Three core stages:
1. Tree Clustering: Correlation-distance metric D_ij = sqrt(0.5 * (1 - rho_ij))
   and single-linkage hierarchical tree clustering via scipy.cluster.hierarchy.linkage.
2. Quasi-Diagonalization: Reordering asset covariance matrix using the dendrogram
   leaves so that highly correlated assets are adjacent.
3. Recursive Bisection: Top-down hierarchical allocation weighting subclusters
   inversely proportional to their cluster variance.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Union
import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import linkage


def _quasi_diag(linkage_matrix: np.ndarray) -> List[int]:
    """Quasi-diagonalization: reorders asset indices to place correlated assets adjacent.
    
    Walks the hierarchical clustering linkage matrix from top to bottom,
    expanding cluster indices until only original asset leaves remain.
    """
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
    """Calculates variance of an inverse-variance weighted sub-cluster.
    
    v_cluster = w^T * Cov * w, where w is proportional to 1 / diag(Cov).
    """
    sub_cov = cov[np.ix_(cluster_items, cluster_items)]
    diag = np.diag(sub_cov).copy()
    diag = np.where(diag < 1e-12, 1e-12, diag)
    inv_diag = 1.0 / diag
    inv_diag[np.isnan(inv_diag) | np.isinf(inv_diag)] = 0.0
    sum_inv = np.sum(inv_diag)
    w = (inv_diag / sum_inv) if sum_inv > 1e-12 else np.ones(len(cluster_items)) / len(cluster_items)
    return float(w @ sub_cov @ w)


def _recursive_bisection(cov: np.ndarray, ordered_items: List[int]) -> pd.Series:
    """Recursive bisection distributing risk inverse to cluster variance.
    
    Splits ordered items recursively, computing cluster variance of left and right,
    and scaling weights by alpha = 1 - (var_left / (var_left + var_right)).
    """
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
                weights[right] *= (1.0 - alpha)

                if len(left) > 1:
                    next_clusters.append(left)
                if len(right) > 1:
                    next_clusters.append(right)
        clusters = next_clusters

    return weights


def optimize_hrp(
    cov_matrix: Union[np.ndarray, pd.DataFrame, List[List[float]]],
    corr_matrix: Optional[Union[np.ndarray, pd.DataFrame, List[List[float]]]] = None,
    asset_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Hierarchical Risk Parity (HRP) tree clustering, quasi-diagonalization, and recursive bisection.
    
    Parameters:
      cov_matrix: (N x N) Asset return covariance matrix
      corr_matrix: (N x N) Asset return correlation matrix (optional, derived if None)
      asset_names: List of N asset identifiers / tickers
      
    Returns:
      Dict with:
        - weights: Dict[str, float] summing strictly to 1.0, non-negative
        - cluster_order: List[str] quasi-diagonalized leaf order
        - portfolio_vol_ann: float annualized portfolio volatility
    """
    if isinstance(cov_matrix, pd.DataFrame):
        if asset_names is None:
            asset_names = list(cov_matrix.columns)
        cov_arr = cov_matrix.values.astype(float)
    else:
        cov_arr = np.asarray(cov_matrix, dtype=float)

    n = cov_arr.shape[0]
    if asset_names is None:
        asset_names = [f"Asset_{i}" for i in range(n)]

    if n == 1:
        port_vol_ann = math.sqrt(max(0.0, float(cov_arr[0, 0]) * 252.0))
        return {
            "weights": {asset_names[0]: 1.0},
            "cluster_order": asset_names,
            "portfolio_vol_ann": round(port_vol_ann, 4),
        }

    if corr_matrix is None:
        d = np.sqrt(np.maximum(1e-12, np.diag(cov_arr)))
        corr_arr = cov_arr / np.outer(d, d)
        np.fill_diagonal(corr_arr, 1.0)
    elif isinstance(corr_matrix, pd.DataFrame):
        corr_arr = corr_matrix.values.astype(float)
    else:
        corr_arr = np.asarray(corr_matrix, dtype=float)

    # 1. Distance matrix: D_ij = sqrt(0.5 * (1 - rho_ij))
    dist = np.sqrt(np.clip(0.5 * (1.0 - corr_arr), 0.0, 1.0))
    np.fill_diagonal(dist, 0.0)

    # Condensed distance for scipy linkage
    condensed = dist[np.triu_indices(n, k=1)]

    # 2. Single-linkage clustering
    link = linkage(condensed, method="single")

    # 3. Quasi-diagonalization
    order_indices = _quasi_diag(link)
    cluster_order = [asset_names[i] for i in order_indices]

    # 4. Recursive bisection
    w_series = _recursive_bisection(cov_arr, order_indices)
    weights = {asset_names[i]: float(w_series[i]) for i in range(n)}

    # Ensure non-negative and exact sum to 1.0
    for k in weights:
        if math.isnan(weights[k]) or weights[k] < 0.0:
            weights[k] = 0.0
    total_w = sum(weights.values())
    if total_w > 1e-12:
        weights = {k: float(v / total_w) for k, v in weights.items()}
    else:
        weights = {k: 1.0 / n for k in weights}

    # Compute portfolio volatility before rounding weights
    w_arr = np.array([weights[a] for a in asset_names])
    port_var = float(w_arr @ cov_arr @ w_arr)
    port_vol_ann = math.sqrt(max(0.0, port_var * 252.0))

    # Round weights and adjust tiny rounding residue to guarantee sum == 1.000000
    weights_rounded = {k: round(v, 6) for k, v in weights.items()}
    residue = round(1.0 - sum(weights_rounded.values()), 6)
    if residue != 0.0 and abs(residue) < 1e-4:
        max_k = max(weights_rounded, key=weights_rounded.get)
        weights_rounded[max_k] = round(weights_rounded[max_k] + residue, 6)

    return {
        "weights": weights_rounded,
        "cluster_order": cluster_order,
        "portfolio_vol_ann": round(port_vol_ann, 4),
    }
