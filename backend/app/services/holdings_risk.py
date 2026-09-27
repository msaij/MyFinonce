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
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app import factor_model, quant_analytics, risk_budgeting, stress_testing
from app.core.cache import cached
from app.db import holdings as hdb
from app.db.connection import get_data_version
from app.services import holdings_analytics as ha
from app.services import holdings_service as svc

RISK_WINDOW_DAYS = 3 * 365
MIN_TRADING_DAYS = 60
REDUNDANT_CORR = 0.95
BACKCAST_FROM = datetime.date(2019, 10, 1)   # before the earliest stress scenario's lookback


def _rf(rf_pct: float, obs_per_year: float = quant_analytics.TRADING_DAYS_PER_YEAR) -> Tuple[float, float]:
    """The daily risk-free rate must be de-annualised on the same clock the return series
    ticks on: a calendar-day series earns it over 365 steps, a trading-day one over 252.
    Spreading a 252-day rate over 365 observations is what drove Sharpe to -14."""
    rf_ann = rf_pct / 100.0
    return rf_ann, (1.0 + rf_ann) ** (1.0 / obs_per_year) - 1.0


def _window(ts: Dict[str, Any]) -> Tuple[pd.Timestamp, pd.Timestamp]:
    idx: pd.DatetimeIndex = ts["index"]
    end = idx[-1]
    return max(idx[0], end - pd.Timedelta(days=RISK_WINDOW_DAYS)), end


def _frame(idx: pd.DatetimeIndex, values: pd.Series, obs_per_year: Optional[float] = None) -> pd.DataFrame:
    """A NAV-shaped frame for the quant engines, carrying the series' annualisation base
    so nothing downstream has to guess it back out of the rows it was handed."""
    df = pd.DataFrame({"nav_date": idx, "nav": values.values}).dropna()
    return quant_analytics.with_obs_per_year(
        df, obs_per_year if obs_per_year is not None else quant_analytics.infer_obs_per_year(df["nav_date"])
    )


def _current_weights(pid: str) -> Tuple[Dict[int, float], Dict[int, Dict[str, Any]], float]:
    summ = svc.summary(pid)
    open_pos = [p for p in summ["positions"] if not p["is_closed"] and p["current_value"] > 0]
    total = sum(p["current_value"] for p in open_pos)
    weights = {p["scheme_code"]: p["current_value"] / total for p in open_pos} if total > 0 else {}
    return weights, {p["scheme_code"]: p for p in open_pos}, total


def _strip(d: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in d.items() if k != "regression_points"}


# --- Which series risk is measured on ----------------------------------------------------
#
# A new portfolio has days, not years, of its own history, but its funds usually have
# years. Rather than show "needs 60 days" for two months, risk and factor statistics
# fall back to the current-mix backcast (the basis Monte Carlo and stress already
# use) and say so: basis = "realised" (your actual track record) or "current_mix"
# (today's weights replayed over the funds' own history -- hypothetical).

def _risk_basis(pid: str) -> Dict[str, Any]:
    ts = ha.timeseries(pid)
    if ts["empty"]:
        return {"empty": True}
    start, end = _window(ts)
    realised_days = int(((ts["index"] >= start) & (ts["index"] <= end)).sum())
    # Measured here, on the whole index, because this is where the chosen basis is decided --
    # every figure below is annualised on this one number rather than re-deriving its own.
    realised = {"empty": False, "basis": "realised", "index": ts["twr"], "bench": ts["bench"],
                "bench_code": ts["bench_code"], "bench_name": ts["bench_name"], "start": start, "end": end,
                "realised_days": realised_days,
                "obs_per_year": quant_analytics.infer_obs_per_year(ts["index"])}
    if realised_days >= MIN_TRADING_DAYS:
        return realised
    bc = backcast(pid)
    recent = recent_returns(bc) if not bc["empty"] else pd.Series(dtype=float)
    if len(recent) < MIN_TRADING_DAYS:
        return realised
    return {"empty": False, "basis": "current_mix", "index": bc["index"], "bench": bc["bench"],
            "bench_code": bc["bench_code"], "bench_name": bc["bench_name"],
            "start": recent.index[0], "end": recent.index[-1], "realised_days": realised_days,
            "obs_per_year": bc["obs_per_year"]}


def _basis_note(b: Dict[str, Any]) -> Optional[str]:
    if b["basis"] != "current_mix":
        return None
    return (f"Your portfolio has {b['realised_days']} trading day(s) of its own history, so these statistics replay "
            f"today's fund mix over the funds' own NAVs from {b['start'].strftime('%d %b %Y')} (hypothetical). "
            f"They switch to your actual track record once it reaches {MIN_TRADING_DAYS} trading days.")


# --- SEBI riskometer -----------------------------------------------------------------------

#: SEBI's six riskometer levels, lowest first (SEBI circular of 5 Oct 2020, in force from
#: 1 Jan 2021). The order is the whole point: it is an ordinal scale, so a portfolio can be
#: summarised by where its money sits on it.
RISKOMETER_LEVELS = ["Low", "Low to Moderate", "Moderate", "Moderately High", "High", "Very High"]
_LEVEL_RANK = {lvl.lower(): i + 1 for i, lvl in enumerate(RISKOMETER_LEVELS)}


def _riskometer(pid: str) -> Dict[str, Any]:
    """Each held fund's official SEBI riskometer label, and where the money sits on the scale.

    This is the fund house's own mandated classification, published by AMFI -- the one risk
    figure in the app that is not inferred from NAV movements, and for a debt or liquid
    portfolio the one that says most: volatility cannot see credit or interest-rate risk
    that has not happened yet, the riskometer is built to. It needs no price history, so it
    is available even when every statistic below is still waiting for enough days."""
    weights, meta, _ = _current_weights(pid)
    if not weights:
        return {"available": False}
    from app.db.connection import get_connection
    con = get_connection()
    try:
        rows = con.execute("SELECT scheme_code, riskometer, riskometer_as_of FROM schemes WHERE scheme_code = ANY(%s)",
                           ([int(c) for c in weights],)).fetchall()
    finally:
        con.close()
    found = {int(c): (lvl, as_of) for c, lvl, as_of in rows}

    funds, dist = [], {lvl: 0.0 for lvl in RISKOMETER_LEVELS}
    known_weight, rank_sum = 0.0, 0.0
    for code, w in sorted(weights.items(), key=lambda kv: -kv[1]):
        level, as_of = found.get(int(code), (None, None))
        rank = _LEVEL_RANK.get((level or "").strip().lower())
        funds.append({"scheme_code": int(code), "scheme_name": meta[code]["display_name"],
                      "level": RISKOMETER_LEVELS[rank - 1] if rank else level, "rank": rank,
                      "weight_pct": w * 100.0, "as_of": as_of.isoformat() if as_of else None})
        if rank:
            dist[RISKOMETER_LEVELS[rank - 1]] += w * 100.0
            known_weight += w
            rank_sum += w * rank
    if known_weight <= 0:
        return {"available": False, "funds": funds,
                "reason": "AMFI has not published a riskometer for any fund you hold yet."}
    # Money-weighted position on the ordinal scale, then the nearest level. Not an official
    # SEBI figure -- SEBI rates schemes, not portfolios -- so it is labelled as a summary.
    avg_rank = rank_sum / known_weight
    ranked = [f for f in funds if f["rank"]]
    highest = max(ranked, key=lambda f: (f["rank"], f["weight_pct"]))
    return {
        "available": True,
        "levels": RISKOMETER_LEVELS,
        "funds": funds,
        "distribution": [{"level": lvl, "rank": i + 1, "weight_pct": dist[lvl]} for i, lvl in enumerate(RISKOMETER_LEVELS)],
        "weighted_rank": avg_rank,
        "portfolio_level": RISKOMETER_LEVELS[int(round(avg_rank)) - 1],
        "highest": {"level": highest["level"], "scheme_name": highest["scheme_name"], "weight_pct": highest["weight_pct"]},
        "coverage_pct": known_weight * 100.0,
        "as_of": max((f["as_of"] for f in funds if f["as_of"]), default=None),
    }


# --- Risk -----------------------------------------------------------------------------

def risk(pid: str, rf_pct: float = 6.5) -> Dict[str, Any]:
    b = _risk_basis(pid)
    if b["empty"]:
        return {"empty": True}
    ppy = b["obs_per_year"]
    rf_ann, _ = _rf(rf_pct, ppy)
    start, end = b["start"], b["end"]
    df_w, cov = quant_analytics.prepare_fund_timeseries(_frame(b["index"].index, b["index"], ppy), start.date(), end.date(), risk_free_rate_ann=rf_ann)
    n = int(cov.get("n_trading_days", 0)) if cov.get("has_data") else 0
    out: Dict[str, Any] = {
        "empty": False,
        "basis": b["basis"],
        "basis_note": _basis_note(b),
        "window": {"start": start.strftime("%Y-%m-%d"), "end": end.strftime("%Y-%m-%d"), "n_trading_days": n,
                   "obs_per_year": round(float(ppy), 1)},
        "risk_free_pct": rf_pct,
        # Before the history check: the riskometer needs no NAVs, so a brand-new
        # portfolio still gets the one risk read that does not wait for data.
        "riskometer": _riskometer(pid),
    }
    if n < MIN_TRADING_DAYS:
        out["insufficient"] = True
        out["message"] = f"Risk statistics need at least {MIN_TRADING_DAYS} trading days of history; this view has {n}."
        return out

    out["metrics"] = quant_analytics.compute_risk_adjusted_metrics(df_w, rf_ann)
    out["benchmark"] = {"scheme_code": b["bench_code"], "scheme_name": b["bench_name"]}
    if b["bench"] is not None:
        df_b, _ = quant_analytics.prepare_fund_timeseries(_frame(b["bench"].index, b["bench"]), start.date(), end.date(), risk_free_rate_ann=rf_ann)
        out["relative"] = _strip(quant_analytics.compute_benchmark_relative_metrics(df_w, df_b, rf_ann)) or None
    out["drawdown"] = {
        "dates": [d.strftime("%Y-%m-%d") for d in df_w["nav_date"]],
        "drawdown_pct": [round(float(v), 4) for v in df_w["drawdown_pct"]],
        "rolling_vol_pct": [None if not np.isfinite(v) else round(float(v), 4) for v in df_w["rolling_vol_ann"]],
        # How much of today's money the line actually represents on each date. The stress
        # cards already say this; the charts drawn from the same replay must say it too,
        # or the early years read as history when they are a fraction of the portfolio.
        "coverage_pct": _chart_coverage(pid, b, df_w["nav_date"]),
    }
    out["holdings"] = _holdings_risk(pid)
    return out


def _chart_coverage(pid: str, b: Dict[str, Any], dates: pd.Series) -> Optional[List[Optional[float]]]:
    """The backcast's covered weight, aligned to the chart's dates. None on the realised
    basis: that series is the portfolio you actually held, so it is 100% covered by
    construction and a coverage line would be noise."""
    if b["basis"] != "current_mix":
        return None
    bc = backcast(pid)
    if bc["empty"]:
        return None
    cov = bc["coverage"].reindex(pd.DatetimeIndex(dates))
    return [None if not np.isfinite(v) else round(float(v) * 100.0, 2) for v in cov]


def _holdings_risk(pid: str) -> Dict[str, Any]:
    """Correlation, risk contribution and redundancy across the CURRENT holdings,
    from the funds' own NAVs over the risk window (not just the days you've held
    them), shortened only as far as the youngest fund requires."""
    bc = backcast(pid)
    if bc["empty"] or len(bc["weights"]) < 2:
        return {"available": False, "reason": "Needs at least two funds."}
    weights, meta, nav = bc["weights"], bc["meta"], bc["nav"]
    end = nav.index[-1]
    window = nav.loc[nav.index >= end - pd.Timedelta(days=RISK_WINDOW_DAYS)]
    codes = [c for c in weights if c in window.columns]
    excluded: List[int] = []
    # Drop the youngest fund until the common window is long enough.
    while len(codes) >= 2:
        if len(window[codes].dropna()) > MIN_TRADING_DAYS:
            break
        youngest = max(codes, key=lambda c: window[c].first_valid_index() or end)
        codes.remove(youngest)
        excluded.append(int(youngest))
    if len(codes) < 2:
        return {"available": False, "reason": "Your funds don't share enough history to correlate."}

    rets = window[codes].dropna().pct_change().dropna()
    corr = rets.corr()
    w = np.array([weights[c] for c in codes])
    # The common window is the intersection of these funds' NAV dates, so it can tick less
    # often than any one of them; measure the base on what the covariance is actually built
    # from. Percentage risk contributions are scale-invariant, so only the reported
    # portfolio volatility moves with this -- and it moves to the truth.
    ppy = quant_analytics.infer_obs_per_year(rets.index)
    rb = risk_budgeting.compute_risk_budgeting(w / w.sum(), (rets.cov() * ppy).values, asset_names=[str(c) for c in codes])

    labels = {c: meta[c]["display_name"] for c in codes}
    held = sum(weights[x] for x in codes)
    contrib = sorted(
        ({"scheme_code": int(c), "scheme_name": labels[c], "weight_pct": float(weights[c] / held * 100.0),
          "risk_pct": float(rb["percentage_risk_contributions"][str(c)])} for c in codes),
        key=lambda r: -r["risk_pct"],
    )
    redundant = [
        {"a": int(a), "a_name": labels[a], "b": int(b), "b_name": labels[b],
         "correlation": float(corr.loc[a, b]), "category": meta[a].get("category")}
        for i, a in enumerate(codes) for b in codes[i + 1:]
        if float(corr.loc[a, b]) >= REDUNDANT_CORR and meta[a].get("category") == meta[b].get("category")
    ]
    return {
        "available": True,
        "window_start": rets.index[0].strftime("%Y-%m-%d"),
        "n_days": int(len(rets)),
        "obs_per_year": round(float(ppy), 1),
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
    b = _risk_basis(pid)
    if b["empty"]:
        return {"empty": True}
    rf_ann, rf_daily = _rf(rf_pct, b["obs_per_year"])
    start, end = b["start"], b["end"]
    df_w, _ = quant_analytics.prepare_fund_timeseries(_frame(b["index"].index, b["index"], b["obs_per_year"]), start.date(), end.date(), risk_free_rate_ann=rf_ann)
    fund_returns = df_w.set_index("nav_date")["daily_return"].dropna() if not df_w.empty else pd.Series(dtype=float)
    base = {"empty": False, "basis": b["basis"], "basis_note": _basis_note(b),
            "window": {"start": start.strftime("%Y-%m-%d"), "end": end.strftime("%Y-%m-%d")}}
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
    bench_code = ha.explicit_benchmark(svc.resolve_portfolio_ids(pid_key))
    load = codes + ([bench_code] if bench_code and bench_code not in codes else [])
    nav_long = db.get_nav_history_dataframe(load, start_date=BACKCAST_FROM)
    nav_long["nav_date"] = pd.to_datetime(nav_long["nav_date"])
    all_nav = nav_long.pivot_table(index="nav_date", columns="scheme_code", values="nav", aggfunc="last").sort_index()
    nav = all_nav.reindex(columns=codes).ffill()
    rets = nav.pct_change()
    w = pd.Series(weights)
    covered = rets.notna().mul(w, axis=1).sum(axis=1)
    port_r = (rets.fillna(0.0).mul(w, axis=1).sum(axis=1) / covered.replace(0.0, np.nan)).dropna()
    index = 100.0 * (1.0 + port_r).cumprod()
    # The replay ticks on the union of its funds' NAV dates -- one liquid fund in the mix
    # makes the whole series calendar-daily. Measured once, here, and carried from here on.
    obs_per_year = quant_analytics.infer_obs_per_year(index.index)
    quant_analytics.with_obs_per_year(index, obs_per_year)
    quant_analytics.with_obs_per_year(port_r, obs_per_year)

    # The same benchmark rule as the realised view: the owner's chosen scheme, else
    # the category blend -- here at today's (fixed) weights.
    bench, bench_name = None, None
    if bench_code and bench_code in all_nav.columns:
        b = all_nav[bench_code].reindex(index.index).ffill()
        if b.first_valid_index() is not None:
            bench = 100.0 * b / b.loc[b.first_valid_index()]
            bench_name = hdb.display_name(hdb.scheme_meta([bench_code]).get(bench_code, {"scheme_code": bench_code}))
    elif not bench_code:
        code_cat = {c: meta[c].get("category") for c in codes}
        bench = ha.blend_index(pd.DataFrame([weights] * len(index), index=index.index), code_cat, index.index)
        bench_name = ha.BLEND_NAME if bench is not None else None
    return {"empty": False, "index": index, "returns": port_r, "coverage": covered.reindex(index.index),
            "obs_per_year": obs_per_year,
            "weights": weights, "meta": meta, "total_value": total, "nav": nav,
            "bench": bench, "bench_code": bench_code if bench is not None and bench_code else None, "bench_name": bench_name}


def backcast(pid: str) -> Dict[str, Any]:
    """Cached per (view, ledger version, market-data version). Read-only."""
    return _backcast_cached(svc.view_key(pid), svc.ledger_version(), get_data_version())


def stress(pid: str, shocks: Dict[str, float]) -> Dict[str, Any]:
    bc = backcast(pid)
    if bc["empty"]:
        return {"empty": True}
    df = pd.DataFrame({"nav_date": bc["index"].index, "nav": bc["index"].values})
    bench_df = pd.DataFrame({"nav_date": bc["bench"].index, "nav": bc["bench"].values}) if bc["bench"] is not None else None
    res = stress_testing.evaluate_historical_stress_scenarios(df, benchmark_series=bench_df)
    value = bc["total_value"]
    scenarios = []
    about = {c["id"]: c.get("description") for c in stress_testing.HISTORICAL_SCENARIOS}
    for s in res["scenarios"]:
        cov_window = bc["coverage"][(bc["coverage"].index >= pd.Timestamp(s["window_start"])) &
                                    (bc["coverage"].index <= pd.Timestamp(s["window_end"]))]
        coverage_pct = float(cov_window.mean() * 100.0) if not cov_window.empty else 0.0
        dd = s.get("max_drawdown_pct") if s.get("available") else None
        scenarios.append({
            "id": s["id"], "name": s["name"], "window_start": s["window_start"], "window_end": s["window_end"],
            "description": about.get(s["id"]),
            "available": bool(s.get("available")), "reason": s.get("reason"),
            "drawdown_pct": dd, "benchmark_drawdown_pct": s.get("benchmark_drawdown_pct"),
            "rupee_impact": (value * dd / 100.0) if dd is not None else None,
            "recovery_days": s.get("recovery_days"), "recovered": s.get("recovered"),
            "downside_beta": s.get("downside_beta"), "coverage_pct": coverage_pct,
        })

    parametric: Dict[str, Any] = {"available": False}
    recent = recent_returns(bc)
    if len(recent) >= MIN_TRADING_DAYS:
        # The same risk-free clock as factors(): 0.00025 was 6.5% spread over 252 trading
        # days, applied to a replay that ticks every calendar day -- the mismatch that once
        # drove the Sharpe ratio to -14, in a quieter corner.
        _, rf_daily = _rf(6.5, bc["obs_per_year"])
        fdf = factor_model.get_indian_factor_returns(recent.index[0].date(), recent.index[-1].date(), rf_daily=rf_daily)
        if not fdf.empty and all(c in fdf.columns for c in ("mkt_excess", "smb", "hml")):
            try:
                attr = factor_model.compute_multivariate_factor_attribution(recent, fdf, rf_daily=rf_daily)
                sim = stress_testing.simulate_factor_shocks(attr["factor_betas"], shocks, initial_capital=value, is_percentage=True)
                parametric = {"available": True, "betas": attr["factor_betas"], **sim}
            except (ValueError, KeyError):
                pass
    return {"empty": False, "hypothetical": True, "current_value": value, "benchmark_name": bc["bench_name"],
            "scenarios": scenarios, "parametric": parametric}


def recent_returns(bc: Dict[str, Any]) -> pd.Series:
    """The current mix's daily returns over the risk window (up to 3 years) -- the
    one calibration sample for the what-if shocks, Monte Carlo and goal projections."""
    r = bc["returns"]
    if not len(r):
        return r
    # Re-attached explicitly: pandas only propagates .attrs through a slice on a best-effort
    # basis, and this slice is the sample the Monte Carlo and the goal projections calibrate on.
    return quant_analytics.with_obs_per_year(
        r[r.index >= r.index[-1] - pd.Timedelta(days=RISK_WINDOW_DAYS)], bc["obs_per_year"]
    )


def monte_carlo(pid: str, years: float = 1.0, sims: int = 1000, seed: int = 42) -> Dict[str, Any]:
    """quant_analytics.run_monte_carlo_simulation (GBM) calibrated on the current
    mix's recent daily returns, starting from today's rupee value.

    One simulated step is one observation of that sample, so the number of steps in a
    year is the sample's own frequency -- 252 steps of a calendar-day series is 8.3
    months, not a year. The same number is echoed to the client so the chart's
    "years from today" axis divides by exactly what was simulated."""
    bc = backcast(pid)
    if bc["empty"]:
        return {"empty": True}
    rets = recent_returns(bc)
    if len(rets) < MIN_TRADING_DAYS:
        return {"empty": False, "insufficient": True}
    ppy = quant_analytics.get_obs_per_year(rets)
    mc = quant_analytics.run_monte_carlo_simulation(
        1.0, rets.values, n_simulations=sims, n_days=max(21, int(round(years * ppy))),
        initial_capital=bc["total_value"], seed=seed, obs_per_year=ppy,
    )
    # Echoed back so the page can label the chart with exactly the horizon that was run.
    return {"empty": False, "years": years, "horizon_days": int(round(years * 365.25)),
            "initial_value": bc["total_value"], **mc}