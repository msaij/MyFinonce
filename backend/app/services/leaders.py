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


#: A category needs this many ranked funds before it can be named the "leading category":
#: a four-fund legacy label topping the market on one lucky fund is not a market signal.
MIN_FUNDS_FOR_LEADING_CATEGORY = 5


def quartile_badge(q: Any) -> str:
    if q is None or (isinstance(q, float) and q != q):
        # Too few peers to rank against (see db.queries.MIN_PEERS_FOR_ALPHA). Calling that
        # "Q4" -- what this used to fall through to -- branded a fund bottom-quartile for
        # having no competitors.
        return "Unranked (under 5 peers)"
    if q == 1:
        return "Q1 (Top 25%)"
    elif q == 2:
        return "Q2 (25-50%)"
    elif q == 3:
        return "Q3 (50-75%)"
    return "Q4 (Bottom 25%)"


QUADRANT_AHEAD_CALMER = "Ahead of peers, calmer"
QUADRANT_AHEAD_BUMPIER = "Ahead of peers, bumpier"
QUADRANT_BEHIND_CALMER = "Behind peers, calmer"
QUADRANT_BEHIND_BUMPIER = "Behind peers, bumpier"


def classify_quadrant(cat_alpha_pct: float, vol_vs_peers_pct: float) -> str:
    """Where a fund sits against its OWN peers on both axes: return (above or below the peer
    median) and volatility (below or above the peers' median volatility).

    The old version measured every fund against one market-wide median return and
    volatility, which only sorted funds by asset class: 241 of 242 liquid funds were
    "Institutional Alpha Stars" for being calm, and 902 equity funds "Value Traps" for being
    equity. Against its peers, a quadrant says something about the fund itself."""
    ahead = cat_alpha_pct >= 0
    calmer = vol_vs_peers_pct <= 0
    if ahead:
        return QUADRANT_AHEAD_CALMER if calmer else QUADRANT_AHEAD_BUMPIER
    return QUADRANT_BEHIND_CALMER if calmer else QUADRANT_BEHIND_BUMPIER


LAGGARD_BOTTOM_DECILE = "Bottom 10% of its peers"
LAGGARD_BOTTOM_QUARTILE = "Bottom quarter of its peers"
LAGGARD_WITH_CATEGORY = "Falling with its category"
LAGGARD_SLIGHTLY_BEHIND = "Slightly behind its peers"
NOT_A_LAGGARD = "Not a laggard"


def diagnose_laggard(cat_median_return: Optional[float], cat_alpha_pct: Optional[float],
                     peer_percentile: Optional[float]) -> str:
    """Why a fund is behind, judged by where it ranks among its peers.

    The old rules used fixed percentage-point cut-offs (-1.5 pp, -3 pp, -15% from the high)
    whatever the window or asset class: -3 pp is a catastrophe for a liquid fund over a
    month and ordinary noise for a small-cap fund over three years. They also sent funds
    AHEAD of their peers to "Mild Underperformer". Rank within the peer group is scale-free,
    so one rule reads the same for every category and window."""
    def missing(v: Any) -> bool:
        return v is None or (isinstance(v, float) and v != v)

    if missing(cat_alpha_pct) or cat_alpha_pct >= 0:
        return NOT_A_LAGGARD
    if not missing(peer_percentile) and peer_percentile <= 10.0:
        return LAGGARD_BOTTOM_DECILE
    if not missing(peer_percentile) and peer_percentile < 25.0:
        return LAGGARD_BOTTOM_QUARTILE
    if not missing(cat_median_return) and cat_median_return < 0:
        # Behind, but not far behind, in a category that fell as a whole: the category is
        # most of the story.
        return LAGGARD_WITH_CATEGORY
    return LAGGARD_SLIGHTLY_BEHIND


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
            "lagging_category": None,
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

    has_alpha = df["cat_alpha_pct"].notna() if "cat_alpha_pct" in df else pd.Series(False, index=df.index)
    top_alpha_row = df[has_alpha].sort_values("cat_alpha_pct", ascending=False).iloc[0] if has_alpha.any() else None
    # Grouped as the peers are (asset class + SEBI category), and only categories with
    # enough funds to mean something.
    cat_key = ["asset_class", "peer_category"] if {"asset_class", "peer_category"} <= set(df.columns) else ["category"]
    # Counted in distinct FUNDS: a fund's Direct and Regular plans are one fund twice, and
    # four funds shown as eight schemes once crowned a category on four funds.
    fund_col = "scheme_name" if "scheme_name" in df.columns else "period_return_pct"
    cats = df.groupby(cat_key).agg(median=("period_return_pct", "median"), count=(fund_col, "nunique"))
    cats = cats[cats["count"] >= MIN_FUNDS_FOR_LEADING_CATEGORY].sort_values("median", ascending=False)

    def _cat_name(key: Any) -> str:
        if isinstance(key, tuple):
            asset_class, category = key
            return f"{category} ({asset_class})"
        return str(key)

    leading_category = ({"name": _cat_name(cats.index[0]), "return_pct": float(cats["median"].iloc[0]),
                         "funds": int(cats["count"].iloc[0])} if not cats.empty else None)
    lagging_category = ({"name": _cat_name(cats.index[-1]), "return_pct": float(cats["median"].iloc[-1]),
                         "funds": int(cats["count"].iloc[-1])} if len(cats) > 1 else None)

    # Quadrant classification: only funds with a real, measurable volatility figure
    # (never a fabricated one) -- matches the original's df_quad filter exactly.
    # Only funds with a real peer-relative position on both axes: 5+ peers (so there is a
    # peer median) and a measured volatility. Never a fabricated figure.
    if {"cat_alpha_pct", "vol_vs_peers_pct"} <= set(df.columns):
        df_quad = df[df["cat_alpha_pct"].notna() & df["vol_vs_peers_pct"].notna()]
    else:
        df_quad = df.iloc[0:0]
    quadrant_excluded = len(df) - len(df_quad)
    med_vol: Optional[float] = None
    med_ret: Optional[float] = None
    # Built as a whole column: under pandas 3, assigning strings to a NEW column for only some
    # rows (df.loc[idx, "quadrant"] = [...]) fills the other rows with the string "nan", which
    # the page then counted as a fifth quadrant.
    quadrants = pd.Series([None] * len(df), index=df.index, dtype=object)
    if not df_quad.empty:
        med_vol = float(df_quad["annualized_vol_pct"].median())
        med_ret = float(df_quad["period_return_pct"].median())
        quadrants.loc[df_quad.index] = [
            classify_quadrant(a, v) for a, v in zip(df_quad["cat_alpha_pct"], df_quad["vol_vs_peers_pct"])
        ]
    df["quadrant"] = quadrants

    # Laggard diagnostics, computed for every row (cheap, scalar-only) -- the
    # frontend decides which subset to actually display (negative alpha or deep drawdown).
    df["diagnostic_classification"] = [
        diagnose_laggard(m, a, p) for m, a, p in zip(
            df["cat_median_return"], df["cat_alpha_pct"],
            df["peer_percentile"] if "peer_percentile" in df else [None] * len(df))
    ]

    return {
        "rows": df,
        "excluded_thin_data": excluded_thin_data,
        "total_funds": total_funds,
        "advancers": advancers,
        "decliners": decliners,
        "market_median_return": market_median_return,
        "top_alpha": ({"name": top_alpha_row["display_name"], "return_pct": float(top_alpha_row["cat_alpha_pct"]),
                       "category": top_alpha_row.get("peer_category")}
                      if top_alpha_row is not None else None),
        "leading_category": leading_category,
        "lagging_category": lagging_category,
        "med_vol": med_vol,
        "med_ret": med_ret,
        "quadrant_excluded": quadrant_excluded,
    }
