"""Leaders & Laggards page's classification logic, moved server-side --
ported from fetcher/pages/3_Leaders_&_Laggards.py's module-level helper
functions (quartile_badge/classify_quadrant/diagnose_laggard) and the
MIN_TRADING_DAYS_FOR_RANKING guard, which lived inline in the page before.
They move here (not into db/queries.py) because they're pure
classification/presentation logic over an already-computed DataFrame, not
SQL -- and because the frontend needs the *result* as queryable/sortable
data fields (Quartile Rank, Quadrant, Diagnostic Classification), not a
client-side recomputation.
"""

import re
from typing import Any, Dict, Optional, Tuple

import pandas as pd

# A fund needs at least this many trading days of NAV history *inside the active
# window* before it's eligible for volatility-based ranking/classification --
# otherwise a fund with 1-2 NAV prints can win flagship KPIs or get badged
# "low risk" purely from a lack of data.
MIN_TRADING_DAYS_FOR_RANKING = 5


def quartile_badge(q: Any) -> str:
    if q == 1:
        return "Q1 (Top 25%)"
    elif q == 2:
        return "Q2 (25-50%)"
    elif q == 3:
        return "Q3 (50-75%)"
    return "Q4 (Bottom 25%)"


def classify_quadrant(period_return_pct: float, annualized_vol_pct: float, med_ret: float, med_vol: float) -> str:
    if period_return_pct >= med_ret and annualized_vol_pct <= med_vol:
        return "Institutional Alpha Stars (High Return, Low Risk)"
    elif period_return_pct >= med_ret and annualized_vol_pct > med_vol:
        return "High-Beta Momentum (High Return, High Risk)"
    elif period_return_pct < med_ret and annualized_vol_pct <= med_vol:
        return "Defensive Anchors (Low Return, Low Risk)"
    else:
        return "Value Traps / Laggards (Low Return, High Risk)"


def diagnose_laggard(cat_median_return: float, cat_alpha_pct: float, dist_from_52w_high_pct: Optional[float]) -> str:
    dd = dist_from_52w_high_pct if dist_from_52w_high_pct is not None else 0.0
    if cat_median_return < 0 and cat_alpha_pct >= -1.5:
        return "Cyclical Dip (Category-wide correction; moving with peers)"
    elif cat_alpha_pct < -3.0:
        return "Structural Drag (Chronic underperformance vs peers)"
    elif dd < -15.0:
        return "Deep Drawdown (Far from 52W High)"
    else:
        return "Mild Underperformer"


def apply_min_trading_days_guard(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """Returns (filtered_df, excluded_count). Only filters when the dataset
    actually has volatility data (the date-windowed query path) -- on the
    no-date-range path annualized_vol_pct is uniformly NaN by design, and
    that's not "insufficient data," it's "not computed on this path."""
    if df.empty:
        return df, 0
    has_vol_data = df["annualized_vol_pct"].notna().any()
    if not has_vol_data:
        return df, 0
    thin_mask = df["n_trading_days"] < MIN_TRADING_DAYS_FOR_RANKING
    excluded = int(thin_mask.sum())
    if excluded == len(df):
        df = df.copy()
        df["annualized_vol_pct"] = None
        return df, 0
    if excluded > 0:
        df = df[~thin_mask].copy()
    return df, excluded


def apply_keyword_filter(df: pd.DataFrame, search_query: str) -> pd.DataFrame:
    if df.empty or not search_query or not search_query.strip():
        return df
    tokens = [t for t in re.findall(r"[a-zA-Z0-9]+", search_query.lower()) if t]
    if not tokens:
        return df
    searchable = (
        df["display_name"].fillna("").astype(str).str.lower()
        + " "
        + df["scheme_name"].fillna("").astype(str).str.lower()
        + " "
        + df["scheme_code"].fillna("").astype(str)
        + " "
        + df["fund_house"].fillna("").astype(str).str.lower()
        + " "
        + df["category"].fillna("").astype(str).str.lower()
    )
    mask = pd.Series(True, index=df.index)
    for token in tokens:
        mask &= searchable.str.contains(token, regex=False)
    return df[mask].copy()


def build_leaders_dataset(df_all: pd.DataFrame, search_query: str = "") -> Dict[str, Any]:
    """Composes the full pipeline the page needs: keyword filter -> min-
    trading-days guard -> Quartile Rank column -> quadrant classification
    (only on rows with a real, measurable volatility figure) -> KPI
    summary numbers. Returns everything the router needs in one call so it
    doesn't have to re-derive medians/counts itself."""
    df = apply_keyword_filter(df_all, search_query)
    df, excluded_thin_data = apply_min_trading_days_guard(df)

    if df.empty:
        return {
            "rows": df,
            "excluded_thin_data": excluded_thin_data,
            "total_funds": 0,
            "advancers": 0,
            "decliners": 0,
            "market_median_return": None,
            "top_alpha": None,
            "leading_category": None,
            "med_vol": None,
            "med_ret": None,
            "quadrant_excluded": 0,
        }

    df = df.copy()
    df["quartile_rank"] = df["quartile"].apply(quartile_badge)

    total_funds = len(df)
    advancers = int((df["period_return_pct"] > 0).sum())
    decliners = int((df["period_return_pct"] < 0).sum())
    market_median_return = float(df["period_return_pct"].median())

    top_alpha_row = df.sort_values("cat_alpha_pct", ascending=False).iloc[0]
    cat_medians = df.groupby("category")["period_return_pct"].median().sort_values(ascending=False)
    leading_category = (
        {"name": cat_medians.index[0], "return_pct": float(cat_medians.iloc[0])} if not cat_medians.empty else None
    )

    # Quadrant classification: only funds with a real, measurable volatility figure
    # (never a fabricated one) -- matches the original's df_quad filter exactly.
    df_quad = df[df["annualized_vol_pct"].notna() & (df["annualized_vol_pct"] > 0.1)]
    quadrant_excluded = len(df) - len(df_quad)
    med_vol: Optional[float] = None
    med_ret: Optional[float] = None
    if not df_quad.empty:
        med_vol = float(df_quad["annualized_vol_pct"].median())
        med_ret = float(df_quad["period_return_pct"].median())
        df.loc[df_quad.index, "quadrant"] = df_quad.apply(
            lambda r: classify_quadrant(r["period_return_pct"], r["annualized_vol_pct"], med_ret, med_vol), axis=1
        )

    # Laggard diagnostics, computed for every row (cheap, scalar-only) -- the
    # frontend decides which subset to actually display (negative alpha or deep drawdown).
    df["diagnostic_classification"] = df.apply(
        lambda r: diagnose_laggard(r["cat_median_return"], r["cat_alpha_pct"], r.get("dist_from_52w_high_pct")), axis=1
    )

    return {
        "rows": df,
        "excluded_thin_data": excluded_thin_data,
        "total_funds": total_funds,
        "advancers": advancers,
        "decliners": decliners,
        "market_median_return": market_median_return,
        "top_alpha": {"name": top_alpha_row["display_name"], "return_pct": float(top_alpha_row["cat_alpha_pct"])},
        "leading_category": leading_category,
        "med_vol": med_vol,
        "med_ret": med_ret,
        "quadrant_excluded": quadrant_excluded,
    }
