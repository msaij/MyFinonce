"""
Fama-French-Carhart 4-Factor Risk Decomposition Engine calibrated for Indian Mutual Funds.
Decomposes fund excess returns into:
  - Market Factor (Nifty 50 Excess Return)
  - Size Factor (SMB: Mid/Small Cap spread over Large Cap)
  - Value Factor (HML: Value/Contra spread over Large Cap)
  - Momentum Factor (WML: Momentum Index or cross-sectional momentum spread)

Computes multivariate OLS regression, factor betas, t-stats, p-values, R-squared,
adjusted R-squared, residual volatility, systematic vs. idiosyncratic risk decomposition,
and Plotly-compatible waterfall attribution data.
"""

import datetime
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import scipy.stats as stats
import plotly.graph_objects as go

from app.core.cache import cached
from app.db.connection import get_connection, fetchdf

_attr_cache: Dict[Tuple, Dict[str, Any]] = {}
_attr_lock = threading.Lock()


def compute_multivariate_factor_attribution(
    fund_returns: pd.Series,
    factor_returns: pd.DataFrame,
    rf_daily: float = 0.00025,
) -> Dict[str, Any]:
    """
    Performs institutional multivariate OLS regression:
      R_{i,t} - R_{f,t} = alpha + beta_mkt * MKT_t + beta_smb * SMB_t + beta_hml * HML_t + beta_wml * WML_t + eps_t

    Parameters:
      fund_returns: pd.Series of daily fund returns indexed by date
      factor_returns: pd.DataFrame with columns ['mkt_excess', 'smb', 'hml', 'wml'] indexed by date
      rf_daily: daily risk-free rate (default 0.00025, corresponding to ~6.5% ann)

    Returns:
      Dict conforming to the institutional FactorAttributionResult contract:
        - alpha_annualized_pct: float
        - alpha_daily: float
        - alpha_t_stat: float
        - alpha_p_value: float
        - r_squared: float
        - adj_r_squared: float
        - residual_vol_ann_pct: float
        - factor_betas: dict[str, float]
        - t_stats: dict[str, float]
        - p_values: dict[str, float]
        - factors: dict[str, dict]
        - variance_decomposition: dict[str, float]
        - systematic_risk_pct: float
        - idiosyncratic_risk_pct: float
        - waterfall_data: dict[str, list]
        - n_observations: int
    """
    try:
        cache_key = (
            len(fund_returns),
            str(fund_returns.index[0]) if len(fund_returns) > 0 else "",
            str(fund_returns.index[-1]) if len(fund_returns) > 0 else "",
            round(float(fund_returns.iloc[0]), 6) if len(fund_returns) > 0 else 0.0,
            round(float(fund_returns.iloc[-1]), 6) if len(fund_returns) > 0 else 0.0,
            len(factor_returns),
            str(factor_returns.index[0]) if len(factor_returns) > 0 else "",
            str(factor_returns.index[-1]) if len(factor_returns) > 0 else "",
            round(rf_daily, 8),
        )
    except Exception:
        cache_key = None

    if cache_key is not None:
        with _attr_lock:
            if cache_key in _attr_cache:
                return _attr_cache[cache_key].copy()

    core_cols = ["mkt_excess", "smb", "hml"]
    for col in core_cols:
        if col not in factor_returns.columns:
            raise ValueError(f"factor_returns missing required column: {col}")
    required_cols = list(core_cols)
    has_wml = "wml" in factor_returns.columns and factor_returns["wml"].notna().any()
    if has_wml:
        required_cols.append("wml")

    # Ensure clean indices
    s_fund = fund_returns.dropna().copy()
    df_factors = factor_returns[required_cols].dropna().copy()

    # Align on common dates
    s_fund.index = pd.to_datetime(s_fund.index)
    df_factors.index = pd.to_datetime(df_factors.index)

    merged = pd.concat([s_fund.rename("fund"), df_factors], axis=1, join="inner").dropna()
    n_obs = len(merged)
    if n_obs < 60:
        raise ValueError(f"Insufficient overlapping observations for factor regression: {n_obs} (minimum 60 required)")

    # Dependent variable: Fund excess return
    y = (merged["fund"].values.astype(float) - rf_daily)
    x_factors = merged[required_cols].values.astype(float)
    x = np.column_stack([np.ones(n_obs, dtype=float), x_factors])

    k_params = 1 + len(required_cols)
    df_resid = n_obs - k_params

    # OLS estimation: beta = (X^T X)^{-1} X^T y
    xtx = x.T @ x
    xty = x.T @ y

    # Numerical condition number detection
    try:
        cond = np.linalg.cond(xtx)
        is_ill_conditioned = (cond > 1e10) or np.isnan(cond) or np.isinf(cond)
    except Exception:
        is_ill_conditioned = True

    if is_ill_conditioned:
        beta = np.linalg.pinv(x, rcond=1e-8) @ y
        xtx_inv = np.linalg.pinv(xtx, rcond=1e-8)
    else:
        try:
            beta = np.linalg.solve(xtx, xty)
            xtx_inv = np.linalg.inv(xtx)
        except np.linalg.LinAlgError:
            # Fallback to pseudo-inverse if singular
            beta = np.linalg.pinv(x, rcond=1e-8) @ y
            xtx_inv = np.linalg.pinv(xtx, rcond=1e-8)

    # Predictions & Residuals
    y_hat = x @ beta
    residuals = y - y_hat
    sse = float(np.sum(residuals ** 2))
    s2 = sse / df_resid if df_resid > 0 else 0.0

    # Covariance matrix of parameter estimates
    var_beta = s2 * xtx_inv
    se_beta = np.sqrt(np.maximum(np.diag(var_beta), 1e-14))

    # t-statistics and two-sided p-values
    t_stats = beta / se_beta
    p_values = 2.0 * stats.t.sf(np.abs(t_stats), df=max(1, df_resid))

    # Variance and R-squared
    y_mean = float(np.mean(y))
    sst = float(np.sum((y - y_mean) ** 2))
    r_squared = float(1.0 - sse / sst) if sst > 1e-12 else 0.0
    r_squared = max(0.0, min(1.0, r_squared))
    adj_r_squared = float(1.0 - (1.0 - r_squared) * (n_obs - 1) / df_resid) if df_resid > 0 else r_squared
    adj_r_squared = max(0.0, min(1.0, adj_r_squared))

    # Annualization (252 trading days)
    alpha_daily = float(beta[0])
    alpha_ann_pct = float(alpha_daily * 252.0 * 100.0)
    residual_vol_ann_pct = float(np.sqrt(max(0.0, s2)) * np.sqrt(252.0) * 100.0)

    # Factor Betas mapping
    col_to_key = {"mkt_excess": "market", "smb": "size", "hml": "value", "wml": "momentum"}
    factor_keys = [col_to_key[c] for c in required_cols]
    betas_dict = {key: float(beta[i + 1]) for i, key in enumerate(factor_keys)}
    t_stats_dict = {"alpha": float(t_stats[0])}
    t_stats_dict.update({key: float(t_stats[i + 1]) for i, key in enumerate(factor_keys)})
    p_values_dict = {"alpha": float(p_values[0])}
    p_values_dict.update({key: float(p_values[i + 1]) for i, key in enumerate(factor_keys)})

    # Variance Decomposition (Euler exact decomposition of systematic variance)
    # Var(Y_hat) = sum_{j=1}^4 beta_j * Cov(X_j, Y_hat)
    y_hat_var = float(np.var(y_hat, ddof=1)) if n_obs > 1 else 0.0
    var_decomp: Dict[str, float] = {}

    if y_hat_var > 1e-12:
        y_hat_centered = y_hat - np.mean(y_hat)
        raw_contrs = []
        for i, key in enumerate(factor_keys):
            xj_centered = x_factors[:, i] - np.mean(x_factors[:, i])
            cov_j = float(np.sum(xj_centered * y_hat_centered) / (n_obs - 1))
            contr = float(beta[i + 1] * cov_j)
            raw_contrs.append(contr)

        sum_raw = sum(raw_contrs)
        max_contr = max(abs(c) for c in raw_contrs) if raw_contrs else 0.0
        has_extreme = any(abs(c) > 1e6 for c in raw_contrs) or any(np.isnan(raw_contrs))
        if abs(sum_raw) > 1e-10 and (max_contr / abs(sum_raw) < 1e8) and not has_extreme:
            for i, key in enumerate(factor_keys):
                var_decomp[key] = float(round((raw_contrs[i] / sum_raw) * 100.0, 4))
        else:
            equal = round(100.0 / max(len(factor_keys), 1), 4)
            for key in factor_keys:
                var_decomp[key] = equal
    else:
        equal = round(100.0 / max(len(factor_keys), 1), 4)
        for key in factor_keys:
            var_decomp[key] = equal

    # Ensure sum of variance decomposition strictly equals 100.0%
    decomp_sum = sum(var_decomp.values())
    if abs(decomp_sum - 100.0) > 1.0:
        equal = round(100.0 / max(len(factor_keys), 1), 4)
        for key in factor_keys:
            var_decomp[key] = equal
        decomp_sum = 100.0

    diff = round(100.0 - decomp_sum, 4)
    if abs(diff) > 1e-6:
        # Absorb rounding residual into factor with largest absolute weight
        largest_key = max(factor_keys, key=lambda k: abs(var_decomp[k]))
        var_decomp[largest_key] = round(var_decomp[largest_key] + diff, 4)

    systematic_risk_pct = float(round(r_squared * 100.0, 4))
    idiosyncratic_risk_pct = float(round(max(0.0, (1.0 - r_squared) * 100.0), 4))

    # Comprehensive factor structure
    factors_summary = {}
    for i, key in enumerate(factor_keys):
        factors_summary[key] = {
            "beta": round(betas_dict[key], 4),
            "t_stat": round(t_stats_dict[key], 4),
            "p_value": round(p_values_dict[key], 4),
            "variance_contribution_pct": var_decomp[key],
        }

    # Waterfall return decomposition (annualized percentage attribution)
    factor_means_ann = [float(np.mean(x_factors[:, i]) * 252.0 * 100.0) for i in range(len(factor_keys))]
    label_by_key = {
        "market": "Market (Nifty 50)",
        "size": "Size (SMB)",
        "value": "Value (HML)",
        "momentum": "Momentum (WML)",
    }
    waterfall_labels = ["Alpha (Manager Skill)"] + [label_by_key[k] for k in factor_keys] + ["Net Excess Return"]
    contribs = [float(betas_dict[k] * factor_means_ann[i]) for i, k in enumerate(factor_keys)]
    net_excess_ann = float(alpha_ann_pct + sum(contribs))

    waterfall_values = [round(alpha_ann_pct, 4)] + [round(c, 4) for c in contribs] + [round(net_excess_ann, 4)]
    waterfall_measures = ["relative"] * (1 + len(factor_keys)) + ["total"]
    waterfall_text = [f"{v:+.2f}%" if m == "relative" else f"{v:.2f}%" for v, m in zip(waterfall_values, waterfall_measures)]

    waterfall_data = {
        "labels": waterfall_labels,
        "values": waterfall_values,
        "measures": waterfall_measures,
        "text": waterfall_text,
    }

    res = {
        "alpha_annualized_pct": round(alpha_ann_pct, 4),
        "alpha_daily": round(alpha_daily, 6),
        "alpha_t_stat": round(float(t_stats[0]), 4),
        "alpha_p_value": round(float(p_values[0]), 4),
        "r_squared": round(r_squared, 4),
        "adj_r_squared": round(adj_r_squared, 4),
        "residual_vol_ann_pct": round(residual_vol_ann_pct, 4),
        "factor_betas": {k: round(v, 4) for k, v in betas_dict.items()},
        "t_stats": {k: round(v, 4) for k, v in t_stats_dict.items()},
        "p_values": {k: round(v, 4) for k, v in p_values_dict.items()},
        "factors": factors_summary,
        "variance_decomposition": var_decomp,
        "systematic_risk_pct": systematic_risk_pct,
        "idiosyncratic_risk_pct": idiosyncratic_risk_pct,
        "waterfall_data": waterfall_data,
        "n_observations": n_obs,
    }

    if cache_key is not None:
        with _attr_lock:
            if len(_attr_cache) > 200:
                _attr_cache.clear()
            _attr_cache[cache_key] = res

    return res


_cached_base_factor_df: Optional[pd.DataFrame] = None
_factor_cache_time: float = 0.0
_factor_lock = threading.Lock()
_cached_factor_source: Optional[Dict[str, Any]] = None

DEFAULT_FACTOR_PROXIES = {
    "factor_proxy_market": 118482,
    "factor_proxy_market_fallback": 100822,
    "factor_proxy_market_fallback_2": 118581,
    "factor_proxy_momentum": 150452,
}


def get_factor_proxies() -> Dict[str, int]:
    """Read configurable proxy scheme codes from sync_meta, falling back to defaults."""
    proxies = dict(DEFAULT_FACTOR_PROXIES)
    try:
        from app.db import queries as db_queries
        stored = db_queries.get_sync_meta_values(list(DEFAULT_FACTOR_PROXIES.keys()))
        for key, raw in stored.items():
            try:
                proxies[key] = int(raw)
            except (TypeError, ValueError):
                pass
    except Exception:
        pass
    return proxies


def describe_factor_source(
    proxies: Dict[str, int],
    market_scheme_code: Optional[int],
    momentum_scheme_code: Optional[int],
    wml_omitted: bool,
    n_obs: int,
    window: Tuple[Optional[datetime.date], Optional[datetime.date]],
    is_partial: bool,
) -> Dict[str, Any]:
    return {
        "market_scheme_code": market_scheme_code,
        "momentum_scheme_code": None if wml_omitted else momentum_scheme_code,
        "smb_method": "category_average_mid_small_minus_large",
        "hml_method": "category_average_value_contra_minus_large",
        "wml_method": "omitted" if wml_omitted else "momentum_index_minus_market",
        "includes_analysed_scheme_in_smb_hml": True,
        "n_obs": n_obs,
        "window": [str(window[0]) if window[0] else None, str(window[1]) if window[1] else None],
        "is_partial": is_partial,
        "proxy_scheme_codes": proxies,
        "disclosure": "SMB/HML are category-average Direct-Growth returns including this scheme.",
    }


def _scheme_return_sql(scheme_code: int, alias: str) -> str:
    return f"""
        SELECT nav_date,
               (nav - LAG(nav) OVER (ORDER BY nav_date)) /
               NULLIF(LAG(nav) OVER (ORDER BY nav_date), 0) as {alias}
        FROM nav_history
        WHERE scheme_code = {int(scheme_code)}
        ORDER BY nav_date ASC
    """


def _compute_base_factor_returns(rf_daily: float = 0.00025) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    proxies = get_factor_proxies()
    market_code = proxies["factor_proxy_market"]
    fallback_codes = [
        proxies.get("factor_proxy_market_fallback", 100822),
        proxies.get("factor_proxy_market_fallback_2", 118581),
    ]
    mom_code = proxies["factor_proxy_momentum"]
    used_market: Optional[int] = None
    used_momentum: Optional[int] = None

    con = get_connection()
    try:
        df_mkt = fetchdf(con.execute(_scheme_return_sql(market_code, "mkt_ret")))
        if not df_mkt.empty and len(df_mkt.dropna()) >= 10:
            used_market = market_code
        else:
            for alt in fallback_codes:
                df_mkt = fetchdf(con.execute(_scheme_return_sql(alt, "mkt_ret")))
                if not df_mkt.empty and len(df_mkt.dropna()) >= 10:
                    used_market = alt
                    break

        # 2. Category daily averages for Size and Value factors
        sql_cats = """
            WITH cat_funds AS (
                SELECT s.scheme_code, s.category
                FROM schemes s
                WHERE s.category IN (
                    'Equity Scheme - Large Cap Fund',
                    'Equity Scheme - Mid Cap Fund',
                    'Equity Scheme - Small Cap Fund',
                    'Equity Scheme - Value Fund',
                    'Equity Scheme - Contra Fund'
                )
                AND s.plan_type = 'Direct' AND s.option_type = 'Growth'
            ),
            diffs AS (
                SELECT cf.category, n.nav_date,
                       (n.nav - LAG(n.nav) OVER (PARTITION BY n.scheme_code ORDER BY n.nav_date)) /
                       NULLIF(LAG(n.nav) OVER (PARTITION BY n.scheme_code ORDER BY n.nav_date), 0) as ret
                FROM nav_history n
                JOIN cat_funds cf ON n.scheme_code = cf.scheme_code
            )
            SELECT category, nav_date, AVG(ret) as cat_ret
            FROM diffs
            WHERE ret IS NOT NULL
            GROUP BY category, nav_date
            ORDER BY nav_date ASC;
        """
        df_cats = fetchdf(con.execute(sql_cats))

        df_mom = fetchdf(con.execute(_scheme_return_sql(mom_code, "mom_ret")))
        if not df_mom.empty and len(df_mom.dropna()) >= 10:
            used_momentum = mom_code

    except Exception:
        df_mkt = pd.DataFrame()
        df_cats = pd.DataFrame()
        df_mom = pd.DataFrame()
    finally:
        con.close()

    empty_source = describe_factor_source(
        proxies, used_market, used_momentum, True, 0, (None, None), True
    )
    if df_mkt.empty and df_cats.empty:
        return pd.DataFrame(columns=["mkt", "mkt_excess", "rf"]), empty_source

    # Pivot category returns
    piv = pd.DataFrame()
    if not df_cats.empty:
        df_cats["nav_date"] = pd.to_datetime(df_cats["nav_date"])
        piv = df_cats.pivot(index="nav_date", columns="category", values="cat_ret")

    # Merge Market
    res = pd.DataFrame(index=piv.index if not piv.empty else pd.to_datetime(df_mkt["nav_date"]))
    if not df_mkt.empty:
        df_mkt["nav_date"] = pd.to_datetime(df_mkt["nav_date"])
        df_mkt_indexed = df_mkt.dropna().set_index("nav_date")
        res["mkt"] = df_mkt_indexed["mkt_ret"]

    # If Market not directly found, use Large Cap Category return
    if "mkt" not in res.columns or res["mkt"].dropna().empty:
        if not piv.empty and "Equity Scheme - Large Cap Fund" in piv.columns:
            res["mkt"] = piv["Equity Scheme - Large Cap Fund"]
        else:
            return pd.DataFrame(columns=["mkt", "mkt_excess", "rf"]), empty_source

    # Fill MKT excess
    res["mkt_excess"] = res["mkt"] - rf_daily

    # Size / value: omit rather than fill a constant 0.0 when the category panel is missing.
    if not piv.empty and "Equity Scheme - Large Cap Fund" in piv.columns:
        large = piv["Equity Scheme - Large Cap Fund"]
        size_legs = [piv[c] for c in ("Equity Scheme - Mid Cap Fund", "Equity Scheme - Small Cap Fund") if c in piv.columns]
        if size_legs:
            res["smb"] = sum(size_legs) / len(size_legs) - large
        value_legs = [piv[c] for c in ("Equity Scheme - Value Fund", "Equity Scheme - Contra Fund") if c in piv.columns]
        if value_legs:
            res["hml"] = sum(value_legs) / len(value_legs) - large

    # Momentum Factor (WML): omit rather than synthesize from SMB
    wml_omitted = True
    if not df_mom.empty:
        df_mom["nav_date"] = pd.to_datetime(df_mom["nav_date"])
        mom_indexed = df_mom.dropna().set_index("nav_date")
        res["wml_raw"] = mom_indexed["mom_ret"]
        res["wml"] = res["wml_raw"] - res["mkt"]
        res.drop(columns=["wml_raw"], inplace=True)
        wml_omitted = bool(res["wml"].dropna().empty)
        if wml_omitted:
            res.drop(columns=["wml"], inplace=True)

    res["rf"] = rf_daily
    res = res.dropna(subset=["mkt_excess"]).copy()
    idx = res.index
    window = (idx.min().date() if len(idx) else None, idx.max().date() if len(idx) else None)
    source = describe_factor_source(
        proxies, used_market, used_momentum, wml_omitted, len(res), window,
        wml_omitted or "smb" not in res.columns or "hml" not in res.columns,
    )
    return res, source


def _load_base_factor_panel(rf_daily: float = 0.00025) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    global _cached_base_factor_df, _factor_cache_time, _cached_factor_source
    now = time.time()
    if _cached_base_factor_df is None or (now - _factor_cache_time >= 3600):
        with _factor_lock:
            now = time.time()
            if _cached_base_factor_df is None or (now - _factor_cache_time >= 3600):
                _cached_base_factor_df, _cached_factor_source = _compute_base_factor_returns(rf_daily=rf_daily)
                _factor_cache_time = now
    return _cached_base_factor_df.copy(), dict(_cached_factor_source or {})


def get_indian_factor_returns(
    start_date: Optional[datetime.date] = None,
    end_date: Optional[datetime.date] = None,
    rf_daily: float = 0.00025,
) -> pd.DataFrame:
    """
    Daily Indian equity risk factors from nav_history (one global panel, cached 1h):
      1. Market Factor: configurable Nifty 50 proxy (default 118482, fallbacks 100822 / 118581)
      2. Size Factor (SMB): (Mid Cap Avg Ret + Small Cap Avg Ret) / 2 - Large Cap Avg Ret
      3. Value Factor (HML): (Value Fund Avg Ret + Contra Fund Avg Ret) / 2 - Large Cap Avg Ret
      4. Momentum Factor (WML): Momentum index (default 150452) minus market; omitted when missing

    SMB/HML include the analysed scheme in the category average (not leave-one-out).
    Returns DataFrame with columns ['mkt_excess', 'smb', 'hml', 'rf'] and 'wml' only when present.
    """
    res, source = _load_base_factor_panel(rf_daily=rf_daily)
    if res.empty:
        empty = pd.DataFrame(columns=["mkt_excess", "rf"])
        src = dict(source)
        src["missing"] = ["mkt_excess", "smb", "hml", "wml"]
        src["n_obs"] = 0
        empty.attrs["source"] = src
        return empty

    if abs(rf_daily - 0.00025) > 1e-7 and "mkt" in res.columns:
        res["mkt_excess"] = res["mkt"] - rf_daily
        res["rf"] = rf_daily

    if start_date:
        res = res[res.index >= pd.to_datetime(start_date)]
    if end_date:
        res = res[res.index <= pd.to_datetime(end_date)]

    cols = [c for c in ["mkt_excess", "smb", "hml", "wml", "rf"] if c in res.columns]
    out = res[cols]
    src = dict(source)
    src["n_obs"] = int(len(out))
    src["window"] = [
        str(start_date) if start_date else src.get("window", [None, None])[0],
        str(end_date) if end_date else src.get("window", [None, None])[1],
    ]
    missing = [c for c in ("mkt_excess", "smb", "hml", "wml") if c not in out.columns]
    src["missing"] = missing
    src["is_partial"] = bool(missing)
    out.attrs["source"] = src
    return out


_figs_cache: Dict[Tuple[str, str], Dict[str, Any]] = {}
_figs_lock = threading.Lock()


def build_factor_figures(attribution: Dict[str, Any], scheme_name: str) -> Dict[str, Any]:
    """
    Generates JSON-safe Plotly figures for:
      - factor_waterfall: Visualizes alpha, factor contributions, and net return
      - factor_correlation: Correlation bar chart / heatmap of factor betas and contributions
    """
    cache_key = (scheme_name, str(attribution.get("waterfall_data", {})))
    if cache_key in _figs_cache:
        return _figs_cache[cache_key]

    with _figs_lock:
        if cache_key in _figs_cache:
            return _figs_cache[cache_key]

        # 1. Waterfall Figure
        wf = attribution.get("waterfall_data", {})
        fig_wf = go.Figure()
        if wf and "labels" in wf:
            fig_wf.add_trace(go.Waterfall(
                name="Attribution",
                orientation="v",
                measure=wf.get("measures", []),
                x=wf.get("labels", []),
                textposition="outside",
                text=wf.get("text", []),
                y=wf.get("values", []),
                connector=dict(line=dict(color="#475569", width=1.5)),
                increasing=dict(marker=dict(color="#059669")),
                decreasing=dict(marker=dict(color="#DC2626")),
                totals=dict(marker=dict(color="#2563EB")),
            ))
            fig_wf.update_layout(
                template="plotly_white",
                font=dict(color="#0F172A"),
                title=f"Factor Risk Attribution Waterfall — {scheme_name}",
                yaxis_title="Annualized Return Contribution (%)",
                yaxis=dict(ticksuffix="%"),
                height=380,
                margin=dict(l=10, r=10, t=40, b=10),
            )

        # 2. Factor Variance Decomposition Donut Chart
        decomp = attribution.get("variance_decomposition", {})
        fig_decomp = go.Figure()
        if decomp:
            label_map = [
                ("market", "Market (Nifty 50)", "#2563EB"),
                ("size", "Size (SMB)", "#059669"),
                ("value", "Value (HML)", "#D97706"),
                ("momentum", "Momentum (WML)", "#7C3AED"),
            ]
            labels = [lab for key, lab, _ in label_map if key in decomp]
            values = [decomp.get(key, 0) for key, _, _ in label_map if key in decomp]
            colors = [col for key, _, col in label_map if key in decomp]
            fig_decomp.add_trace(go.Pie(
                labels=labels,
                values=values,
                hole=0.55,
                marker=dict(colors=colors),
                textinfo="label+percent",
                hoverinfo="label+value+percent",
            ))
            fig_decomp.update_layout(
                template="plotly_white",
                font=dict(color="#0F172A"),
                title="Systematic Factor Variance Decomposition",
                height=340,
                margin=dict(l=10, r=10, t=40, b=10),
                legend=dict(orientation="h", yanchor="bottom", y=-0.15, xanchor="center", x=0.5),
            )

        res = {
            "factor_waterfall": fig_wf.to_plotly_json(),
            "factor_decomposition": fig_decomp.to_plotly_json(),
        }
        _figs_cache[cache_key] = res
        return res


def _warmup_factor_model() -> None:
    try:
        get_indian_factor_returns()
        from app.db import queries as db_queries
        profile, df_hist = db_queries.get_scheme_profile(100645)
        if profile and not df_hist.empty:
            from app import quant_analytics
            s_date = datetime.date.today() - datetime.timedelta(days=365 * 3)
            e_date = datetime.date.today()
            df_fund, _ = quant_analytics.prepare_fund_timeseries(df_hist, s_date, e_date, risk_free_rate_ann=0.065)
            fund_ret = df_fund.set_index("nav_date")["daily_return"].dropna()
            f_df = get_indian_factor_returns(s_date, e_date)
            res = compute_multivariate_factor_attribution(fund_ret, f_df)
            build_factor_figures(res, profile.get("scheme_name", ""))
    except Exception:
        pass


_warmup_factor_model()
