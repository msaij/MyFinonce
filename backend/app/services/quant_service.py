"""Quantitative MF Analysis page's composition layer -- ported from
fetcher/pages/4_Quantitative_MF_Analysis.py's page code. Composes
prepare_fund_timeseries + compute_risk_adjusted_metrics +
get_synthetic_category_benchmark + compute_benchmark_relative_metrics +
compute_rolling_metrics into one response, and builds the actual Plotly
figures server-side with plotly.graph_objects -- per the migration plan's
charting philosophy ("reuse the Python, don't reimplement in JS"), which
matters most here for the Monte Carlo fan chart's fill="tonexty" band
pattern. The frontend just renders each figure's JSON as-is via PlotlyChart.
"""

import datetime
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from app import quant_analytics
from app.db import queries as db
from app.services.quant_intelligence import generate_quantitative_intelligence


def _fig(fig: go.Figure) -> dict:
    # to_plotly_json() (not to_dict()) guarantees a JSON-safe {data, layout} dict --
    # it recursively converts numpy arrays/scalars to plain lists/Python types, which
    # to_dict() does not reliably do. This is the exact method the migration plan names
    # for this reuse-Python-figures-as-is pattern.
    return fig.to_plotly_json()


def _dates(series: pd.Series) -> list:
    """Converts a pandas datetime Series to plain "YYYY-MM-DD" strings for use as a
    Plotly trace's x values. Necessary, not cosmetic: passing the Series (or its
    .values) directly leaves a raw numpy datetime64 ndarray inside the trace, and
    fig.to_plotly_json() does NOT convert that to something json.dumps()-safe (it
    reliably converts plain numeric arrays, but not datetime64 ones) -- confirmed by
    a real TypeError: Object of type ndarray is not JSON serializable at the API
    boundary before this helper was introduced. Plotly.js accepts ISO date strings
    for a date-type axis identically to native Date objects, so this costs nothing
    on the frontend."""
    return pd.to_datetime(series).dt.strftime("%Y-%m-%d").tolist()


def _cumulative_return_figure(df_fund: pd.DataFrame, fund_name: str, df_bench: pd.DataFrame, bench_label: str) -> dict:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=_dates(df_fund["nav_date"]), y=df_fund["cum_return"] * 100.0, mode="lines", name=fund_name,
        line=dict(color="#2563EB", width=2.5), hovertemplate="%{fullData.name}: <b>%{y:.2f}%</b><extra></extra>",
    ))
    if not df_bench.empty:
        b = df_bench.copy()
        b["cum_return_pct"] = (b["nav"] / b["nav"].iloc[0] - 1.0) * 100.0
        fig.add_trace(go.Scatter(
            x=_dates(b["nav_date"]), y=b["cum_return_pct"], mode="lines", name=bench_label,
            line=dict(color="#059669", width=2, dash="dash"), hovertemplate="%{fullData.name}: <b>%{y:.2f}%</b><extra></extra>",
        ))
    fig.add_hline(y=0.0, line_dash="dot", line_color="#475569")
    fig.update_layout(
        template="plotly_white", font=dict(color="#0F172A"),
        xaxis_title="Date", yaxis_title="Cumulative Return (%)", yaxis=dict(ticksuffix="%"),
        hovermode="x unified", hoversort="value descending", xaxis=dict(hoverformat="%d-%b-%Y"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=10, r=10, t=20, b=10), height=340,
    )
    return _fig(fig)


def _distribution_figure(df_fund: pd.DataFrame, var_95_daily_pct: float) -> dict:
    daily_rets = df_fund["daily_return"].dropna() * 100.0
    mu = daily_rets.mean()
    sigma = daily_rets.std()
    fig = go.Figure()
    fig.add_trace(go.Histogram(
        x=daily_rets.tolist(), nbinsx=40, histnorm="probability density", name="Empirical Returns",
        marker_color="rgba(30, 58, 138, 0.65)", opacity=0.75,
    ))
    if sigma and sigma > 0:
        x_norm = np.linspace(daily_rets.min(), daily_rets.max(), 200)
        y_norm = (1.0 / (sigma * np.sqrt(2 * np.pi))) * np.exp(-0.5 * ((x_norm - mu) / sigma) ** 2)
        fig.add_trace(go.Scatter(
            x=x_norm.tolist(), y=y_norm.tolist(), mode="lines", name="Fitted Normal (Gaussian)",
            line=dict(color="#EF4444", width=2.5, dash="dash"),
        ))
    fig.add_vline(x=0, line_color="#475569", line_width=1, line_dash="dot")
    fig.add_vline(x=var_95_daily_pct, line_color="#EF4444", line_width=1.5, annotation_text="95% VaR", annotation_position="top left", annotation_font=dict(color="#DC2626", size=11))
    fig.update_layout(
        template="plotly_white", font=dict(color="#0F172A"),
        xaxis_title="Daily Return (%)", yaxis_title="Probability Density",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=10, r=10, t=20, b=10), height=400,
    )
    return _fig(fig)


def _drawdown_figure(df_fund: pd.DataFrame) -> dict:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=_dates(df_fund["nav_date"]), y=df_fund["drawdown_pct"], mode="lines", name="Drawdown (%)", fill="tozeroy",
        line=dict(color="#EF4444", width=1.5), fillcolor="rgba(239, 68, 68, 0.25)",
        hovertemplate="<b>%{y:.4f}%</b><extra></extra>",
    ))
    fig.update_layout(
        template="plotly_white", font=dict(color="#0F172A"),
        xaxis_title="Date", yaxis_title="Drawdown (%)", yaxis=dict(ticksuffix="%", zeroline=True, zerolinecolor="black"),
        hovermode="x unified", hoversort="value descending", xaxis=dict(hoverformat="%d-%b-%Y"), margin=dict(l=10, r=10, t=20, b=10), height=380,
    )
    return _fig(fig)


def _capm_regression_figure(reg_pts: Optional[pd.DataFrame], beta: float, r_squared: float, bench_label: str) -> Optional[dict]:
    if reg_pts is None or reg_pts.empty:
        return None
    x_pts = (reg_pts["r_bench"] * 100.0)
    y_pts = (reg_pts["r_fund"] * 100.0)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x_pts.tolist(), y=y_pts.tolist(), mode="markers", name="Daily Observations", marker=dict(size=6, color="#1E3A8A", opacity=0.6)))
    x_line = np.linspace(x_pts.min(), x_pts.max(), 100)
    intercept = y_pts.mean() - beta * x_pts.mean()
    y_line = beta * x_line + intercept
    fig.add_trace(go.Scatter(x=x_line.tolist(), y=y_line.tolist(), mode="lines", name=f"Regression Fit (β={beta:.2f}, R²={r_squared:.2f})", line=dict(color="#059669", width=2.5)))
    fig.update_layout(
        template="plotly_white", font=dict(color="#0F172A"),
        xaxis_title=f"{bench_label} Daily Return (%)", yaxis_title="Fund Daily Return (%)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1), margin=dict(l=10, r=10, t=20, b=10), height=400,
    )
    return _fig(fig)


def _capture_figure(up_cap: float, dn_cap: float, capture_ratio) -> dict:
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=["Up-Market Capture"], y=[up_cap], marker_color="#059669",
        text=[f"{up_cap:.2f}%"], textposition="outside", textfont=dict(color="#0F172A", size=11),
        name="Up-Market Capture"
    ))
    fig.add_trace(go.Bar(
        x=["Down-Market Capture"], y=[dn_cap], marker_color="#DC2626",
        text=[f"{dn_cap:.2f}%"], textposition="outside", textfont=dict(color="#0F172A", size=11),
        name="Down-Market Capture"
    ))
    fig.add_hline(y=100.0, line_dash="dash", line_color="#475569", annotation_text="Benchmark Parity (100%)", annotation_font=dict(color="#0F172A", size=11))
    fig.update_layout(template="plotly_white", font=dict(color="#0F172A"), showlegend=False, height=330, margin=dict(l=10, r=10, t=20, b=10), yaxis_title="Capture %")
    return _fig(fig)


def _rolling_figure(df_roll: pd.DataFrame, column: str, title_y: str, color: str, hover_fmt: str, zero_line: bool = False) -> dict:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=_dates(df_roll["nav_date"]), y=df_roll[column], mode="lines", line=dict(color=color, width=2), hovertemplate=hover_fmt))
    if zero_line:
        fig.add_hline(y=0, line_dash="dash", line_color="#475569")
    fig.update_layout(template="plotly_white", font=dict(color="#0F172A"), yaxis_title=title_y, hovermode="x unified", hoversort="value descending", xaxis=dict(hoverformat="%d-%b-%Y"), height=360, margin=dict(l=10, r=10, t=20, b=10))
    return _fig(fig)


def _monte_carlo_figure(mc: Dict[str, Any]) -> dict:
    days_x = mc["days"]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=days_x, y=mc["p95"], mode="lines", name="95th Percentile (Bull Case)", line=dict(color="rgba(5, 150, 105, 0.4)", width=1.5), hovertemplate="%{fullData.name}: <b>₹ %{y:,.2f}</b><extra></extra>"))
    fig.add_trace(go.Scatter(x=days_x, y=mc["p75"], mode="lines", name="75th Percentile (Optimistic)", line=dict(color="rgba(5, 150, 105, 0.8)", width=1.5), fill="tonexty", fillcolor="rgba(5, 150, 105, 0.15)", hovertemplate="%{fullData.name}: <b>₹ %{y:,.2f}</b><extra></extra>"))
    fig.add_trace(go.Scatter(x=days_x, y=mc["p50"], mode="lines", name="50th Percentile (Median Path)", line=dict(color="#2563EB", width=3), hovertemplate="%{fullData.name}: <b>₹ %{y:,.2f}</b><extra></extra>"))
    fig.add_trace(go.Scatter(x=days_x, y=mc["p25"], mode="lines", name="25th Percentile (Conservative)", line=dict(color="rgba(220, 38, 38, 0.8)", width=1.5), fill="tonexty", fillcolor="rgba(37, 99, 235, 0.1)", hovertemplate="%{fullData.name}: <b>₹ %{y:,.2f}</b><extra></extra>"))
    fig.add_trace(go.Scatter(x=days_x, y=mc["p5"], mode="lines", name="5th Percentile (Stress Test)", line=dict(color="rgba(220, 38, 38, 0.4)", width=1.5), fill="tonexty", fillcolor="rgba(220, 38, 38, 0.15)", hovertemplate="%{fullData.name}: <b>₹ %{y:,.2f}</b><extra></extra>"))
    fig.add_hline(y=100000.0, line_dash="dash", line_color="#475569", annotation_text="Initial Capital (₹ 100,000)", annotation_font=dict(color="#0F172A", size=11))
    fig.update_layout(
        template="plotly_white", font=dict(color="#0F172A"),
        xaxis_title="Forward Trading Days (1 Year = 252 Days)", yaxis_title="Projected Portfolio Value (₹)",
        hovermode="x unified", hoversort="value descending", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=10, r=10, t=20, b=10), height=450,
    )
    return _fig(fig)


def get_quant_analysis(
    scheme_code: int,
    start_date: datetime.date,
    end_date: datetime.date,
    bench_mode: str = "Category Benchmark (Synthesized Peer Average)",
    custom_peer_code: Optional[int] = None,
    risk_free_rate_pct: float = 6.50,
) -> Dict[str, Any]:
    profile, df_hist_raw = db.get_scheme_profile(scheme_code)
    if not profile or df_hist_raw.empty:
        return {"error": "Selected scheme has no recorded NAV history. Please select another scheme or run backfill."}

    rf_annual = risk_free_rate_pct / 100.0
    df_fund, cov_fund = quant_analytics.prepare_fund_timeseries(df_hist_raw, start_date, end_date, risk_free_rate_ann=rf_annual)
    if df_fund.empty or not cov_fund.get("has_data", False):
        return {"error": "No NAV records found for this scheme within the selected date window. Please expand your date range."}

    q_metrics = quant_analytics.compute_risk_adjusted_metrics(df_fund, risk_free_rate_ann=rf_annual)
    if not q_metrics:
        # compute_risk_adjusted_metrics returns {} for fewer than 3 valid daily-return rows --
        # a real possibility (has_data above only guarantees >=1 NAV row, not >=3) that would
        # otherwise reach the frontend as a "metrics" object missing every expected key.
        return {"error": "Not enough trading days in the selected window to compute risk metrics (minimum 3 required). Please expand your date range."}

    # --- Benchmark selection (mirrors the original page's 3 modes exactly) ---
    df_bench = pd.DataFrame()
    bench_label = "Benchmark"
    bench_unavailable_reason = None

    if bench_mode == "Category Benchmark (Synthesized Peer Average)":
        cat_name = profile.get("category", "")
        bench_label = f"Category Avg ({cat_name})"
        df_bench = quant_analytics.get_synthetic_category_benchmark(
            cat_name, start_date=start_date, end_date=end_date, exclude_scheme_code=scheme_code
        )
        if df_bench.empty:
            bench_unavailable_reason = f"No category-peer NAV data was available to build a synthesized benchmark for {cat_name or 'this category'} in the selected window."

    elif bench_mode == "Nifty 50 Index Fund Proxy":
        bench_label = "Nifty 50 Index Fund"
        nifty_candidates = db.get_schemes_for_dropdown(search_term="nifty 50 index", limit=50)
        nifty_direct_growth = [
            s for s in nifty_candidates
            if s["plan_type"] == "Direct" and s["option_type"] == "Growth" and "nifty 50" in s["scheme_name"].lower()
        ]
        nifty_code = nifty_direct_growth[0]["scheme_code"] if nifty_direct_growth else (nifty_candidates[0]["scheme_code"] if nifty_candidates else None)
        if nifty_code is None:
            bench_unavailable_reason = "No Nifty 50 index fund was found in the local database to use as a proxy."
        else:
            _, df_b_raw = db.get_scheme_profile(nifty_code)
            if not df_b_raw.empty:
                df_bench, _ = quant_analytics.prepare_fund_timeseries(df_b_raw, start_date, end_date, risk_free_rate_ann=rf_annual)
            if df_bench.empty:
                bench_unavailable_reason = "The Nifty 50 index fund found has no NAV data in the selected window."

    elif bench_mode == "Custom Peer Mutual Fund" and custom_peer_code:
        peer_profile, df_b_raw = db.get_scheme_profile(custom_peer_code)
        bench_label = peer_profile["scheme_name"] if peer_profile else "Custom Peer"
        if not df_b_raw.empty:
            df_bench, _ = quant_analytics.prepare_fund_timeseries(df_b_raw, start_date, end_date, risk_free_rate_ann=rf_annual)
        if df_bench.empty:
            bench_unavailable_reason = "The selected peer fund has no NAV data in the selected window."

    b_metrics = quant_analytics.compute_benchmark_relative_metrics(df_fund, df_bench, risk_free_rate_ann=rf_annual)
    if not b_metrics and not bench_unavailable_reason:
        bench_unavailable_reason = "Fewer than 5 trading days overlap between this fund and the benchmark in the selected window."

    # Gross alpha: Net alpha + dated official TER, only when TER is officially sourced.
    ter_raw = profile.get("expense_ratio")
    ter_pct = float(ter_raw) if ter_raw is not None and not pd.isna(ter_raw) else None
    ter_is_official = profile.get("ter_status") == "official"
    alpha_val = b_metrics.get("alpha_annualized_pct") if b_metrics else None
    gross_alpha_pct = (alpha_val + ter_pct) if (alpha_val is not None and ter_is_official and ter_pct is not None) else None

    # --- Rolling metrics (30-day, falling back to 10-day if too little history) ---
    df_roll = df_fund.dropna(subset=["rolling_vol_ann"]).copy() if "rolling_vol_ann" in df_fund.columns else pd.DataFrame()
    roll_window = 30
    if df_roll.empty:
        roll_window = 10
        df_roll = quant_analytics.compute_rolling_metrics(df_fund, window=roll_window, risk_free_rate_ann=rf_annual)

    figures = {
        "cumulative_return": _cumulative_return_figure(df_fund, profile["scheme_name"], df_bench, bench_label),
        "distribution": _distribution_figure(df_fund, q_metrics.get("var_95_daily_pct", 0.0)) if q_metrics else None,
        "drawdown": _drawdown_figure(df_fund),
        "capm_regression": _capm_regression_figure(b_metrics.get("regression_points") if b_metrics else None, b_metrics.get("beta", 1.0) if b_metrics else 1.0, b_metrics.get("r_squared", 0.0) if b_metrics else 0.0, bench_label),
        "capture": _capture_figure(b_metrics.get("up_market_capture_pct", 100.0), b_metrics.get("down_market_capture_pct", 100.0), b_metrics.get("capture_ratio", "-")) if b_metrics else None,
        "rolling_volatility": _rolling_figure(df_roll, "rolling_vol_ann", "Annualized Volatility (%)", "#2563EB", "<b>%{y:.4f}%</b><extra></extra>") if not df_roll.empty and len(df_roll) >= 2 else None,
        "rolling_sharpe": _rolling_figure(df_roll, "rolling_sharpe", "Rolling Sharpe Ratio", "#10B981", "<b>%{y:.4f}</b><extra></extra>", zero_line=True) if not df_roll.empty and len(df_roll) >= 2 else None,
    }

    cat_ret = b_metrics.get("benchmark_cagr_pct") if b_metrics else None
    cat_vol = b_metrics.get("benchmark_vol_annualized_pct") if b_metrics else None

    intelligence = generate_quantitative_intelligence(
        metrics=q_metrics,
        benchmark={
            "label": bench_label,
            "available": bool(b_metrics),
            "metrics": b_metrics or None,
        },
        profile=profile,
        gross_alpha_pct=gross_alpha_pct,
        cat_median_ret=cat_ret,
        cat_median_vol=cat_vol,
    )

    return {
        "profile": profile,
        "coverage": {
            "n_trading_days": cov_fund["n_trading_days"],
            "actual_start": cov_fund["actual_start"],
            "actual_end": cov_fund["actual_end"],
            "is_partial": cov_fund.get("is_partial", False),
            "requested_days": cov_fund.get("requested_days"),
        },
        "metrics": q_metrics,
        "benchmark": {
            "label": bench_label,
            "available": bool(b_metrics),
            "unavailable_reason": bench_unavailable_reason,
            "metrics": b_metrics or None,
        },
        "gross_alpha_pct": gross_alpha_pct,
        "rolling_window": roll_window,
        "intelligence": intelligence,
        "figures": figures,
    }


def get_monte_carlo(scheme_code: int, start_date: datetime.date, end_date: datetime.date, seed: int = 42) -> Dict[str, Any]:
    profile, df_hist_raw = db.get_scheme_profile(scheme_code)
    if not profile or df_hist_raw.empty:
        return {"error": "Selected scheme has no recorded NAV history."}
    df_fund, cov_fund = quant_analytics.prepare_fund_timeseries(df_hist_raw, start_date, end_date)
    if df_fund.empty or not cov_fund.get("has_data", False):
        return {"error": "No NAV records found for this scheme within the selected date window."}

    returns = df_fund["daily_return"].dropna().values
    mc = quant_analytics.run_monte_carlo_simulation(
        latest_nav=float(profile.get("latest_nav", 10.0)), returns=returns,
        n_simulations=500, n_days=252, initial_capital=100000.0, seed=seed,
    )
    if not mc:
        return {"error": "Insufficient return records to calibrate Monte Carlo simulation."}

    p25_val = round(float(mc["p25"][-1]), 2) if len(mc["p25"]) > 0 else float(mc["median_terminal"])
    p75_val = round(float(mc["p75"][-1]), 2) if len(mc["p75"]) > 0 else float(mc["median_terminal"])
    p5_val = round(float(mc["worst_case_p5"]), 2) if mc.get("worst_case_p5") is not None else None
    p95_val = round(float(mc["best_case_p95"]), 2) if mc.get("best_case_p95") is not None else None
    exp_term = round(float(mc["expected_terminal"]), 2) if mc.get("expected_terminal") is not None else None

    return {
        "prob_profit_pct": float(mc["prob_profit_pct"]),
        "prob_beat_inflation_pct": float(mc["prob_beat_inflation_pct"]),
        "prob_beat_12pct": float(mc["prob_beat_12pct"]),
        "median_terminal": float(mc["median_terminal"]),
        "expected_terminal": exp_term,
        "var_95_capital": float(mc["var_95_capital"]),
        "ci_90": [p5_val, p95_val],
        "ci_50": [p25_val, p75_val],
        "worst_case_p5": p5_val,
        "best_case_p95": p95_val,
        "figure": _monte_carlo_figure(mc),
    }
