import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.core.serialize import sanitize_floats
from app.services import quant_service

router = APIRouter(prefix="/api/quant", tags=["quant"])


@router.get("/{scheme_code}")
def get_quant_analysis(
    scheme_code: int,
    start_date: Optional[datetime.date] = Query(None),
    end_date: Optional[datetime.date] = Query(None),
    start: Optional[datetime.date] = Query(None),
    end: Optional[datetime.date] = Query(None),
    bench_mode: str = Query("Category Benchmark (Synthesized Peer Average)"),
    custom_peer_code: Optional[int] = None,
    risk_free_rate_pct: float = 6.50,
) -> dict:
    actual_start = start_date or start
    actual_end = end_date or end
    if not actual_start or not actual_end:
        raise HTTPException(status_code=422, detail="Both start_date and end_date (or start and end) are required")
    if actual_start > actual_end:
        actual_start, actual_end = actual_end, actual_start

    result = quant_service.get_quant_analysis(
        scheme_code=scheme_code,
        start_date=actual_start,
        end_date=actual_end,
        bench_mode=bench_mode,
        custom_peer_code=custom_peer_code,
        risk_free_rate_pct=risk_free_rate_pct,
    )
    if "error" in result:
        raise HTTPException(status_code=422, detail=result["error"])

    # regression_points (raw daily-pair DataFrame, potentially hundreds of rows) is already
    # fully consumed server-side to build the capm_regression figure -- drop it rather than
    # duplicate that data as bulky, otherwise-unused JSON (mirrors /suggest's shortlists strip).
    benchmark = result.get("benchmark") or {}
    metrics = benchmark.get("metrics")
    if metrics:
        benchmark = {**benchmark, "metrics": {k: v for k, v in metrics.items() if k != "regression_points"}}
        result = {**result, "benchmark": benchmark}

    return sanitize_floats(result)


@router.get("/{scheme_code}/monte-carlo")
def get_monte_carlo(
    scheme_code: int,
    start_date: Optional[datetime.date] = Query(None),
    end_date: Optional[datetime.date] = Query(None),
    start: Optional[datetime.date] = Query(None),
    end: Optional[datetime.date] = Query(None),
    seed: int = 42,
) -> dict:
    actual_start = start_date or start
    actual_end = end_date or end
    if not actual_start or not actual_end:
        raise HTTPException(status_code=422, detail="Both start_date and end_date (or start and end) are required")
    if actual_start > actual_end:
        actual_start, actual_end = actual_end, actual_start

    result = quant_service.get_monte_carlo(scheme_code, actual_start, actual_end, seed=seed)
    if "error" in result:
        raise HTTPException(status_code=422, detail=result["error"])
    return sanitize_floats(result)


@router.get("/{scheme_code}/factors")
def get_factor_attribution(
    scheme_code: int,
    start_date: Optional[datetime.date] = Query(None),
    end_date: Optional[datetime.date] = Query(None),
    start: Optional[datetime.date] = Query(None),
    end: Optional[datetime.date] = Query(None),
    risk_free_rate_pct: float = Query(6.50),
) -> dict:
    actual_start = start_date or start
    actual_end = end_date or end

    from app import factor_model, quant_analytics
    from app.db import queries as db_queries
    import pandas as pd

    profile, df_hist_raw = db_queries.get_scheme_profile(scheme_code)
    if not profile or df_hist_raw.empty:
        raise HTTPException(status_code=404, detail="Selected scheme has no recorded NAV history.")

    rf_annual = risk_free_rate_pct / 100.0
    rf_daily = (1.0 + rf_annual) ** (1.0 / 252.0) - 1.0

    s_date = actual_start or (datetime.date.today() - datetime.timedelta(days=365 * 3))
    e_date = actual_end or datetime.date.today()
    if s_date > e_date:
        s_date, e_date = e_date, s_date

    df_fund, cov_fund = quant_analytics.prepare_fund_timeseries(
        df_hist_raw, s_date, e_date, risk_free_rate_ann=rf_annual
    )
    if df_fund.empty or not cov_fund.get("has_data", False):
        raise HTTPException(status_code=422, detail="No NAV records found for this scheme within the selected date window.")

    fund_returns = df_fund.set_index("nav_date")["daily_return"].dropna()
    if len(fund_returns) < 60:
        raise HTTPException(
            status_code=422,
            detail="Insufficient trading days in selected window (minimum 60 required for factor attribution).",
        )

    factor_df = factor_model.get_indian_factor_returns(s_date, e_date, rf_daily=rf_daily)
    source = dict(getattr(factor_df, "attrs", {}).get("source") or {})
    overlap_n = 0
    if not factor_df.empty and "mkt_excess" in factor_df.columns:
        aligned = pd.concat(
            [fund_returns.rename("fund"), factor_df[["mkt_excess"]].dropna()],
            axis=1,
            join="inner",
        ).dropna()
        overlap_n = int(len(aligned))
    source["n_obs"] = overlap_n
    core_missing = [c for c in ("mkt_excess", "smb", "hml") if c not in factor_df.columns]
    if factor_df.empty or overlap_n < 60 or core_missing:
        from app import metrics as app_metrics
        app_metrics.incr("quant_factors_unavailable_total")
        raise HTTPException(
            status_code=422,
            detail={
                "code": "FACTORS_UNAVAILABLE",
                "missing": core_missing or source.get("missing") or ["mkt_excess", "smb", "hml"],
                "proxy_scheme_codes": source.get("proxy_scheme_codes") or factor_model.get_factor_proxies(),
                "n_obs": overlap_n,
            },
        )

    try:
        regression_result = factor_model.compute_multivariate_factor_attribution(
            fund_returns=fund_returns,
            factor_returns=factor_df,
            rf_daily=rf_daily,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    figures = factor_model.build_factor_figures(regression_result, profile.get("scheme_name", f"Scheme {scheme_code}"))

    response = {
        "scheme_code": scheme_code,
        "scheme_name": profile.get("scheme_name", ""),
        "category": profile.get("category", ""),
        "window": {
            "start": str(cov_fund["actual_start"]),
            "end": str(cov_fund["actual_end"]),
            "n_trading_days": cov_fund["n_trading_days"],
        },
        "regression": regression_result,
        "source": source,
        "figures": figures,
    }
    return sanitize_floats(response)


@router.get("/{scheme_code}/stress-test")
def get_stress_test(
    scheme_code: int,
    shock_market: float = Query(-0.15),
    shock_size: float = Query(-0.05),
    shock_value: float = Query(0.02),
    shock_momentum: float = Query(-0.08),
    is_percentage: Optional[bool] = Query(
        None,
        description="True if shocks are in percentage units (e.g. -15.0), False if decimal fractions (e.g. -0.15). If None, auto-detects.",
    ),
) -> dict:
    from app import factor_model, quant_analytics, stress_testing
    from app.db import queries as db_queries

    profile, df_hist_raw = db_queries.get_scheme_profile(scheme_code)
    if not profile or df_hist_raw.empty:
        raise HTTPException(status_code=404, detail="Selected scheme has no recorded NAV history.")

    stress_result = stress_testing.evaluate_historical_stress_scenarios(scheme_code, df_hist=df_hist_raw)

    betas = None
    try:
        df_rets = quant_analytics.compute_daily_returns(df_hist_raw)
        fund_rets = df_rets.set_index("nav_date")["daily_return"].dropna()
        if len(fund_rets) >= 60:
            fact_df = factor_model.get_indian_factor_returns(rf_daily=0.00025)
            if not fact_df.empty and all(c in fact_df.columns for c in ("mkt_excess", "smb", "hml")):
                attr = factor_model.compute_multivariate_factor_attribution(fund_rets, fact_df)
                betas = attr.get("factor_betas")
    except ValueError:
        betas = None

    shocks = {
        "market": shock_market,
        "size": shock_size,
        "value": shock_value,
        "momentum": shock_momentum,
    }
    sim = None
    if betas:
        sim = stress_testing.simulate_factor_shocks(betas, shocks, is_percentage=is_percentage)
    else:
        sim = {"unavailable": True, "code": "FACTORS_UNAVAILABLE"}

    response = {
        "scheme_code": scheme_code,
        "scheme_name": profile.get("scheme_name", ""),
        "category": profile.get("category", ""),
        "scenarios": stress_result["scenarios"],
        "parametric_simulation": sim,
        "parametric_available": bool(betas),
        "figure": stress_result["figure"],
    }
    return sanitize_floats(response)


@router.get("/{scheme_code}/tail-risk")
def get_tail_risk(
    scheme_code: int,
    start_date: Optional[datetime.date] = Query(None),
    end_date: Optional[datetime.date] = Query(None),
    start: Optional[datetime.date] = Query(None),
    end: Optional[datetime.date] = Query(None),
    risk_free_rate_pct: float = Query(6.50),
) -> dict:
    actual_start = start_date or start
    actual_end = end_date or end

    from app import quant_analytics
    from app.db import queries as db_queries

    profile, df_hist_raw = db_queries.get_scheme_profile(scheme_code)
    if not profile or df_hist_raw.empty:
        raise HTTPException(status_code=404, detail="Selected scheme has no recorded NAV history.")

    rf_annual = risk_free_rate_pct / 100.0

    if not actual_start or not actual_end:
        actual_end = datetime.date.today()
        actual_start = actual_end - datetime.timedelta(days=365 * 3)

    if actual_start > actual_end:
        actual_start, actual_end = actual_end, actual_start

    df_fund, cov_fund = quant_analytics.prepare_fund_timeseries(
        df_hist_raw, actual_start, actual_end, risk_free_rate_ann=rf_annual
    )
    if df_fund.empty or not cov_fund.get("has_data", False):
        raise HTTPException(status_code=422, detail="No NAV records found for this scheme within the selected date window.")

    cat_name = profile.get("category", "")
    df_bench = quant_analytics.get_synthetic_category_benchmark(
        cat_name, start_date=actual_start, end_date=actual_end, exclude_scheme_code=scheme_code
    )

    tail_metrics = quant_analytics.compute_tail_risk_metrics(df_fund, df_bench, risk_free_rate_ann=rf_annual)
    if not tail_metrics:
        raise HTTPException(status_code=422, detail="Insufficient trading days in selected window to compute tail risk metrics.")

    figs = {
        "drawdown": quant_service._drawdown_figure(df_fund),
        "distribution": quant_service._distribution_figure(df_fund, tail_metrics.get("var_95_daily_pct", 0.0)),
    }

    response = {
        "scheme_code": scheme_code,
        "scheme_name": profile.get("scheme_name", ""),
        "category": cat_name,
        "coverage": {
            "n_trading_days": cov_fund["n_trading_days"],
            "actual_start": cov_fund["actual_start"],
            "actual_end": cov_fund["actual_end"],
        },
        "tail_risk": tail_metrics,
        "figures": figs,
    }
    return sanitize_floats(response)


@router.get("/{scheme_code}/fee-drag")
def get_fee_drag(
    scheme_code: int,
    paired_scheme_code: Optional[int] = Query(None),
    initial_capital: float = Query(100000.0, ge=0.0),
    horizons: Optional[str] = Query(None, description="Comma-separated horizons e.g. 1Y,3Y,5Y,10Y"),
) -> dict:
    """Computes exact multi-horizon compounding fee drag and gross vs net alpha attribution."""
    from app.db import queries as db_queries
    from app.services import fee_drag, plan_matcher

    profile, _ = db_queries.get_scheme_profile(scheme_code)
    if not profile:
        raise HTTPException(status_code=404, detail=f"Scheme {scheme_code} not found")

    target_paired = paired_scheme_code
    if target_paired is None:
        target_paired = plan_matcher.find_paired_scheme(scheme_code)

    plan_type = str(profile.get("plan_type") or "").strip().lower()
    scheme_name = str(profile.get("scheme_name") or "").strip().lower()
    is_direct = "direct" in plan_type or "direct" in scheme_name

    if target_paired:
        if is_direct:
            d_code, r_code = scheme_code, target_paired
        else:
            d_code, r_code = target_paired, scheme_code
    else:
        if is_direct:
            d_code, r_code = scheme_code, None
        else:
            d_code, r_code = None, scheme_code

    h_list = [h.strip().upper() for h in horizons.split(",")] if horizons else ["1Y", "3Y", "5Y", "10Y"]

    result = fee_drag.compute_fee_drag_attribution(
        direct_scheme_code=d_code,
        regular_scheme_code=r_code,
        horizons=h_list,
        initial_capital=initial_capital,
    )
    result["is_direct"] = is_direct
    result["queried_scheme_code"] = scheme_code
    result["paired_scheme_code"] = target_paired

    return sanitize_floats(result)


