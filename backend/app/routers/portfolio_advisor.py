import datetime
import math
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from app import portfolio_advisor
from app import portfolio_hrp
from app import portfolio_black_litterman
from app import risk_budgeting
from app import quant_analytics
from app import factor_model
from app.db import queries as db
from app.core.serialize import df_to_records, sanitize_floats

router = APIRouter(prefix="/api/portfolio-advisor", tags=["portfolio-advisor"])


@router.get("/questionnaire")
def questionnaire() -> dict:
    return {
        "questions": portfolio_advisor.RISK_QUESTIONNAIRE,
        "risk_tiers": portfolio_advisor.RISK_TIERS,
        "sleeve_allocations": portfolio_advisor.SLEEVE_ALLOCATIONS,
        "score_weights": portfolio_advisor.SCORE_WEIGHTS,
    }


class ScoreRequest(BaseModel):
    answers: Dict[str, int]  # {question_key: point_value}
    horizon_years: Optional[float] = None


@router.post("/score")
def score(req: ScoreRequest) -> dict:
    total, tier = portfolio_advisor.score_questionnaire(req.answers)
    result = {"score": total, "risk_tier": tier}
    if req.horizon_years is not None:
        result["suitability_warning"] = portfolio_advisor.suitability_warning(tier, req.horizon_years)
    return result


class SuggestRequest(BaseModel):
    risk_tier: str
    budget: float
    mode: str  # "Lump Sum" or "SIP (Monthly)"
    lump_sum_amount: float = 0.0
    sip_amount: float = 0.0
    start_date: datetime.date
    end_date: datetime.date
    construction_start: Optional[datetime.date] = None
    construction_end: Optional[datetime.date] = None
    test_start: Optional[datetime.date] = None
    test_end: Optional[datetime.date] = None
    holdout: bool = False


def _resolve_suggest_windows(req: SuggestRequest) -> dict:
    warnings: List[str] = []
    if not req.holdout:
        return {
            "construction_start": req.start_date,
            "construction_end": req.end_date,
            "test_start_effective": req.start_date,
            "test_end": req.end_date,
            "construction_interval": "closed",
            "test_interval": "closed",
            "sample": "is",
            "warnings": ["In-sample: weights and backtest share this window."],
        }

    four = (req.construction_start, req.construction_end, req.test_start, req.test_end)
    if all(d is not None for d in four):
        c_start, c_end, t_start, t_end = four
        if t_start < c_end:
            raise HTTPException(status_code=422, detail="test_start must not be before construction_end")
        if t_start == c_end:
            t_eff = c_end + datetime.timedelta(days=1)
        else:
            t_eff = t_start
        return {
            "construction_start": c_start,
            "construction_end": c_end,
            "test_start_effective": t_eff,
            "test_end": t_end,
            "construction_interval": "closed",
            "test_interval": "left_open",
            "sample": "oos",
            "warnings": [],
        }

    # 3Y fit / 1Y test clipped to [start_date, end_date]
    end = req.end_date
    c_end = end - datetime.timedelta(days=365)
    c_start = max(req.start_date, end - datetime.timedelta(days=365 * 4))
    t_eff = c_end + datetime.timedelta(days=1)
    t_end = end
    approx_fit = int((c_end - c_start).days * 5 / 7)
    approx_test = int((t_end - t_eff).days * 5 / 7)
    if approx_fit < 252 or approx_test < 63 or c_end <= c_start or t_eff > t_end:
        from app import metrics as app_metrics
        app_metrics.incr("suggest_holdout_fallback_is_total")
        return {
            "construction_start": req.start_date,
            "construction_end": req.end_date,
            "test_start_effective": req.start_date,
            "test_end": req.end_date,
            "construction_interval": "closed",
            "test_interval": "closed",
            "sample": "is",
            "warnings": ["Span too short for a 3Y/1Y holdout; showing in-sample comparison."],
        }
    return {
        "construction_start": c_start,
        "construction_end": c_end,
        "test_start_effective": t_eff,
        "test_end": t_end,
        "construction_interval": "closed",
        "test_interval": "left_open",
        "sample": "oos",
        "warnings": warnings,
    }


def _serialize_backtest(bt: Optional[dict], sample: str = "is") -> Optional[dict]:
    if bt is None:
        return None
    if "error" in bt:
        return {"error": bt["error"], "sample": sample}
    bt = dict(bt)
    df_result = bt.pop("df_result")
    return sanitize_floats({**bt, "df_result": df_to_records(df_result), "sample": sample})


@router.post("/suggest")
def suggest(req: SuggestRequest) -> dict:
    bt_mode = "SIP" if req.mode.startswith("SIP") else "Lump Sum"
    windows = _resolve_suggest_windows(req)
    fit_start = windows["construction_start"]
    fit_end = windows["construction_end"]
    test_start = windows["test_start_effective"]
    test_end = windows["test_end"]
    sample = windows["sample"]
    warnings = list(windows["warnings"])

    rules_result = portfolio_advisor.build_rules_based_portfolio(req.risk_tier, fit_start, fit_end, req.budget)
    if "error" in rules_result:
        return {
            "rules_result": {"error": rules_result["error"]},
            "mvo_result": None,
            "hrp_result": None,
            "rules_backtest": None,
            "mvo_backtest": None,
            "hrp_backtest": None,
            "risk_budgeting": None,
            "warnings": warnings,
            "windows": windows,
        }

    mvo_result = portfolio_advisor.build_mvo_portfolio(req.risk_tier, fit_start, fit_end, req.budget, rules_result)

    rules_backtest = portfolio_advisor.backtest_portfolio(
        rules_result.get("weights", {}), test_start, test_end, bt_mode, req.lump_sum_amount, req.sip_amount
    )
    mvo_backtest = None
    if "error" not in mvo_result:
        mvo_backtest = portfolio_advisor.backtest_portfolio(
            mvo_result.get("weights", {}), test_start, test_end, bt_mode, req.lump_sum_amount, req.sip_amount
        )

    # -------------------------------------------------------------
    # Hierarchical Risk Parity (HRP) & Risk Budgeting
    # -------------------------------------------------------------
    hrp_result: Optional[dict] = None
    hrp_backtest: Optional[dict] = None
    rb_summary: Optional[dict] = None

    # Determine asset universe from rules candidate picks
    rules_picks = [p for p in rules_result.get("picks", []) if "scheme_code" in p]
    codes = [int(p["scheme_code"]) for p in rules_picks]

    if len(codes) >= 2:
        df_hist = db.get_nav_history_dataframe(codes, start_date=fit_start, end_date=fit_end)
        if not df_hist.empty:
            df_hist["nav_date"] = pd.to_datetime(df_hist["nav_date"])
            df_hist["nav"] = df_hist["nav"].astype(float)
            wide = df_hist.pivot_table(index="nav_date", columns="scheme_code", values="nav", aggfunc="last").sort_index()
            wide = wide.reindex(columns=codes).ffill().dropna()

            if len(wide) >= 30:
                daily_ret = wide.pct_change().dropna()
                cov_ann = daily_ret.cov().values * 252.0
                corr = daily_ret.corr().values
                str_codes = [str(c) for c in codes]

                # Run HRP
                hrp_out = portfolio_hrp.optimize_hrp(cov_ann, corr, str_codes)
                hrp_weights = {int(k): v for k, v in hrp_out["weights"].items()}

                # Construct HRP picks
                pick_info_by_code = {int(p["scheme_code"]): p for p in rules_picks}
                hrp_picks = []
                for code, w in sorted(hrp_weights.items(), key=lambda kv: -kv[1]):
                    info = pick_info_by_code.get(code, {})
                    hrp_picks.append({
                        "sleeve": info.get("sleeve", info.get("category", "-")),
                        "scheme_code": code,
                        "scheme_name": info.get("scheme_name", str(code)),
                        "fund_house": info.get("fund_house", "-"),
                        "category": info.get("category", "-"),
                        "weight_pct": round(w * 100.0, 2),
                        "amount": round(w * req.budget, 2),
                        "expense_ratio": info.get("expense_ratio"),
                        "sharpe_ratio": info.get("sharpe_ratio"),
                        "sortino_ratio": info.get("sortino_ratio"),
                        "cagr_pct": info.get("cagr_pct"),
                        "max_drawdown_pct": info.get("max_drawdown_pct"),
                        "why": (
                            f"Allocated {round(w * 100.0, 1)}% via Hierarchical Risk Parity (HRP) tree "
                            f"clustering and recursive inverse-variance bisection, resisting covariance collapse."
                        ),
                    })

                hrp_result = {
                    "risk_tier": req.risk_tier,
                    "budget": req.budget,
                    "weights": hrp_weights,
                    "picks": hrp_picks,
                    "method": "hierarchical_risk_parity",
                    "cluster_order": [int(x) if x.isdigit() else x for x in hrp_out["cluster_order"]],
                    "achieved_vol_pct": round(hrp_out["portfolio_vol_ann"], 4),
                }

                hrp_backtest = portfolio_advisor.backtest_portfolio(
                    hrp_weights, test_start, test_end, bt_mode, req.lump_sum_amount, req.sip_amount
                )

                # Compute Risk Budgeting for Rules, MVO, and HRP
                w_rules_vec = np.array([rules_result.get("weights", {}).get(c, 0.0) for c in codes])
                rb_rules = risk_budgeting.compute_risk_budgeting(w_rules_vec, cov_ann, str_codes)

                rb_mvo = None
                if mvo_result and "error" not in mvo_result:
                    w_mvo_vec = np.array([mvo_result.get("weights", {}).get(c, 0.0) for c in codes])
                    rb_mvo = risk_budgeting.compute_risk_budgeting(w_mvo_vec, cov_ann, str_codes)

                w_hrp_vec = np.array([hrp_weights.get(c, 0.0) for c in codes])
                rb_hrp = risk_budgeting.compute_risk_budgeting(w_hrp_vec, cov_ann, str_codes)

                rb_summary = {
                    "rules_based": rb_rules,
                    "mean_variance": rb_mvo,
                    "hierarchical_risk_parity": rb_hrp,
                }

    rules_result_out = {k: v for k, v in rules_result.items() if k != "shortlists"}
    mvo_result_out = {k: v for k, v in mvo_result.items() if k != "shortlists"} if mvo_result else mvo_result

    return {
        "rules_result": sanitize_floats(rules_result_out),
        "mvo_result": sanitize_floats(mvo_result_out),
        "hrp_result": sanitize_floats(hrp_result),
        "rules_backtest": _serialize_backtest(rules_backtest, sample),
        "mvo_backtest": _serialize_backtest(mvo_backtest, sample),
        "hrp_backtest": _serialize_backtest(hrp_backtest, sample),
        "risk_budgeting": sanitize_floats(rb_summary),
        "warnings": warnings,
        "windows": {
            "construction_start": str(fit_start),
            "construction_end": str(fit_end),
            "test_start_effective": str(test_start),
            "test_end": str(test_end),
            "construction_interval": windows["construction_interval"],
            "test_interval": windows["test_interval"],
            "sample": sample,
        },
    }


class BlackLittermanRequest(BaseModel):
    scheme_codes: Optional[List[int]] = None
    asset_names: Optional[List[str]] = None
    prior_weights: Optional[List[float]] = None
    views_matrix_P: List[List[float]]  # (K x N)
    views_returns_Q: List[float]  # (K,)
    views_confidences: Optional[List[float]] = None  # (K,)
    risk_aversion: float = 2.5
    tau: float = 0.05
    cov_matrix: Optional[List[List[float]]] = None  # (N x N)
    start_date: Optional[datetime.date] = None
    end_date: Optional[datetime.date] = None


@router.post("/black-litterman")
def black_litterman(req: BlackLittermanRequest) -> dict:
    """Computes Bayesian Black-Litterman portfolio allocation given market equilibrium priors and tactical views."""
    asset_names = req.asset_names
    cov_arr: Optional[np.ndarray] = None

    if req.cov_matrix is not None:
        cov_arr = np.asarray(req.cov_matrix, dtype=float)
        n = cov_arr.shape[0]
        if asset_names is None:
            asset_names = [f"Asset_{i}" for i in range(n)]
    elif req.scheme_codes is not None and len(req.scheme_codes) > 0:
        codes = req.scheme_codes
        s_date = req.start_date or (datetime.date.today() - datetime.timedelta(days=365 * 3))
        e_date = req.end_date or datetime.date.today()

        df_hist = db.get_nav_history_dataframe(codes, start_date=s_date, end_date=e_date)
        if df_hist.empty:
            raise HTTPException(status_code=400, detail="Insufficient NAV history for requested schemes.")
        df_hist["nav_date"] = pd.to_datetime(df_hist["nav_date"])
        df_hist["nav"] = df_hist["nav"].astype(float)
        wide = df_hist.pivot_table(index="nav_date", columns="scheme_code", values="nav", aggfunc="last").sort_index()
        wide = wide.reindex(columns=codes).ffill().dropna()
        if len(wide) < 30:
            raise HTTPException(status_code=400, detail="Not enough overlapping daily data points.")
        daily_ret = wide.pct_change().dropna()
        cov_arr = daily_ret.cov().values * 252.0
        n = len(codes)
        if asset_names is None:
            asset_names = [str(c) for c in codes]
    else:
        raise HTTPException(status_code=400, detail="Either cov_matrix or scheme_codes must be supplied.")

    n = cov_arr.shape[0]
    prior_w = req.prior_weights
    if prior_w is None:
        prior_w = [1.0 / n] * n
    elif len(prior_w) != n:
        raise HTTPException(status_code=400, detail=f"prior_weights length ({len(prior_w)}) must match asset count ({n}).")

    empty_views = len(req.views_matrix_P) == 0 and len(req.views_returns_Q) == 0
    if empty_views:
        P = np.zeros((0, n), dtype=float)
        Q = np.zeros((0,), dtype=float)
    else:
        P = np.asarray(req.views_matrix_P, dtype=float)
        if P.ndim != 2 or P.shape[0] == 0 or P.shape[1] != n:
            raise HTTPException(status_code=400, detail=f"views_matrix_P must be a 2D matrix of shape (K, N) where N matches asset count ({n}).")
        Q = np.asarray(req.views_returns_Q, dtype=float)
        if Q.ndim != 1 or Q.shape[0] != P.shape[0]:
            raise HTTPException(status_code=400, detail=f"views_returns_Q length must match rows in views_matrix_P ({P.shape[0]}).")

    conf = np.asarray(req.views_confidences, dtype=float) if req.views_confidences is not None else None

    bl_res = portfolio_black_litterman.optimize_black_litterman(
        cov_matrix=cov_arr,
        prior_weights=np.asarray(prior_w, dtype=float),
        views_matrix_P=P,
        views_returns_Q=Q,
        risk_aversion=req.risk_aversion,
        tau=req.tau,
        views_confidences=conf,
        asset_names=asset_names,
    )

    opt_w = np.asarray(bl_res["optimal_weights"], dtype=float)
    rb_res = risk_budgeting.compute_risk_budgeting(opt_w, cov_arr, asset_names)

    return sanitize_floats({
        "prior_returns": bl_res["prior_returns"],
        "posterior_expected_returns": bl_res["posterior_expected_returns"],
        "optimal_weights": bl_res["optimal_weights"],
        "posterior_cov_matrix": bl_res["posterior_cov_matrix"],
        "asset_names": asset_names,
        "weights_by_asset": {asset_names[i]: bl_res["optimal_weights"][i] for i in range(n)},
        "risk_budgeting": rb_res,
    })


@router.get("/efficient-frontier")
def efficient_frontier(
    risk_tier: str = Query("Growth", description="Risk tier to fetch candidate funds for"),
    start_date: Optional[datetime.date] = Query(None, description="Start date for return/cov window"),
    end_date: Optional[datetime.date] = Query(None, description="End date for return/cov window"),
    construction_start: Optional[datetime.date] = Query(None),
    construction_end: Optional[datetime.date] = Query(None),
    scheme_codes: Optional[str] = Query(None, description="Optional comma-separated scheme codes"),
    rf: float = Query(0.065, description="Annualized risk-free rate"),
    n_points: int = Query(50, ge=10, le=100, description="Number of points on the frontier"),
) -> dict:
    """In-sample geometry of the construction window (not the holdout bake-off)."""
    e_date = construction_end or end_date or datetime.date.today()
    s_date = construction_start or start_date or (e_date - datetime.timedelta(days=365 * 3))

    codes: List[int] = []
    if scheme_codes:
        codes = [int(c.strip()) for c in scheme_codes.split(",") if c.strip().isdigit()]
    else:
        rules_res = portfolio_advisor.build_rules_based_portfolio(risk_tier, s_date, e_date, budget=100000.0)
        if "picks" in rules_res:
            codes = [int(p["scheme_code"]) for p in rules_res["picks"] if "scheme_code" in p]

    if len(codes) < 2:
        raise HTTPException(status_code=400, detail="At least 2 assets are required to build an efficient frontier.")

    df_hist = db.get_nav_history_dataframe(codes, start_date=s_date, end_date=e_date)
    if df_hist.empty:
        raise HTTPException(status_code=400, detail="No NAV history available for selected schemes in this window.")

    df_hist["nav_date"] = pd.to_datetime(df_hist["nav_date"])
    df_hist["nav"] = df_hist["nav"].astype(float)
    wide = df_hist.pivot_table(index="nav_date", columns="scheme_code", values="nav", aggfunc="last").sort_index()
    wide = wide.reindex(columns=codes).ffill().dropna()

    if len(wide) < 30:
        raise HTTPException(status_code=400, detail="Insufficient overlapping data points across assets.")

    daily_ret = wide.pct_change().dropna()
    cov_ann = daily_ret.cov().values * 252.0
    cagr_vec = np.array([
        quant_analytics.calendar_day_cagr(
            (wide[c].iloc[-1] / wide[c].iloc[0]) - 1.0, wide.index[0], wide.index[-1]
        )
        for c in codes
    ])

    asset_names = [str(c) for c in codes]

    ef_res = risk_budgeting.compute_efficient_frontier(
        expected_returns=cagr_vec,
        cov_matrix=cov_ann,
        asset_names=asset_names,
        rf=rf,
        n_points=n_points,
    )

    # Compute model portfolio positions on the frontier plane (risk vs return)
    # Rules-based
    rules_port = portfolio_advisor.build_rules_based_portfolio(risk_tier, s_date, e_date, budget=100000.0)
    w_rules = np.array([rules_port.get("weights", {}).get(c, 0.0) for c in codes])
    vol_rules = float(math.sqrt(max(1e-12, w_rules @ cov_ann @ w_rules)))
    ret_rules = float(w_rules @ cagr_vec)

    # HRP
    corr = daily_ret.corr().values
    hrp_out = portfolio_hrp.optimize_hrp(cov_ann, corr, asset_names)
    w_hrp = np.array([hrp_out["weights"].get(str(c), 0.0) for c in codes])
    vol_hrp = float(math.sqrt(max(1e-12, w_hrp @ cov_ann @ w_hrp)))
    ret_hrp = float(w_hrp @ cagr_vec)

    # MVO
    mvo_port = portfolio_advisor.build_mvo_portfolio(risk_tier, s_date, e_date, budget=100000.0, rules_based_result=rules_port)
    vol_mvo = None
    ret_mvo = None
    if "error" not in mvo_port:
        w_mvo = np.array([mvo_port.get("weights", {}).get(c, 0.0) for c in codes])
        vol_mvo = float(math.sqrt(max(1e-12, w_mvo @ cov_ann @ w_mvo)))
        ret_mvo = float(w_mvo @ cagr_vec)

    model_portfolios = {
        "rules_based": {
            "volatility": round(vol_rules, 4),
            "expected_return": round(ret_rules, 4),
            "sharpe_ratio": round((ret_rules - rf) / vol_rules, 4) if vol_rules > 0 else 0.0,
            "weights": {str(codes[i]): round(float(w_rules[i]), 4) for i in range(len(codes))},
        },
        "hierarchical_risk_parity": {
            "volatility": round(vol_hrp, 4),
            "expected_return": round(ret_hrp, 4),
            "sharpe_ratio": round((ret_hrp - rf) / vol_hrp, 4) if vol_hrp > 0 else 0.0,
            "weights": {str(codes[i]): round(float(w_hrp[i]), 4) for i in range(len(codes))},
        },
    }
    if vol_mvo is not None and ret_mvo is not None:
        model_portfolios["mean_variance"] = {
            "volatility": round(vol_mvo, 4),
            "expected_return": round(ret_mvo, 4),
            "sharpe_ratio": round((ret_mvo - rf) / vol_mvo, 4) if vol_mvo > 0 else 0.0,
            "weights": {str(codes[i]): round(float(w_mvo[i]), 4) for i in range(len(codes))},
        }

    return sanitize_floats({
        "risk_tier": risk_tier,
        "sample": "is",
        "construction_start": str(s_date),
        "construction_end": str(e_date),
        "asset_names": asset_names,
        "cagr_by_asset": {str(codes[i]): round(float(cagr_vec[i]), 4) for i in range(len(codes))},
        "frontier_points": ef_res["frontier_points"],
        "min_vol_portfolio": ef_res["min_vol_portfolio"],
        "max_sharpe_portfolio": ef_res["max_sharpe_portfolio"],
        "iso_sharpe_rays": ef_res["iso_sharpe_rays"],
        "iso_sortino_rays": ef_res["iso_sortino_rays"],
        "model_portfolios": model_portfolios,
    })
