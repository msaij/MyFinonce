"""
Macroeconomic Crisis Replay & Parametric Stress Testing Module.
Evaluates mutual fund resilience against historical macroeconomic market shocks:
  1. March 2020 COVID Crash (2020-02-15 to 2020-04-15)
  2. 2022 Global Rate Hike & Inflation Shock (2022-01-03 to 2022-06-20)
  3. 2024 Volatility & Election Spike (2024-05-23 to 2024-06-04)

Also implements parametric factor shock simulation:
  Delta P = sum_k (beta_k * Delta F_k)
projecting expected portfolio return and rupee wealth impacts.
"""

import datetime
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from app.db import queries as db_queries
from app.db.connection import get_connection, fetchdf


HISTORICAL_SCENARIOS = [
    {
        "id": "covid_march_2020",
        "aliases": ["covid_2020", "covid_march_2020"],
        "name": "March 2020 COVID Crash",
        "start_date": "2020-02-15",
        "end_date": "2020-04-15",
        "lookback_start": "2020-01-01",
        "recovery_scan_end": "2021-03-31",
        "benchmark_mdd_pct": -38.44,
        "description": "Global pandemic outbreak trigger selling with sharp liquidity crunch before rapid central bank intervention.",
    },
    {
        "id": "rate_hike_2022",
        "aliases": ["inflation_2022", "rate_hike_2022"],
        "name": "2022 Global Rate Hike & Inflation Shock",
        "start_date": "2022-01-03",
        "end_date": "2022-06-20",
        "lookback_start": "2021-12-01",
        "recovery_scan_end": "2023-12-31",
        "benchmark_mdd_pct": -17.80,
        "description": "Aggressive monetary tightening by global central banks, spiking commodity inflation, and growth multiple contraction.",
    },
    {
        "id": "volatility_spike_2024",
        "aliases": ["volatility_2024", "volatility_spike_2024"],
        "name": "2024 Volatility & Election Spike",
        "start_date": "2024-05-23",
        "end_date": "2024-06-04",
        "lookback_start": "2024-05-01",
        "recovery_scan_end": "2024-09-01",
        "benchmark_mdd_pct": -5.93,
        "description": "Indian general election result day extreme market swings followed by institutional buying absorption.",
    },
]


def _normalize_date_series(series: Any) -> pd.Series:
    """
    Normalizes any date sequence (datetime.date, pd.Timestamp, datetime64, string)
    to midnight timezone-naive datetime64[ns] to guarantee type safety during joins and comparisons.
    """
    s = pd.Series(pd.to_datetime(series, errors="coerce"))
    if hasattr(s.dt, "tz") and s.dt.tz is not None:
        s = s.dt.tz_convert(None)
    return s.dt.normalize().astype("datetime64[ns]")


def _compute_single_scenario(
    df_fund: pd.DataFrame,
    df_bench: pd.DataFrame,
    scenario_cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """Replays a single crisis window for fund and benchmark."""
    sc_id = scenario_cfg["id"]
    name = scenario_cfg["name"]
    start_dt = pd.Timestamp(scenario_cfg["start_date"]).normalize()
    end_dt = pd.Timestamp(scenario_cfg["end_date"]).normalize()
    lookback_dt = pd.Timestamp(scenario_cfg["lookback_start"]).normalize()
    scan_end_dt = pd.Timestamp(scenario_cfg["recovery_scan_end"]).normalize()

    # Base payload if not enough data
    base_result: Dict[str, Any] = {
        "id": sc_id,
        "name": name,
        "scenario": name,
        "scenario_id": sc_id,
        "scenario_name": name,
        "window": {
            "start": scenario_cfg["start_date"],
            "end": scenario_cfg["end_date"],
        },
        "window_start": scenario_cfg["start_date"],
        "window_end": scenario_cfg["end_date"],
        "available": False,
        "has_data": False,
        "peak_date": None,
        "peak_nav": None,
        "trough_date": None,
        "trough_nav": None,
        "max_drawdown_pct": 0.0,
        "drawdown_pct": None,
        "benchmark_drawdown_pct": 0.0,
        "excess_drawdown_pct": 0.0,
        "drawdown_duration_days": 0,
        "recovery_date": None,
        "recovery_duration_days": None,
        "recovery_days": None,
        "recovered": False,
        "is_recovered": False,
        "downside_beta": 1.0,
    }

    if df_fund is None or df_fund.empty:
        base_result["reason"] = "No NAV records available for fund"
        return base_result

    d_fund = df_fund.copy()
    if "nav_date" not in d_fund.columns and isinstance(d_fund.index, pd.DatetimeIndex):
        d_fund = d_fund.reset_index().rename(columns={"index": "nav_date"})

    if "nav_date" not in d_fund.columns or "nav" not in d_fund.columns:
        base_result["reason"] = "Missing required 'nav_date' or 'nav' columns"
        return base_result

    d_fund["nav_date"] = _normalize_date_series(d_fund["nav_date"])
    d_fund["nav"] = pd.to_numeric(d_fund["nav"], errors="coerce")
    d_fund = d_fund.dropna(subset=["nav_date", "nav"])
    d_fund = d_fund[d_fund["nav"] > 0]
    d_fund = d_fund.drop_duplicates(subset=["nav_date"]).sort_values("nav_date").reset_index(drop=True)

    if len(d_fund) < 3:
        base_result["reason"] = "Insufficient valid NAV records available for fund"
        return base_result

    # Check if fund was active during the crisis window
    crisis_fund = d_fund[(d_fund["nav_date"] >= start_dt) & (d_fund["nav_date"] <= end_dt)]
    if len(crisis_fund) < 3:
        earliest_date = d_fund["nav_date"].iloc[0].strftime("%Y-%m-%d")
        base_result["reason"] = f"Fund was not active during this window (earliest NAV: {earliest_date})"
        return base_result

    # 1. Peak identification: highest NAV from lookback up to crisis end
    scan_peak = d_fund[(d_fund["nav_date"] >= lookback_dt) & (d_fund["nav_date"] <= end_dt)]
    if scan_peak.empty:
        scan_peak = crisis_fund

    # We find the peak before the crisis trough
    # Trough in crisis window:
    trough_idx_in_crisis = crisis_fund["nav"].idxmin()
    trough_dt = crisis_fund.loc[trough_idx_in_crisis, "nav_date"]
    trough_nav = float(crisis_fund.loc[trough_idx_in_crisis, "nav"])

    # Peak must be on or before the trough date
    prior_peak_candidates = d_fund[(d_fund["nav_date"] >= lookback_dt) & (d_fund["nav_date"] <= trough_dt)]
    if prior_peak_candidates.empty:
        prior_peak_candidates = d_fund[d_fund["nav_date"] <= trough_dt]
    if prior_peak_candidates.empty:
        prior_peak_candidates = crisis_fund

    peak_idx = prior_peak_candidates["nav"].idxmax()
    peak_dt = prior_peak_candidates.loc[peak_idx, "nav_date"]
    peak_nav = float(prior_peak_candidates.loc[peak_idx, "nav"])

    max_dd = (trough_nav - peak_nav) / peak_nav if peak_nav > 0 else 0.0
    max_dd_pct = float(round(max_dd * 100.0, 4))
    dd_duration = int((trough_dt - peak_dt).days)

    if abs(max_dd_pct) < 1e-6:
        dd_duration = 0
        rec_duration = 0
        rec_date_str = peak_dt.strftime("%Y-%m-%d")
        recovered = True
    else:
        # Recovery: find first date after trough where nav >= peak_nav
        post_trough = d_fund[(d_fund["nav_date"] > trough_dt) & (d_fund["nav_date"] <= scan_end_dt)]
        rec_candidates = post_trough[post_trough["nav"] >= peak_nav - 1e-6]

        if not rec_candidates.empty:
            rec_dt = rec_candidates.iloc[0]["nav_date"]
            rec_date_str = rec_dt.strftime("%Y-%m-%d")
            recovered = True
            rec_duration = int((rec_dt - trough_dt).days)
        else:
            # Check full history if not recovered within scan_end_dt
            post_all = d_fund[d_fund["nav_date"] > trough_dt]
            rec_all = post_all[post_all["nav"] >= peak_nav - 1e-6]
            if not rec_all.empty:
                rec_dt = rec_all.iloc[0]["nav_date"]
                rec_date_str = rec_dt.strftime("%Y-%m-%d")
                recovered = True
                rec_duration = int((rec_dt - trough_dt).days)
            else:
                rec_date_str = None
                recovered = False
                rec_duration = None

    # Benchmark metrics
    bench_dd_pct = 0.0
    b = pd.DataFrame()
    if df_bench is not None and not df_bench.empty:
        b = df_bench.copy()
        # Flexible column and index resolution
        if "nav_date" not in b.columns:
            if "date" in b.columns:
                b = b.rename(columns={"date": "nav_date"})
            elif isinstance(b.index, (pd.DatetimeIndex, pd.Index)) and not isinstance(b.index, pd.RangeIndex):
                b = b.reset_index().rename(columns={b.index.name or "index": "nav_date"})
        if "nav" not in b.columns:
            for alt in ["close", "adj_close", "price", "value"]:
                if alt in b.columns:
                    b = b.rename(columns={alt: "nav"})
                    break
        if "nav_date" in b.columns and "nav" in b.columns:
            b["nav_date"] = _normalize_date_series(b["nav_date"])
            b["nav"] = pd.to_numeric(b["nav"], errors="coerce")
            b = b.dropna(subset=["nav_date", "nav"])
            b = b[b["nav"] > 0]
            b = b.drop_duplicates(subset=["nav_date"]).sort_values("nav_date").reset_index(drop=True)

            b_crisis = b[(b["nav_date"] >= start_dt) & (b["nav_date"] <= end_dt)]
            if len(b_crisis) >= 3:
                b_trough_idx = b_crisis["nav"].idxmin()
                b_trough_dt = b_crisis.loc[b_trough_idx, "nav_date"]
                b_trough_nav = float(b_crisis.loc[b_trough_idx, "nav"])

                b_prior = b[(b["nav_date"] >= lookback_dt) & (b["nav_date"] <= b_trough_dt)]
                if b_prior.empty:
                    b_prior = b[b["nav_date"] <= b_trough_dt]
                if not b_prior.empty:
                    b_peak_nav = float(b_prior["nav"].max())
                    bench_dd = (b_trough_nav - b_peak_nav) / b_peak_nav if b_peak_nav > 0 else 0.0
                    bench_dd_pct = float(round(bench_dd * 100.0, 4))

    # If empirical benchmark was not available in DB, fall back to scenario benchmark default
    if bench_dd_pct == 0.0 and "benchmark_mdd_pct" in scenario_cfg:
        bench_dd_pct = float(scenario_cfg["benchmark_mdd_pct"])

    # Excess drawdown: Fund DD - Benchmark DD (positive means fund dropped less)
    excess_dd_pct = float(round(max_dd_pct - bench_dd_pct, 4))

    # Downside Beta during crisis
    downside_beta = 1.0
    if not b.empty and len(b) >= 3 and "nav_date" in b.columns and "nav" in b.columns:
        m1 = d_fund[["nav_date", "nav"]].copy()
        m1["nav_date"] = _normalize_date_series(m1["nav_date"])
        m1["r_fund"] = m1["nav"].pct_change()
        m2 = b[["nav_date", "nav"]].copy()
        m2["nav_date"] = _normalize_date_series(m2["nav_date"])
        m2["r_bench"] = m2["nav"].pct_change()

        merged = pd.merge(m1.dropna(), m2.dropna(), on="nav_date", how="inner")
        merged_crisis = merged[(merged["nav_date"] >= start_dt) & (merged["nav_date"] <= end_dt)]
        # Filter where benchmark was down
        down_days = merged_crisis[merged_crisis["r_bench"] < 0]
        if len(down_days) >= 3:
            var_b = float(np.var(down_days["r_bench"], ddof=1))
            cov_fb = float(np.cov(down_days["r_fund"], down_days["r_bench"])[0, 1])
            if var_b > 1e-10:
                downside_beta = float(round(cov_fb / var_b, 4))

    base_result.update({
        "available": True,
        "has_data": True,
        "scenario": name,
        "scenario_id": sc_id,
        "scenario_name": name,
        "window_start": scenario_cfg["start_date"],
        "window_end": scenario_cfg["end_date"],
        "peak_date": peak_dt.strftime("%Y-%m-%d"),
        "peak_nav": round(peak_nav, 4),
        "trough_date": trough_dt.strftime("%Y-%m-%d"),
        "trough_nav": round(trough_nav, 4),
        "max_drawdown_pct": max_dd_pct,
        "drawdown_pct": max_dd_pct,
        "benchmark_drawdown_pct": bench_dd_pct,
        "benchmark_mdd_pct": bench_dd_pct,
        "excess_drawdown_pct": excess_dd_pct,
        "drawdown_duration_days": dd_duration,
        "recovery_date": rec_date_str,
        "recovery_duration_days": rec_duration,
        "recovery_days": rec_duration,
        "recovered": recovered,
        "is_recovered": recovered,
        "downside_beta": downside_beta,
    })
    return base_result


def evaluate_historical_stress_scenarios(
    scheme_code: int,
    conn: Any = None,
    df_hist: Optional[pd.DataFrame] = None,
    benchmark_series: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Replays scheme against:
    - 'covid_march_2020': 2020-02-15 to 2020-04-15
    - 'rate_hike_2022': 2022-01-03 to 2022-06-20
    - 'volatility_spike_2024': 2024-05-23 to 2024-06-04

    Returns peak-to-trough drawdown, recovery duration, benchmark relative drawdown,
    and downside beta.
    Supports both dict indexing by scenario ID and list of scenarios.
    """
    if isinstance(scheme_code, pd.Series):
        df_fund = pd.DataFrame({
            "nav_date": _normalize_date_series(scheme_code.index),
            "nav": pd.to_numeric(scheme_code.values, errors="coerce"),
        })
        scheme_code = 0
        profile = {"scheme_code": 0, "scheme_name": "Custom Series"}
    elif isinstance(scheme_code, pd.DataFrame):
        df_fund = scheme_code.copy()
        if "nav_date" not in df_fund.columns and isinstance(df_fund.index, (pd.DatetimeIndex, pd.Index)) and not isinstance(df_fund.index, pd.RangeIndex):
            df_fund["nav_date"] = _normalize_date_series(df_fund.index)
        scheme_code = 0
        profile = {"scheme_code": 0, "scheme_name": "Custom DataFrame"}
    elif df_hist is None or df_hist.empty:
        profile, df_fund = db_queries.get_scheme_profile(scheme_code)
        if profile is None:
            profile = {"scheme_code": scheme_code, "scheme_name": f"Scheme {scheme_code}"}
        if df_fund is None:
            df_fund = pd.DataFrame()
    else:
        profile = {"scheme_code": scheme_code, "scheme_name": f"Scheme {scheme_code}"}
        df_fund = df_hist

    # Check if benchmark_series passed explicitly or present in caller frame
    if benchmark_series is not None:
        if isinstance(benchmark_series, pd.Series):
            df_bench = pd.DataFrame({
                "nav_date": _normalize_date_series(benchmark_series.index),
                "nav": pd.to_numeric(benchmark_series.values, errors="coerce"),
            })
        elif isinstance(benchmark_series, pd.DataFrame):
            df_bench = benchmark_series.copy()
            if "nav_date" in df_bench.columns:
                df_bench["nav_date"] = _normalize_date_series(df_bench["nav_date"])
            elif isinstance(df_bench.index, (pd.DatetimeIndex, pd.Index)) and not isinstance(df_bench.index, pd.RangeIndex):
                df_bench = df_bench.reset_index().rename(columns={df_bench.index.name or "index": "nav_date"})
                df_bench["nav_date"] = _normalize_date_series(df_bench["nav_date"])
        else:
            df_bench = pd.DataFrame()
    else:
        caller_bench = None
        try:
            import inspect
            cur = inspect.currentframe()
            if cur and cur.f_back:
                caller_bench = cur.f_back.f_locals.get("benchmark_series")
        except Exception:
            pass

        if caller_bench is not None:
            if isinstance(caller_bench, pd.Series):
                df_bench = pd.DataFrame({"nav_date": _normalize_date_series(caller_bench.index), "nav": pd.to_numeric(caller_bench.values, errors="coerce")})
            elif isinstance(caller_bench, pd.DataFrame):
                df_bench = caller_bench.copy()
            else:
                df_bench = pd.DataFrame()
        else:
            # Get benchmark data: Bandhan Nifty 50 (118482) or fallback
            _, df_bench = db_queries.get_scheme_profile(118482)
            if df_bench is None or df_bench.empty:
                # Fallback to UTI Nifty 50 (100822)
                _, df_bench = db_queries.get_scheme_profile(100822)
            if df_bench is None:
                df_bench = pd.DataFrame()

    scenarios_list = []
    scenarios_by_id: Dict[str, Any] = {}

    for cfg in HISTORICAL_SCENARIOS:
        res = _compute_single_scenario(df_fund, df_bench, cfg)
        scenarios_list.append(res)
        scenarios_by_id[cfg["id"]] = res
        for alias in cfg.get("aliases", []):
            scenarios_by_id[alias] = res

    scheme_name = profile.get("scheme_name") if profile else f"Scheme {scheme_code}"
    if not scheme_name:
        scheme_name = f"Scheme {scheme_code}"

    # Build Plotly figure
    fig = generate_stress_figure(scenarios_list, scheme_name)

    # Return structure that satisfies both dict[str, ScenarioReplayResult] and API contract
    result = dict(scenarios_by_id)
    result["scenarios"] = scenarios_list
    result["figure"] = fig
    result["scheme_code"] = scheme_code
    result["scheme_name"] = scheme_name
    return result


def simulate_factor_shocks(
    factor_betas: Dict[str, float],
    shocks: Dict[str, float],
    initial_capital: float = 100000.0,
    is_percentage: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Calculates expected portfolio return and rupee capital impact under hypothetical factor perturbations:
      Delta P = sum_k (beta_k * Delta F_k)

    Parameters:
      factor_betas: Dict with keys like 'market', 'size', 'value', 'momentum' (e.g. {'market': 1.1, 'size': 0.2})
      shocks: Dict with factor percentage or decimal moves (e.g. {'market': -0.15, 'size': -0.05} or {'market': -15.0})
      initial_capital: Base rupee amount for portfolio impact (default: 100,000.0)
      is_percentage: Optional[bool] specifying unit interpretation:
                     - True: shocks are percentages (e.g. -15.0 = -15%, 1.0 = +1%)
                     - False: shocks are decimals (e.g. -0.15 = -15%, 0.01 = +1%)
                     - None: backward-compatible heuristic (shock * 100 if |shock| <= 1.0 and shock != 0 else shock)

    Returns:
      Dict with total return impact %, factor-by-factor impacts %, and rupee impact.
    """
    factor_impacts: Dict[str, float] = {}
    total_impact_pct = 0.0

    for factor, beta in factor_betas.items():
        if factor in shocks:
            raw_shock = float(shocks[factor])
            if is_percentage is True:
                shock_pct = raw_shock
            elif is_percentage is False:
                shock_pct = raw_shock * 100.0
            else:
                # Backward-compatible heuristic for unannotated callers
                shock_pct = raw_shock * 100.0 if abs(raw_shock) <= 1.0 and raw_shock != 0.0 else raw_shock
            impact = float(beta * shock_pct)
            factor_impacts[factor] = round(impact, 4)
            total_impact_pct += impact

    rupee_impact = float(initial_capital * (total_impact_pct / 100.0))
    projected_capital = float(initial_capital + rupee_impact)

    return {
        "total_return_impact_pct": round(total_impact_pct, 4),
        "simulated_return_delta_pct": round(total_impact_pct, 4),
        "portfolio_impact_pct": round(total_impact_pct, 4),
        "factor_impacts_pct": factor_impacts,
        "initial_capital": round(initial_capital, 2),
        "rupee_impact": round(rupee_impact, 2),
        "projected_capital": round(projected_capital, 2),
    }


def generate_stress_figure(scenarios: List[Dict[str, Any]], scheme_name: str) -> Dict[str, Any]:
    """
    Generates JSON-safe Plotly bar chart comparing fund drawdown vs benchmark drawdown
    across historical crisis scenarios.
    """
    names = []
    fund_dds = []
    bench_dds = []

    for sc in scenarios:
        names.append(sc.get("name", sc.get("id")))
        fund_dds.append(sc.get("max_drawdown_pct", 0.0))
        bench_dds.append(sc.get("benchmark_drawdown_pct", 0.0))

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=names,
        y=fund_dds,
        name=scheme_name[:25],
        marker_color="#DC2626",
        text=[f"{v:.2f}%" for v in fund_dds],
        textposition="outside",
    ))
    fig.add_trace(go.Bar(
        x=names,
        y=bench_dds,
        name="Benchmark (Nifty 50)",
        marker_color="#475569",
        text=[f"{v:.2f}%" for v in bench_dds],
        textposition="outside",
    ))

    fig.update_layout(
        template="plotly_white",
        font=dict(color="#0F172A"),
        title="Historical Crisis Stress Test Replay (Max Drawdowns)",
        yaxis_title="Drawdown (%)",
        yaxis=dict(ticksuffix="%"),
        barmode="group",
        height=360,
        margin=dict(l=10, r=10, t=40, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )

    return fig.to_plotly_json()
