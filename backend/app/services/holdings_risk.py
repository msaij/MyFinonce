"""Risk on the owner's actual portfolio (Holdings Phase 3).

No new risk math. Two synthetic "NAV" series are built from the ledger and handed
to the engines the Quant page already uses:

* **Realised TWR index** (holdings_analytics.timeseries): what the portfolio
  actually did, day by day, with its real mix at each date. Feeds
  quant_analytics (vol, Sharpe, drawdown, VaR/CVaR, beta/alpha/capture),
  factor_model (4-factor regression) and the Monte Carlo projection.

* **Current-mix backcast**: today's weights held fixed and replayed through
  history. This is what "how would *this* portfolio have done in March 2020?"
  means, and it is the only honest way to stress a portfolio that didn't exist
  (or looked different) back then. Every figure built on it is labelled
  hypothetical. On days a fund had no NAV yet, the remaining weights are
  renormalised and the covered weight is reported, never silently assumed.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

from app import factor_model, quant_analytics, risk_budgeting, stress_testing
from app.core.cache import cached
from app.db.connection import get_data_version
from app.services import holdings_analytics as ha
from app.services import holdings_service as svc

RISK_WINDOW_DAYS = 3 * 365
MIN_TRADING_DAYS = 60
REDUNDANT_CORR = 0.95
BACKCAST_FROM = datetime.date(2019, 10, 1)   # before the earliest stress scenario's lookback


def _rf(rf_pct: float) -> Tuple[float, float]:
    rf_ann = rf_pct / 100.0
    return rf_ann, (1.0 + rf_ann) ** (1.0 / 252.0) - 1.0


def _window(ts: Dict[str, Any]) -> Tuple[pd.Timestamp, pd.Timestamp]:
    idx: pd.DatetimeIndex = ts["index"]
    end = idx[-1]
    return max(idx[0], end - pd.Timedelta(days=RISK_WINDOW_DAYS)), end


def _frame(idx: pd.DatetimeIndex, values: pd.Series) -> pd.DataFrame:
    return pd.DataFrame({"nav_date": idx, "nav": values.values}).dropna()


def _current_weights(pid: str) -> Tuple[Dict[int, float], Dict[int, Dict[str, Any]], float]:
    summ = svc.summary(pid)
    open_pos = [p for p in summ["positions"] if not p["is_closed"] and p["current_value"] > 0]
    total = sum(p["current_value"] for p in open_pos)
    weights = {p["scheme_code"]: p["current_value"] / total for p in open_pos} if total > 0 else {}
    return weights, {p["scheme_code"]: p for p in open_pos}, total


def _strip(d: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in d.items() if k != "regression_points"}


# --- Realised risk --------------------------------------------------------------------

def risk(pid: str, rf_pct: float = 6.5) -> Dict[str, Any]:
    ts = ha.timeseries(pid)
    if ts["empty"]:
        return {"empty": True}
    rf_ann, _ = _rf(rf_pct)
    start, end = _window(ts)
    idx = ts["index"]
    df_w, cov = quant_analytics.prepare_fund_timeseries(_frame(idx, ts["twr"]), start.date(), end.date(), risk_free_rate_ann=rf_ann)
    n = int(cov.get("n_trading_days", 0)) if cov.get("has_data") else 0
    out: Dict[str, Any] = {
        "empty": False,
        "window": {"start": start.strftime("%Y-%m-%d"), "end": end.strftime("%Y-%m-%d"), "n_trading_days": n},
        "risk_free_pct": rf_pct,
    }
    if n < MIN_TRADING_DAYS:
        out["insufficient"] = True
        out["message"] = f"Risk statistics need at least {MIN_TRADING_DAYS} trading days of history; this view has {n}."
        return out

    out["metrics"] = quant_analytics.compute_risk_adjusted_metrics(df_w, rf_ann)
    out["benchmark"] = {"scheme_code": ts["bench_code"], "scheme_name": ts["bench_name"]}
    if ts["bench"] is not None:
        df_b, _ = quant_analytics.prepare_fund_timeseries(_frame(idx, ts["bench"]), start.date(), end.date(), risk_free_rate_ann=rf_ann)
        out["relative"] = _strip(quant_analytics.compute_benchmark_relative_metrics(df_w, df_b, rf_ann)) or None
    out["drawdown"] = {
        "dates": [d.strftime("%Y-%m-%d") for d in df_w["nav_date"]],
        "drawdown_pct": [round(float(v), 4) for v in df_w["drawdown_pct"]],
        "rolling_vol_pct": [None if not np.isfinite(v) else round(float(v), 4) for v in df_w["rolling_vol_ann"]],
    }
    out["holdings"] = _holdings_risk(pid, ts, start, end)
    return out


def _holdings_risk(pid: str, ts: Dict[str, Any], start: pd.Timestamp, end: pd.Timestamp) -> Dict[str, Any]:
    """Correlation, risk contribution and redundancy across the CURRENT holdings,
    over the longest window in which every one of them has NAV history."""
    weights, meta, _ = _current_weights(pid)
    if len(weights) < 2:
        return {"available": False, "reason": "Needs at least two funds."}
    nav = ts["nav"]
    codes = [c for c in weights if c in nav.columns]
    window = nav.loc[(nav.index >= start) & (nav.index <= end), codes]
    excluded: List[int] = []
    # Drop the youngest fund until the common window is long enough.
    while len(codes) >= 2:
        common = window[codes].dropna()
        if len(common) > MIN_TRADING_DAYS:
            break
        youngest = max(codes, key=lambda c: window[c].first_valid_index() or end)
        codes.remove(youngest)
        excluded.append(int(youngest))
    if len(codes) < 2:
        return {"available": False, "reason": "Your funds don't share enough history to correlate."}

    rets = window[codes].dropna().pct_change().dropna()
    corr = rets.corr()
    cov_ann = rets.cov() * 252.0
    w = np.array([weights[c] for c in codes])
    w = w / w.sum()
    names = [str(c) for c in codes]
    rb = risk_budgeting.compute_risk_budgeting(w, cov_ann.values, asset_names=names)

    labels = {c: (meta[c]["scheme_name"] or str(c)) for c in codes}
    contrib = []
    for c in codes:
        contrib.append({
            "scheme_code": int(c),
            "scheme_name": labels[c],
            "weight_pct": float(weights[c] / sum(weights[x] for x in codes) * 100.0),
            "risk_pct": float(rb["percentage_risk_contributions"][str(c)]),
        })
    contrib.sort(key=lambda r: -r["risk_pct"])

    redundant = []
    for i, a in enumerate(codes):
        for b in codes[i + 1:]:
            rho = float(corr.loc[a, b])
            if rho >= REDUNDANT_CORR and meta[a].get("category") == meta[b].get("category"):
                redundant.append({"a": int(a), "a_name": labels[a], "b": int(b), "b_name": labels[b],
                                  "correlation": rho, "category": meta[a].get("category")})

    return {
        "available": True,
        "window_start": rets.index[0].strftime("%Y-%m-%d"),
        "n_days": int(len(rets)),
        "codes": [int(c) for c in codes],
        "names": [labels[c] for c in codes],
        "correlation": [[round(float(x), 4) for x in row] for row in corr.values],
        "risk_contributions": contrib,
        "portfolio_vol_pct": float(rb["portfolio_volatility"] * 100.0),
        "effective_funds": rb["effective_number_of_constituents"],
        "effective_bets": rb["effective_number_of_correlated_bets"],
        "redundant_pairs": redundant,
        "excluded_short_history": excluded,
    }


# --- Factors ------------------------------------------------------------------------------

def factors(pid: str, rf_pct: float = 6.5) -> Dict[str, Any]:
    ts = ha.timeseries(pid)
    if ts["empty"]:
        return {"empty": True}
    rf_ann, rf_daily = _rf(rf_pct)
    start, end = _window(ts)
    df_w, cov = quant_analytics.prepare_fund_timeseries(_frame(ts["index"], ts["twr"]), start.date(), end.date(), risk_free_rate_ann=rf_ann)
    fund_returns = df_w.set_index("nav_date")["daily_return"].dropna() if not df_w.empty else pd.Series(dtype=float)
    base = {"empty": False, "window": {"start": start.strftime("%Y-%m-%d"), "end": end.strftime("%Y-%m-%d")}}
    if len(fund_returns) < MIN_TRADING_DAYS:
        return {**base, "unavailable": True, "reason": f"Factor regression needs {MIN_TRADING_DAYS}+ trading days."}
    factor_df = factor_model.get_indian_factor_returns(start.date(), end.date(), rf_daily=rf_daily)
    if factor_df.empty or any(c not in factor_df.columns for c in ("mkt_excess", "smb", "hml")):
        return {**base, "unavailable": True, "reason": "Factor proxy funds lack NAV data for this window (see Data Management → factor proxies)."}
    try:
        attr = factor_model.compute_multivariate_factor_attribution(fund_returns, factor_df, rf_daily=rf_daily)
    except ValueError as e:
        return {**base, "unavailable": True, "reason": str(e)}
    return {**base, "regression": attr, "figures": factor_model.build_factor_figures(attr, "Your portfolio")}


# --- Current-mix backcast, stress and Monte Carlo ---------------------------------------------

@cached(ttl=600, maxsize=32)
def _backcast_cached(pid_key: str, ledger_v: int, data_v: int) -> Dict[str, Any]:
    from app.db import queries as db

    weights, meta, total = _current_weights(pid_key)
    if not weights:
        return {"empty": True}
    codes = list(weights)
    nav_long = db.get_nav_history_dataframe(codes, start_date=BACKCAST_FROM)
    nav_long["nav_date"] = pd.to_datetime(nav_long["nav_date"])
    nav = nav_long.pivot_table(index="nav_date", columns="scheme_code", values="nav", aggfunc="last").sort_index()
    nav = nav.reindex(columns=codes).ffill()
    rets = nav.pct_change()
    w = pd.Series(weights)
    avail = rets.notna()
    covered = avail.mul(w, axis=1).sum(axis=1)
    port_r = rets.fillna(0.0).mul(w, axis=1).sum(axis=1) / covered.replace(0.0, np.nan)
    port_r = port_r.dropna()
    index = 100.0 * (1.0 + port_r).cumprod()
    return {"empty": False, "index": index, "returns": port_r, "coverage": covered.reindex(index.index),
            "weights": weights, "meta": meta, "total_value": total}


def backcast(pid: str) -> Dict[str, Any]:
    """Cached per (view, ledger version, market-data version). Read-only."""
    return _backcast_cached(svc.view_key(pid), svc.ledger_version(), get_data_version())


def stress(pid: str, shocks: Dict[str, float]) -> Dict[str, Any]:
    bc = backcast(pid)
    if bc["empty"]:
        return {"empty": True}
    ts = ha.timeseries(pid)
    bench_df = None
    if ts.get("bench_code"):
        from app.db import queries as db

        b = db.get_nav_history_dataframe([ts["bench_code"]], start_date=BACKCAST_FROM)
        bench_df = b[["nav_date", "nav"]] if not b.empty else None
    df = pd.DataFrame({"nav_date": bc["index"].index, "nav": bc["index"].values})
    res = stress_testing.evaluate_historical_stress_scenarios(df, benchmark_series=bench_df)
    value = bc["total_value"]
    scenarios = []
    for s in res["scenarios"]:
        cov_window = bc["coverage"][(bc["coverage"].index >= pd.Timestamp(s["window_start"])) &
                                    (bc["coverage"].index <= pd.Timestamp(s["window_end"]))]
        coverage_pct = float(cov_window.mean() * 100.0) if not cov_window.empty else 0.0
        dd = s.get("max_drawdown_pct") if s.get("available") else None
        scenarios.append({
            "id": s["id"], "name": s["name"], "window_start": s["window_start"], "window_end": s["window_end"],
            "available": bool(s.get("available")), "reason": s.get("reason"),
            "drawdown_pct": dd, "benchmark_drawdown_pct": s.get("benchmark_drawdown_pct"),
            "rupee_impact": (value * dd / 100.0) if dd is not None else None,
            "recovery_days": s.get("recovery_days"), "recovered": s.get("recovered"),
            "downside_beta": s.get("downside_beta"), "coverage_pct": coverage_pct,
        })

    parametric: Dict[str, Any] = {"available": False}
    recent = recent_returns(bc)
    if len(recent) >= MIN_TRADING_DAYS:
        fdf = factor_model.get_indian_factor_returns(recent.index[0].date(), recent.index[-1].date(), rf_daily=0.00025)
        if not fdf.empty and all(c in fdf.columns for c in ("mkt_excess", "smb", "hml")):
            try:
                attr = factor_model.compute_multivariate_factor_attribution(recent, fdf)
                sim = stress_testing.simulate_factor_shocks(attr["factor_betas"], shocks, initial_capital=value, is_percentage=True)
                parametric = {"available": True, "betas": attr["factor_betas"], **sim}
            except (ValueError, KeyError):
                pass
    return {"empty": False, "hypothetical": True, "current_value": value, "scenarios": scenarios, "parametric": parametric}


def recent_returns(bc: Dict[str, Any]) -> pd.Series:
    """The current mix's daily returns over the risk window (up to 3 years) -- the
    one calibration sample for the what-if shocks, Monte Carlo and goal projections."""
    r = bc["returns"]
    return r[r.index >= r.index[-1] - pd.Timedelta(days=RISK_WINDOW_DAYS)] if len(r) else r


def monte_carlo(pid: str, years: float = 1.0, sims: int = 1000, seed: int = 42) -> Dict[str, Any]:
    """quant_analytics.run_monte_carlo_simulation (GBM) calibrated on the current
    mix's recent daily returns, starting from today's rupee value."""
    bc = backcast(pid)
    if bc["empty"]:
        return {"empty": True}
    rets = recent_returns(bc)
    if len(rets) < MIN_TRADING_DAYS:
        return {"empty": False, "insufficient": True}
    mc = quant_analytics.run_monte_carlo_simulation(
        1.0, rets.values, n_simulations=sims, n_days=max(21, int(round(years * 252))),
        initial_capital=bc["total_value"], seed=seed,
    )
    return {"empty": False, "years": years, "initial_value": bc["total_value"], **mc}