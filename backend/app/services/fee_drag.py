"""Fee Drag Attribution Modeling Service.

Computes exact multi-horizon compounding fee drag, rupee wealth erosion,
and 3-tier gross vs net alpha attribution comparing Direct and Regular mutual fund plans.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.db.connection import get_connection, fetchdf


HORIZON_YEARS: Dict[str, float] = {
    "1Y": 1.0,
    "3Y": 3.0,
    "5Y": 5.0,
    "10Y": 10.0,
    "15Y": 15.0,
    "20Y": 20.0,
    "30Y": 30.0,
}


def _load_ter_histories(con: Any, direct_code: Optional[int], regular_code: Optional[int]) -> Tuple[pd.DataFrame, pd.DataFrame]:
    codes = [c for c in (direct_code, regular_code) if c is not None]
    if not codes:
        return pd.DataFrame(), pd.DataFrame()
    try:
        df = fetchdf(con.execute(
            f"""SELECT scheme_code, ter_date, total_ter_pct
                FROM ter_history
                WHERE scheme_code IN ({','.join(str(c) for c in codes)})
                ORDER BY ter_date ASC"""
        ))
    except Exception:
        return pd.DataFrame(), pd.DataFrame()
    if df.empty:
        return pd.DataFrame(), pd.DataFrame()
    d = df[df["scheme_code"] == direct_code].copy() if direct_code is not None else pd.DataFrame()
    r = df[df["scheme_code"] == regular_code].copy() if regular_code is not None else pd.DataFrame()
    return d, r


def _time_varying_ter_decimal(hist: pd.DataFrame, years: float) -> Optional[float]:
    """Mean official TER (percent → decimal) when history is dense; else None."""
    if hist is None or hist.empty or "total_ter_pct" not in hist.columns:
        return None
    vals = hist["total_ter_pct"].dropna().astype(float)
    if vals.empty:
        return None
    span_days = max(365.0 * years, 365.0)
    if len(vals) < max(8, int(span_days / 45.0)):
        return None
    mean_pct = float(vals.mean())
    return mean_pct / 100.0


def _cagr_from_period_return(return_pct: Optional[float], years: float) -> Optional[float]:
    """Converts a cumulative percentage return over N years into an annualized CAGR decimal.

    E.g. return_pct = 50.0% over 3 years -> (1 + 0.50)^(1/3) - 1 = 0.1447 (14.47% p.a.).
    """
    if return_pct is None or math.isnan(return_pct):
        return None
    tot_growth = 1.0 + (return_pct / 100.0)
    if tot_growth <= 0:
        return -1.0
    if years <= 0:
        return 0.0
    return float(tot_growth ** (1.0 / years) - 1.0)


def compute_fee_drag_attribution(
    direct_cagr: Optional[float] = None,
    regular_cagr: Optional[float] = None,
    ter_direct: Optional[float] = None,
    ter_regular: Optional[float] = None,
    direct_alpha: float = 0.0,
    regular_alpha: float = 0.0,
    horizons: Optional[List[str]] = None,
    initial_capital: float = 100000.0,
    direct_scheme_code: Optional[int] = None,
    regular_scheme_code: Optional[int] = None,
    conn: Any = None,
) -> Dict[str, Any]:
    """Computes exact multi-horizon compounding fee drag and 3-tier gross vs net alpha isolation.

    Supports both:
    1. Direct numerical inputs (CAGRs as decimal fractions e.g. 0.15, TERs as decimal fractions e.g. 0.0075).
    2. Database lookup via direct_scheme_code and regular_scheme_code.

    Formulas:
      - W_D(T) = C_0 * (1 + CAGR_D)^T
      - W_R(T) = C_0 * (1 + CAGR_R)^T
      - FeeDrag_cum(T) = (W_D(T) - W_R(T)) / W_D(T) * 100%
      - CAGR Spread = (CAGR_D - CAGR_R) * 100%
      - Rupee Wealth Erosion = C_0 * (W_D(T) - W_R(T))
      - Gross Alpha = Direct Alpha + avg(TER_Direct)
      - Net Alpha Regular = Gross Alpha - avg(TER_Regular) = Direct Alpha - (TER_R - TER_D)
      - 3-tier drag: Total Drag = Operating Drag (W_gross - W_D) + Distribution Drag (W_D - W_R)

    Returns:
      Dict with 'horizons', 'alpha_isolation', and optional 'scheme_metadata'.
    """
    if horizons is None:
        horizons = ["1Y", "3Y", "5Y", "10Y"]

    scheme_metadata: Dict[str, Any] = {}
    horizon_cagrs_d: Dict[str, float] = {}
    horizon_cagrs_r: Dict[str, float] = {}
    row_d = None
    row_r = None
    hist_d: pd.DataFrame = pd.DataFrame()
    hist_r: pd.DataFrame = pd.DataFrame()

    # If scheme codes are provided, fetch empirical metrics from DB
    if direct_scheme_code is not None or regular_scheme_code is not None:
        close_con = False
        con = conn
        if con is None:
            con = get_connection()
            close_con = True
        try:
            codes = [c for c in (direct_scheme_code, regular_scheme_code) if c is not None]
            sql = f"""
                SELECT scheme_code, scheme_name, fund_house, category, plan_type, option_type,
                       expense_ratio, ter_base_expense_ratio, ter_status, ter_as_of_date, latest_nav,
                       return_1y_pct, return_3y_pct, return_5y_pct, return_10y_pct
                FROM summary_table
                WHERE scheme_code IN ({','.join(str(c) for c in codes)});
            """
            df_schemes = fetchdf(con.execute(sql))
            s_map = {row["scheme_code"]: row for _, row in df_schemes.iterrows()}

            row_d = s_map.get(direct_scheme_code) if direct_scheme_code else None
            row_r = s_map.get(regular_scheme_code) if regular_scheme_code else None

            def _ter_decimal(row: Any) -> Optional[float]:
                if row is None:
                    return None
                exp = row.get("expense_ratio")
                if exp is None:
                    exp = row.get("ter_base_expense_ratio")
                if exp is None or (isinstance(exp, float) and math.isnan(exp)):
                    return None
                exp_f = float(exp)
                status = str(row.get("ter_status") or "")
                # Official + ter_history are percent. Decimal heuristic only for leftover non-official TERs.
                if status == "official":
                    return exp_f / 100.0
                return exp_f / 100.0 if exp_f > 0.05 else exp_f

            if ter_direct is None:
                ter_direct = _ter_decimal(row_d)
            if ter_regular is None:
                ter_regular = _ter_decimal(row_r)

            # Prepopulate multi-horizon CAGRs from summary_table if present
            for h in horizons:
                yrs = HORIZON_YEARS.get(h, 1.0)
                col_name = f"return_{h.lower()}_pct"
                if row_d is not None:
                    ret_d = row_d.get(col_name)
                    c_d = _cagr_from_period_return(ret_d, yrs)
                    if c_d is not None:
                        horizon_cagrs_d[h] = c_d
                if row_r is not None:
                    ret_r = row_r.get(col_name)
                    c_r = _cagr_from_period_return(ret_r, yrs)
                    if c_r is not None:
                        horizon_cagrs_r[h] = c_r

            scheme_metadata = {
                "direct_scheme": {
                    "scheme_code": direct_scheme_code,
                    "scheme_name": row_d.get("scheme_name") if row_d is not None else None,
                    "fund_house": row_d.get("fund_house") if row_d is not None else None,
                    "category": row_d.get("category") if row_d is not None else None,
                    "expense_ratio_pct": (ter_direct * 100.0) if ter_direct is not None else None,
                    "ter_status": (row_d.get("ter_status") if row_d is not None else None),
                    "ter_as_of_date": str(row_d.get("ter_as_of_date")) if row_d is not None and row_d.get("ter_as_of_date") is not None else None,
                },
                "regular_scheme": {
                    "scheme_code": regular_scheme_code,
                    "scheme_name": row_r.get("scheme_name") if row_r is not None else None,
                    "fund_house": row_r.get("fund_house") if row_r is not None else None,
                    "category": row_r.get("category") if row_r is not None else None,
                    "expense_ratio_pct": (ter_regular * 100.0) if ter_regular is not None else None,
                    "ter_status": (row_r.get("ter_status") if row_r is not None else None),
                    "ter_as_of_date": str(row_r.get("ter_as_of_date")) if row_r is not None and row_r.get("ter_as_of_date") is not None else None,
                },
            }
            hist_d, hist_r = _load_ter_histories(con, direct_scheme_code, regular_scheme_code)
        finally:
            if close_con:
                con.close()
    else:
        hist_d, hist_r = pd.DataFrame(), pd.DataFrame()

    lookup_mode = direct_scheme_code is not None or regular_scheme_code is not None
    paired = direct_scheme_code is not None and regular_scheme_code is not None
    if lookup_mode and (not paired or (direct_scheme_code is not None and row_d is None) or (regular_scheme_code is not None and row_r is None)):
        status = "unpaired"
    elif lookup_mode and (ter_direct is None or (paired and ter_regular is None)):
        status = "unavailable"
    else:
        status = "ok"

    # If base CAGRs provided as percentages (>1.0), convert to decimal fractions (e.g. 15.0 -> 0.15)
    # But note: boundary high return test uses 35% as 0.35. If someone passes 15.0, convert to 0.15.
    base_d_cagr = direct_cagr
    base_r_cagr = regular_cagr

    if base_d_cagr is not None and abs(base_d_cagr) > 1.0:
        base_d_cagr = base_d_cagr / 100.0
    if base_r_cagr is not None and abs(base_r_cagr) > 1.0:
        base_r_cagr = base_r_cagr / 100.0

    if base_d_cagr is None:
        base_d_cagr = horizon_cagrs_d.get("1Y")
    if base_r_cagr is None:
        base_r_cagr = horizon_cagrs_r.get("1Y")

    if lookup_mode and status == "ok" and (base_d_cagr is None or (paired and base_r_cagr is None)):
        status = "unavailable"

    ter_model = "constant"
    ter_banner = None
    if lookup_mode and ter_direct is not None:
        years_max = max(HORIZON_YEARS.get(h, 1.0) for h in horizons if h in HORIZON_YEARS) if horizons else 1.0
        tv_d = _time_varying_ter_decimal(hist_d, years_max)
        tv_r = _time_varying_ter_decimal(hist_r, years_max) if paired else None
        if tv_d is not None and (not paired or tv_r is not None):
            ter_direct = tv_d
            if tv_r is not None:
                ter_regular = tv_r
            ter_model = "time_varying_history"
        else:
            as_of = None
            if scheme_metadata:
                as_of = (scheme_metadata.get("direct_scheme") or {}).get("ter_as_of_date")
            ter_banner = f"constant-TER assumption from {as_of}" if as_of else "constant-TER assumption"

    if status in ("unavailable", "unpaired") and lookup_mode:
        result: Dict[str, Any] = {
            "status": status,
            "horizons": {},
            "alpha_isolation": None,
            "parameters": {
                "initial_capital": initial_capital,
                "ter_direct_pct": round(ter_direct * 100.0, 4) if ter_direct is not None else None,
                "ter_regular_pct": round(ter_regular * 100.0, 4) if ter_regular is not None else None,
                "ter_model": ter_model,
            },
            "empirical_cagrs": {"direct": horizon_cagrs_d, "regular": horizon_cagrs_r},
            "message": (
                "No Direct/Regular pair; pair drag not computed."
                if status == "unpaired"
                else "Missing official TER or empirical CAGR; fee-drag not invented."
            ),
        }
        if scheme_metadata:
            result["schemes"] = scheme_metadata
        if ter_banner:
            result["ter_banner"] = ter_banner
        return result

    if ter_direct is None or ter_regular is None or base_d_cagr is None or base_r_cagr is None:
        return {
            "status": "unavailable",
            "horizons": {},
            "alpha_isolation": None,
            "parameters": {"initial_capital": initial_capital},
            "message": "Missing TER or CAGR; fee-drag not invented.",
        }

    horizon_results: Dict[str, Dict[str, float]] = {}
    for h in horizons:
        if h in HORIZON_YEARS:
            years = HORIZON_YEARS[h]
        elif h.endswith("Y") and h[:-1].isdigit():
            years = float(h[:-1])
        else:
            years = 1.0
        # Use horizon-specific CAGR if available from DB, else use base CAGR
        cagr_d = horizon_cagrs_d.get(h, base_d_cagr)
        cagr_r = horizon_cagrs_r.get(h, base_r_cagr)

        # Compound wealth
        w_d = initial_capital * ((1.0 + cagr_d) ** years)
        w_r = initial_capital * ((1.0 + cagr_r) ** years)

        # Pre-expense gross wealth (reconstituted with direct TER)
        cagr_gross = cagr_d + ter_direct
        w_gross = initial_capital * ((1.0 + cagr_gross) ** years)

        # Wealth erosion & fee drag
        rupee_erosion = max(0.0, w_d - w_r) if initial_capital > 0 else 0.0
        cum_drag_pct = ((w_d - w_r) / w_d * 100.0) if w_d > 0 else 0.0

        # When identical CAGR and TER, clamp exactly to 0.0
        if math.isclose(cagr_d, cagr_r, abs_tol=1e-9) and math.isclose(ter_direct, ter_regular, abs_tol=1e-9):
            cum_drag_pct = 0.0
            rupee_erosion = 0.0

        cagr_spread_pct = round((cagr_d - cagr_r) * 100.0, 4)

        # 3-tier dollar decomposition
        operating_drag_wealth = max(0.0, w_gross - w_d) if initial_capital > 0 else 0.0
        distribution_drag_wealth = max(0.0, w_d - w_r) if initial_capital > 0 else 0.0

        horizon_results[h] = {
            "wealth_direct": round(w_d, 2),
            "wealth_regular": round(w_r, 2),
            "wealth_gross": round(w_gross, 2),
            "rupee_wealth_erosion": round(rupee_erosion, 2),
            "cumulative_drag_pct": round(cum_drag_pct, 2),
            "cagr_spread_pct": cagr_spread_pct,
            "cagr_direct_pct": round(cagr_d * 100.0, 4),
            "cagr_regular_pct": round(cagr_r * 100.0, 4),
            "operating_drag_wealth": round(operating_drag_wealth, 2),
            "distribution_drag_wealth": round(distribution_drag_wealth, 2),
        }

    # 3-Tier Alpha & Fee Attribution
    ter_diff = (ter_regular - ter_direct) * 100.0
    gross_alpha = direct_alpha + (ter_direct * 100.0)
    net_alpha_d = direct_alpha
    # If regular alpha is not passed, default to gross_alpha - ter_regular
    net_alpha_r = regular_alpha if regular_alpha != 0.0 or direct_alpha == 0.0 else (gross_alpha - (ter_regular * 100.0))

    alpha_isolation = {
        "gross_alpha_pct": round(gross_alpha, 4),
        "net_alpha_direct_pct": round(net_alpha_d, 4),
        "net_alpha_regular_pct": round(net_alpha_r, 4),
        "ter_differential_pct": round(ter_diff, 4),
        "operating_drag_pct": round(ter_direct * 100.0, 4),
        "distribution_drag_pct": round(ter_diff, 4),
    }

    result: Dict[str, Any] = {
        "status": "ok",
        "horizons": horizon_results,
        "alpha_isolation": alpha_isolation,
        "parameters": {
            "initial_capital": initial_capital,
            "ter_direct_pct": round(ter_direct * 100.0, 4),
            "ter_regular_pct": round(ter_regular * 100.0, 4),
            "ter_model": ter_model,
        },
    }
    if scheme_metadata:
        result["schemes"] = scheme_metadata
    if ter_banner:
        result["ter_banner"] = ter_banner

    return result
