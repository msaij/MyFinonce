"""Risk-profiled, budget-aware model-portfolio construction.

Educational, rules-based tooling — never personalized investment advice. Every allocation
and every fund pick is the output of a disclosed, inspectable rule or formula applied to
official AMFI historical data; nothing here is a black box and nothing is fabricated.

Two independent construction methods are offered side by side, deliberately not collapsed
into one "the algorithm says so" answer:

- Rules-based: a strategic asset-allocation table (by risk tier) across sleeves that map
  onto real AMFI categories, then a transparent weighted quality score picks the best
  candidate within each sleeve.
- Mean-variance optimization: scipy solves for the max-Sharpe long-only weighting across
  the *same* rules-based candidate shortlist (never the raw multi-thousand-scheme universe —
  screening first is the standard mitigation for MVO's well-known sensitivity to noisy
  historical inputs), anchored near the rules-based sleeve weights so it can't drift into an
  unintuitive corner solution.

Both methods hand their resulting weights straight to portfolio_sim.run_backtest — the same
engine already live on Compare & Simulate's Portfolio Backtest tab — for historical
backtesting. No new backtest math is introduced here.
"""

import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.db import queries as db
from app.db.connection import fetchdf
from app import quant_analytics
from app import portfolio_sim

# ============================================================
# 1. RISK TIERS & STRATEGIC ASSET ALLOCATION
# ============================================================

RISK_TIERS = ["Conservative", "Moderate", "Balanced", "Growth", "Aggressive"]

SLEEVE_NAMES = ["Equity Core", "Equity Satellite", "International Equity", "Debt", "Gold", "Liquid Buffer"]

# Grounded in standard institutional strategic-allocation bands (e.g. a Conservative->
# Aggressive equity glide from ~15% to ~80-95% total equity), adapted to Indian mutual-fund
# categories. Deliberate choice: the Debt sleeve's own credit quality stays conservative at
# every tier (see SLEEVE_CATEGORY_FILTERS) — the Equity weight and Equity-Satellite mix are
# the risk dial, not debt credit quality, since a "safety sleeve" that takes on credit risk
# defeats its own purpose.
SLEEVE_ALLOCATIONS: Dict[str, Dict[str, float]] = {
    "Conservative": {"Equity Core": 0.15, "Equity Satellite": 0.00, "International Equity": 0.00, "Debt": 0.60, "Gold": 0.10, "Liquid Buffer": 0.15},
    "Moderate":     {"Equity Core": 0.25, "Equity Satellite": 0.10, "International Equity": 0.05, "Debt": 0.45, "Gold": 0.10, "Liquid Buffer": 0.05},
    "Balanced":     {"Equity Core": 0.35, "Equity Satellite": 0.15, "International Equity": 0.05, "Debt": 0.30, "Gold": 0.10, "Liquid Buffer": 0.05},
    "Growth":       {"Equity Core": 0.45, "Equity Satellite": 0.20, "International Equity": 0.10, "Debt": 0.15, "Gold": 0.07, "Liquid Buffer": 0.03},
    "Aggressive":   {"Equity Core": 0.45, "Equity Satellite": 0.35, "International Equity": 0.15, "Debt": 0.00, "Gold": 0.05, "Liquid Buffer": 0.00},
}
for _tier_name, _weights in SLEEVE_ALLOCATIONS.items():
    assert abs(sum(_weights.values()) - 1.0) < 1e-9, f"{_tier_name} sleeve weights don't sum to 100%"

# Annualized-volatility ceiling per tier — used only as a mean-variance optimizer constraint,
# not part of the rules-based sleeve table itself.
VOL_CEILING_BY_TIER: Dict[str, float] = {
    "Conservative": 7.0, "Moderate": 11.0, "Balanced": 15.0, "Growth": 19.0, "Aggressive": 26.0,
}

# Category keyword lists verified live against summary_table (2026-09-10). Debt spans both
# modern ("Debt Scheme - X") and pre-2018-recategorization ("Income/Debt Oriented Schemes -
# X") AMFI naming — a large share of currently-active debt schemes still carry the legacy
# label (see db.py's broad_category fix, same date) — and intentionally excludes Credit Risk,
# Gilt, Dynamic/Medium/Long-duration categories at every tier: this sleeve is the safety
# buffer, so it never takes on credit or long-duration rate risk regardless of the user's
# overall risk tier. Liquid Fund/Overnight Fund are deliberately reserved for the separate
# Liquid Buffer sleeve below, not repeated here — every sleeve's category_keywords must stay
# mutually exclusive, since the same scheme_code appearing as a candidate in two sleeves at
# once breaks the wide-format NAV pivot both builders rely on (get_sleeve_candidates() also
# asserts this at import time).
_DEBT_SAFE_KEYWORDS = [
    "Money Market Fund", "Ultra Short", "Low Duration Fund",
    "Short Duration Fund", "Short Term Fund", "Corporate Bond Fund", "Banking and PSU",
    "Floating Interest Rates Fund",
]

SLEEVE_CATEGORY_FILTERS: Dict[str, Dict[str, Any]] = {
    "Equity Core": {
        "category_keywords": [
            "Large Cap Fund", "Flexi Cap Fund", "Large & Mid Cap Fund", "Multi Cap Fund",
            "Value Fund", "Contra Fund", "Dividend Yield Fund", "ELSS",
        ],
        "relax_plan_filter": False,
    },
    "Equity Satellite": {
        "category_keywords": ["Mid Cap Fund", "Small Cap Fund", "Sectoral/ Thematic", "Focused Fund"],
        "relax_plan_filter": False,
    },
    "International Equity": {
        "category_keywords": ["FoF Overseas"],
        "relax_plan_filter": False,
    },
    "Debt": {
        "category_keywords": _DEBT_SAFE_KEYWORDS,
        "relax_plan_filter": False,
    },
    "Gold": {
        # ETFs/gold-FoF-wrapper products don't carry a meaningful Direct/Regular distinction
        # (no distributor commission concept applies), so the plan-type filter is relaxed.
        "category_keywords": ["Gold ETF", "FoF Domestic"],
        "name_keyword": "Gold",
        "relax_plan_filter": True,
    },
    "Liquid Buffer": {
        "category_keywords": ["Liquid Fund", "Overnight Fund"],
        "relax_plan_filter": False,
    },
}

# Defense in depth: a scheme_code selected into two sleeves at once breaks the wide-format
# NAV pivot both builders rely on downstream. This catches an accidental future keyword
# overlap at import time instead of a live ValueError deep inside the optimizer.
for _s1, _spec1 in SLEEVE_CATEGORY_FILTERS.items():
    for _s2, _spec2 in SLEEVE_CATEGORY_FILTERS.items():
        if _s1 >= _s2:
            continue
        _shared = set(_spec1["category_keywords"]) & set(_spec2["category_keywords"])
        assert not _shared, f"Sleeve category keywords overlap between {_s1!r} and {_s2!r}: {_shared}"

MIN_SLEEVE_AMOUNT = 500.0  # near-universal SIP/lump-sum minimum floor across Indian AMCs
FOLD_MAP = {"Liquid Buffer": "Debt", "Gold": "Debt", "International Equity": "Equity Core"}

# Transparent, disclosed composite-score weights — shown to the user verbatim in the UI's
# methodology expander so every pick is explainable, never a black box.
SCORE_WEIGHTS = {"sharpe": 0.30, "sortino": 0.25, "alpha_vs_sleeve_median": 0.25, "max_drawdown": 0.10, "expense_ratio": 0.10}


def _sleeve_where_clause(sleeve: str) -> Tuple[str, List[Any]]:
    spec = SLEEVE_CATEGORY_FILTERS[sleeve]
    keywords = spec["category_keywords"]
    # LIKE, not ILIKE (DuckDB-only) -- SQLite's LIKE is already ASCII case-insensitive by
    # default, the same effect for this English-text-only data.
    cat_conditions = " OR ".join(["s.category LIKE ?"] * len(keywords))
    params: List[Any] = [f"%{kw}%" for kw in keywords]
    where = f"({cat_conditions})"
    if spec.get("name_keyword"):
        where += " AND s.scheme_name LIKE ?"
        params.append(f"%{spec['name_keyword']}%")
    return where, params


def get_sleeve_candidates(
    sleeve: str,
    active_start: datetime.date,
    active_end: datetime.date,
    min_track_record_years: float = 3.0,
    prefilter_top_n: int = 20,
) -> pd.DataFrame:
    """Cheap SQL-only pre-filter: real, is_active (never a dead/delisted scheme), sufficiently
    track-recorded schemes in this sleeve, ranked by a rough return/vol signal down to a
    manageable shortlist. score_candidates() then computes true Sharpe/Sortino/MaxDD on just
    this shortlist — a two-stage design so a 1,000+-scheme sleeve (e.g. Sectoral/Thematic)
    never triggers per-scheme metric computation on its full candidate pool.

    Track record is checked against each scheme's *overall* history (its first-ever NAV date,
    independent of the active window) — not how many data points happen to fall inside
    active_start..active_end. The active window can legitimately be short (e.g. "Past 90
    Days", the app-wide default) without that ever being confused for "this fund is too new
    to trust"; those are two different questions."""
    if sleeve not in SLEEVE_CATEGORY_FILTERS:
        return pd.DataFrame()
    spec = SLEEVE_CATEGORY_FILTERS[sleeve]
    where_cat, cat_params = _sleeve_where_clause(sleeve)
    # Growth option is required for every sleeve, never relaxed: an IDCW-option scheme's NAV
    # drops on every distribution payout (that value left the fund as cash to the investor),
    # so a plain NAV-ratio "return" systematically understates it -- this app has no
    # distribution-amount data source to adjust for that, so IDCW candidates are excluded
    # outright rather than silently mis-scored. Only the Direct/Regular plan_type requirement
    # is relaxed for ETF-like sleeves (Gold), which don't carry a meaningful commission split.
    plan_clause = "AND s.option_type = 'Growth'"
    if not spec.get("relax_plan_filter"):
        plan_clause += " AND s.plan_type = 'Direct'"

    track_record_cutoff = active_end - datetime.timedelta(days=int(min_track_record_years * 365.25))
    # A within-window data-sufficiency floor that scales with the window itself (~5 trading
    # days/week), so a short active window doesn't require an impossible number of points,
    # while a degenerate 1-2-point window still gets excluded from scoring.
    window_days = max(1, (active_end - active_start).days)
    min_window_points = max(5, int(window_days * 5.0 / 7.0 * 0.5))

    con = db.get_connection()
    sql = f"""
        WITH p_start AS (
            SELECT scheme_code, nav AS start_nav
            FROM (SELECT scheme_code, nav, ROW_NUMBER() OVER (PARTITION BY scheme_code ORDER BY nav_date ASC) rn
                  FROM nav_history WHERE nav_date >= ? AND nav_date <= ?) WHERE rn = 1
        ),
        p_end AS (
            SELECT scheme_code, nav AS end_nav
            FROM (SELECT scheme_code, nav, ROW_NUMBER() OVER (PARTITION BY scheme_code ORDER BY nav_date DESC) rn
                  FROM nav_history WHERE nav_date >= ? AND nav_date <= ?) WHERE rn = 1
        ),
        vol_calc AS (
            SELECT scheme_code,
                   ROUND(STDDEV(ret) * SQRT(252.0) * 100.0, 4) AS annualized_vol_pct,
                   COUNT(ret) AS n_trading_days
            FROM (
                SELECT scheme_code,
                       (nav - LAG(nav) OVER (PARTITION BY scheme_code ORDER BY nav_date)) /
                       NULLIF(LAG(nav) OVER (PARTITION BY scheme_code ORDER BY nav_date), 0) AS ret
                FROM nav_history WHERE nav_date >= ? AND nav_date <= ?
            ) WHERE ret IS NOT NULL
            GROUP BY scheme_code
        ),
        inception AS (
            SELECT scheme_code, MIN(nav_date) AS first_nav_date
            FROM nav_history
            GROUP BY scheme_code
        )
        SELECT s.scheme_code, s.scheme_name, s.fund_house, s.category, s.plan_type, s.option_type,
               s.expense_ratio, s.ter_status,
               ROUND((pe.end_nav - ps.start_nav) / NULLIF(ps.start_nav, 0) * 100.0, 4) AS period_return_pct,
               v.annualized_vol_pct, COALESCE(v.n_trading_days, 0) AS n_trading_days
        FROM summary_table s
        JOIN p_start ps ON s.scheme_code = ps.scheme_code
        JOIN p_end pe ON s.scheme_code = pe.scheme_code
        JOIN inception i ON s.scheme_code = i.scheme_code
        LEFT JOIN vol_calc v ON s.scheme_code = v.scheme_code
        WHERE s.is_active = 1
          AND i.first_nav_date <= ?
          {plan_clause}
          AND {where_cat};
    """
    all_params = [active_start, active_end, active_start, active_end, active_start, active_end, track_record_cutoff] + cat_params
    try:
        df = fetchdf(con.execute(sql, all_params))
    finally:
        con.close()

    if df.empty:
        return df
    df = df[df["n_trading_days"] >= min_window_points].copy()
    if df.empty:
        return df

    # Rough pre-rank only (return per unit of vol) — stage 2 computes true Sharpe/Sortino.
    df["_prefilter_score"] = df["period_return_pct"] / df["annualized_vol_pct"].replace(0, np.nan)
    df["_prefilter_score"] = df["_prefilter_score"].fillna(df["period_return_pct"])
    return df.sort_values("_prefilter_score", ascending=False).head(prefilter_top_n).reset_index(drop=True)


def score_candidates(
    df_shortlist: pd.DataFrame,
    active_start: datetime.date,
    active_end: datetime.date,
    risk_free_rate_ann: float = 0.065,
) -> pd.DataFrame:
    """Computes true Sharpe/Sortino/MaxDD/CAGR per shortlisted candidate via the exact same
    quant_analytics formulas already used (and tested) on the Quant Analysis page, and a
    transparent composite quality_score (weights: SCORE_WEIGHTS, shown to the user)."""
    if df_shortlist.empty:
        return df_shortlist

    codes = df_shortlist["scheme_code"].astype(int).tolist()
    df_hist_all = db.get_nav_history_dataframe(codes, start_date=active_start, end_date=active_end)
    if df_hist_all.empty:
        return pd.DataFrame()

    sleeve_median_return = df_shortlist["period_return_pct"].median()
    rows: List[Dict[str, Any]] = []
    for _, cand in df_shortlist.iterrows():
        code = int(cand["scheme_code"])
        df_raw = df_hist_all[df_hist_all["scheme_code"] == code][["nav_date", "nav"]]
        if df_raw.empty or len(df_raw) < 10:
            continue
        df_window, coverage = quant_analytics.prepare_fund_timeseries(
            df_raw, active_start, active_end, risk_free_rate_ann=risk_free_rate_ann
        )
        if not coverage.get("has_data") or len(df_window) < 10:
            continue
        metrics = quant_analytics.compute_risk_adjusted_metrics(df_window, risk_free_rate_ann=risk_free_rate_ann)
        if not metrics:
            continue
        row = cand.to_dict()
        row["cagr_pct"] = metrics["cagr_pct"]
        row["sharpe_ratio"] = metrics["sharpe_ratio"]
        row["sortino_ratio"] = metrics["sortino_ratio"] if isinstance(metrics["sortino_ratio"], (int, float)) else 0.0
        row["max_drawdown_pct"] = metrics["max_drawdown_pct"]
        row["vol_annualized_pct"] = metrics["vol_annualized_pct"]
        row["alpha_vs_sleeve_median_pct"] = round(float(cand["period_return_pct"]) - float(sleeve_median_return), 4)
        rows.append(row)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    def _z(series: pd.Series) -> pd.Series:
        std = series.std(ddof=0)
        if not std or pd.isna(std) or std < 1e-9:
            return pd.Series(0.0, index=series.index)
        return (series - series.mean()) / std

    ter_filled = df["expense_ratio"].astype(float)
    ter_filled = ter_filled.fillna(ter_filled.median() if ter_filled.notna().any() else 0.0)

    df["quality_score"] = (
        SCORE_WEIGHTS["sharpe"] * _z(df["sharpe_ratio"])
        + SCORE_WEIGHTS["sortino"] * _z(df["sortino_ratio"])
        + SCORE_WEIGHTS["alpha_vs_sleeve_median"] * _z(df["alpha_vs_sleeve_median_pct"])
        - SCORE_WEIGHTS["max_drawdown"] * _z(df["max_drawdown_pct"].abs())
        - SCORE_WEIGHTS["expense_ratio"] * _z(ter_filled)
    )
    return df.sort_values("quality_score", ascending=False).reset_index(drop=True)


# ============================================================
# 2. RULES-BASED PORTFOLIO BUILDER
# ============================================================

def build_rules_based_portfolio(
    risk_tier: str,
    active_start: datetime.date,
    active_end: datetime.date,
    budget: float,
    risk_free_rate_ann: float = 0.065,
) -> Dict[str, Any]:
    if risk_tier not in SLEEVE_ALLOCATIONS:
        return {"error": f"Unknown risk tier: {risk_tier}"}
    if budget <= 0:
        return {"error": "Budget must be a positive amount."}

    target_alloc = dict(SLEEVE_ALLOCATIONS[risk_tier])
    folded_notes: List[str] = []
    for sleeve, target in list(target_alloc.items()):
        if target > 0 and target * budget < MIN_SLEEVE_AMOUNT and sleeve in FOLD_MAP:
            dest = FOLD_MAP[sleeve]
            target_alloc[dest] = target_alloc.get(dest, 0.0) + target
            folded_notes.append(f"{sleeve} folded into {dest} — budget too small to split further.")
            target_alloc[sleeve] = 0.0

    shortlists: Dict[str, pd.DataFrame] = {}
    picks: List[Dict[str, Any]] = []
    used_amcs: set = set()
    used_codes: set = set()
    weights: Dict[int, float] = {}

    for sleeve, target in target_alloc.items():
        if target <= 0:
            continue
        candidates = get_sleeve_candidates(sleeve, active_start, active_end)
        if candidates.empty:
            picks.append({"sleeve": sleeve, "error": "No qualifying active funds found for this sleeve in the selected window."})
            continue
        scored = score_candidates(candidates, active_start, active_end, risk_free_rate_ann)
        shortlists[sleeve] = scored
        if scored.empty:
            picks.append({"sleeve": sleeve, "error": "No candidates had enough history to score in this window."})
            continue

        # Defense in depth: sleeves are designed with mutually exclusive category keywords
        # (asserted at import time), but a scheme_code already used by an earlier sleeve is
        # still skipped here -- the same fund weighted into two sleeves at once would corrupt
        # the weights dict (a dict keyed by scheme_code can't hold two different weights for
        # the same key) and break the downstream wide-format NAV pivot.
        chosen = None
        for _, row in scored.iterrows():
            if int(row["scheme_code"]) in used_codes:
                continue
            if row["fund_house"] not in used_amcs:
                chosen = row
                break
            if chosen is None:
                chosen = row
        if chosen is None:
            for _, row in scored.iterrows():
                if int(row["scheme_code"]) not in used_codes:
                    chosen = row
                    break
        if chosen is None:
            picks.append({"sleeve": sleeve, "error": "Every candidate for this sleeve was already used by another sleeve."})
            continue
        used_amcs.add(chosen["fund_house"])
        used_codes.add(int(chosen["scheme_code"]))

        code = int(chosen["scheme_code"])
        weights[code] = target
        picks.append({
            "sleeve": sleeve, "scheme_code": code, "scheme_name": chosen["scheme_name"],
            "fund_house": chosen["fund_house"], "category": chosen["category"],
            "weight_pct": round(target * 100.0, 2), "amount": round(target * budget, 2),
            "expense_ratio": chosen.get("expense_ratio"), "ter_status": chosen.get("ter_status"),
            "sharpe_ratio": chosen.get("sharpe_ratio"), "sortino_ratio": chosen.get("sortino_ratio"),
            "cagr_pct": chosen.get("cagr_pct"), "max_drawdown_pct": chosen.get("max_drawdown_pct"),
            "alpha_vs_sleeve_median_pct": chosen.get("alpha_vs_sleeve_median_pct"),
            "why": (
                f"Ranked #1 of {len(scored)} qualifying {sleeve} candidates on a disclosed composite of "
                f"Sharpe ({chosen.get('sharpe_ratio')}), Sortino ({chosen.get('sortino_ratio')}), return vs. "
                f"sleeve peers ({chosen.get('alpha_vs_sleeve_median_pct')}%), max drawdown "
                f"({chosen.get('max_drawdown_pct')}%), and TER ({chosen.get('expense_ratio')}%)."
            ),
        })

    total_w = sum(weights.values())
    if total_w <= 0:
        return {"error": "Could not build a portfolio: no sleeve had a qualifying candidate in this window."}
    weights = {c: w / total_w for c, w in weights.items()}

    return {
        "risk_tier": risk_tier, "budget": budget, "target_alloc": target_alloc,
        "weights": weights, "picks": picks, "folded_notes": folded_notes,
        "shortlists": shortlists, "method": "rules_based",
    }


# ============================================================
# 3. MEAN-VARIANCE OPTIMIZED BUILDER
# ============================================================

def build_mvo_portfolio(
    risk_tier: str,
    active_start: datetime.date,
    active_end: datetime.date,
    budget: float,
    rules_based_result: Dict[str, Any],
    risk_free_rate_ann: float = 0.065,
    max_single_weight: float = 0.40,
    sleeve_floor_tolerance: float = 0.15,
    top_n_per_sleeve: int = 3,
) -> Dict[str, Any]:
    """Maximizes the historical Sharpe ratio (long-only) over the top-N scored candidates per
    sleeve from build_rules_based_portfolio's own shortlists — never the raw universe.
    Historical CAGR stands in for "expected return" (a well-known simplification, disclosed
    in the UI, not hidden) and each candidate's weight is bounded near its sleeve's
    rules-based target (+/- sleeve_floor_tolerance) so the optimizer can meaningfully re-tilt
    within and across sleeves without a noisy historical window pushing it into an
    unintuitive, hard-to-explain corner solution."""
    import scipy.optimize as sco

    if "error" in rules_based_result:
        return {"error": "Cannot run the optimizer: the rules-based screen didn't produce a usable candidate set."}

    shortlists = rules_based_result.get("shortlists", {})
    target_alloc = rules_based_result.get("target_alloc", {})

    universe_rows: List[Dict[str, Any]] = []
    bounds_lo: List[float] = []
    bounds_hi: List[float] = []
    codes: List[int] = []

    for sleeve, target in target_alloc.items():
        if target <= 0 or sleeve not in shortlists or shortlists[sleeve].empty:
            continue
        top_n = shortlists[sleeve].head(top_n_per_sleeve)
        lo = max(0.0, target - sleeve_floor_tolerance) / max(1, len(top_n))
        hi = min(max_single_weight, min(1.0, target + sleeve_floor_tolerance))
        for _, row in top_n.iterrows():
            code = int(row["scheme_code"])
            # Defense in depth (see the sleeve-keyword-overlap assert near
            # SLEEVE_CATEGORY_FILTERS): a scheme_code appearing in two sleeves' shortlists
            # would create a duplicate column in the wide-format NAV pivot below and corrupt
            # the optimizer's covariance matrix -- skip a repeat rather than let that happen.
            if code in codes:
                continue
            universe_rows.append(row.to_dict())
            codes.append(code)
            bounds_lo.append(lo)
            bounds_hi.append(max(hi, lo))

    if len(codes) < 2:
        return {"error": "Not enough scored candidates across sleeves to run the optimizer."}

    df_hist = db.get_nav_history_dataframe(codes, start_date=active_start, end_date=active_end)
    if df_hist.empty:
        return {"error": "Not enough overlapping NAV history to run the optimizer."}
    df_hist["nav_date"] = pd.to_datetime(df_hist["nav_date"])
    df_hist["nav"] = df_hist["nav"].astype(float)
    wide = df_hist.pivot_table(index="nav_date", columns="scheme_code", values="nav", aggfunc="last").sort_index()
    wide = wide.reindex(columns=codes).ffill().dropna()
    if len(wide) < 30:
        return {"error": "Not enough overlapping trading days across candidates to run the optimizer."}

    daily_ret = wide.pct_change().dropna()
    cov_ann = daily_ret.cov().values * 252.0
    cagr_vec = np.array([
        quant_analytics.calendar_day_cagr(
            (wide[c].iloc[-1] / wide[c].iloc[0]) - 1.0, wide.index[0], wide.index[-1]
        ) for c in codes
    ])

    n = len(codes)
    vol_ceiling = VOL_CEILING_BY_TIER.get(risk_tier, 20.0) / 100.0

    def neg_sharpe(w: np.ndarray) -> float:
        ret = float(np.dot(w, cagr_vec))
        vol = float(np.sqrt(np.dot(w, np.dot(cov_ann, w))))
        return -((ret - risk_free_rate_ann) / vol) if vol > 1e-8 else 0.0

    def vol_constraint(w: np.ndarray) -> float:
        return vol_ceiling - float(np.sqrt(np.dot(w, np.dot(cov_ann, w))))

    eq_constraint = {"type": "eq", "fun": lambda w: float(np.sum(w) - 1.0)}
    vol_ineq = {"type": "ineq", "fun": vol_constraint}
    bounds = list(zip(bounds_lo, bounds_hi))
    x0 = np.array([1.0 / n] * n)

    result = sco.minimize(neg_sharpe, x0, method="SLSQP", bounds=bounds,
                           constraints=[eq_constraint, vol_ineq], options={"maxiter": 500})
    vol_ceiling_applied = True
    if not result.success:
        # A genuinely infeasible vol ceiling given this exact candidate set is better
        # reported honestly than silently dropped — retry once without it, then give up.
        result = sco.minimize(neg_sharpe, x0, method="SLSQP", bounds=bounds,
                               constraints=[eq_constraint], options={"maxiter": 500})
        vol_ceiling_applied = False
        if not result.success:
            return {"error": "The optimizer could not find a feasible allocation for this candidate set."}

    raw_weights = {codes[i]: max(0.0, float(result.x[i])) for i in range(n)}
    total_w = sum(raw_weights.values())
    if total_w <= 0:
        return {"error": "The optimizer returned an all-zero allocation."}
    # Drop noise-level (<0.5%) slivers, then renormalize to 100%.
    weights = {c: w / total_w for c, w in raw_weights.items() if (w / total_w) > 0.005}
    total_w2 = sum(weights.values())
    weights = {c: w / total_w2 for c, w in weights.items()}

    info_by_code = {int(r["scheme_code"]): r for r in universe_rows}
    achieved_vol = float(np.sqrt(sum(
        weights.get(codes[i], 0.0) * weights.get(codes[j], 0.0) * cov_ann[i, j]
        for i in range(n) for j in range(n)
    ))) * 100.0

    picks = []
    for code, w in sorted(weights.items(), key=lambda kv: -kv[1]):
        info = info_by_code.get(code, {})
        picks.append({
            "sleeve": info.get("category", "-"), "scheme_code": code,
            "scheme_name": info.get("scheme_name", str(code)), "fund_house": info.get("fund_house", "-"),
            "category": info.get("category", "-"), "weight_pct": round(w * 100.0, 2),
            "amount": round(w * budget, 2), "expense_ratio": info.get("expense_ratio"),
            "sharpe_ratio": info.get("sharpe_ratio"), "sortino_ratio": info.get("sortino_ratio"),
            "cagr_pct": info.get("cagr_pct"), "max_drawdown_pct": info.get("max_drawdown_pct"),
            "why": (
                f"Weight set by the optimizer to maximize the historical Sharpe ratio of the combined "
                f"candidate set, subject to the {risk_tier} tier's volatility ceiling"
                + (f" (~{vol_ceiling*100:.0f}%)" if vol_ceiling_applied else " — relaxed, the ceiling was infeasible for this set")
                + " and per-sleeve anchoring bounds."
            ),
        })

    return {
        "risk_tier": risk_tier, "budget": budget, "weights": weights, "picks": picks,
        "method": "mean_variance", "vol_ceiling_pct": vol_ceiling * 100.0,
        "vol_ceiling_applied": vol_ceiling_applied, "achieved_vol_pct": round(achieved_vol, 4),
    }


# ============================================================
# 4. BACKTEST GLUE (reuses portfolio_sim.run_backtest as-is)
# ============================================================

def backtest_portfolio(
    weights: Dict[int, float],
    active_start: datetime.date,
    active_end: datetime.date,
    mode: str,
    lump_sum_amount: float,
    sip_amount: float,
    rebalance_freq: str = portfolio_sim.REBALANCE_QUARTERLY,
) -> Dict[str, Any]:
    """Feeds a build_rules_based_portfolio()/build_mvo_portfolio() result's weights straight
    into the existing portfolio_sim backtest engine — the identical mechanism already live on
    Compare & Simulate's Portfolio Backtest tab, including the lookback_row seed that keeps
    XIRR and TWR-CAGR from silently disagreeing for a plain lump-sum, no-rebalancing case.
    Takes the weights dict directly (not the full build_*_portfolio() result) so callers can
    cache on it as a plain, cheaply-hashable value instead of a DataFrame-bearing dict."""
    weights = dict(weights or {})
    codes = list(weights.keys())
    if not codes:
        return {"error": "No funds to backtest."}

    df_hist = db.get_nav_history_dataframe(codes, start_date=active_start, end_date=active_end)
    if df_hist.empty:
        return {"error": "No NAV history for the selected funds in this window."}
    df_hist["nav_date"] = pd.to_datetime(df_hist["nav_date"])
    df_hist["nav"] = df_hist["nav"].astype(float)
    nav_wide = df_hist.pivot_table(index="nav_date", columns="scheme_code", values="nav", aggfunc="last").sort_index()
    nav_wide = nav_wide.ffill().dropna()

    present = [c for c in codes if c in nav_wide.columns]
    if len(present) < len(codes):
        weights = {c: w for c, w in weights.items() if c in present}
        total = sum(weights.values())
        if total <= 0:
            return {"error": "No overlapping NAV history across the selected funds."}
        weights = {c: w / total for c, w in weights.items()}
    if nav_wide.empty or len(nav_wide) < 2:
        return {"error": "Not enough overlapping NAV history to backtest."}

    actual_start = nav_wide.index[0].date()
    lookback_row = None
    df_lb = db.get_nav_history_dataframe(
        list(weights.keys()),
        start_date=actual_start - datetime.timedelta(days=10),
        end_date=actual_start - datetime.timedelta(days=1),
    )
    if not df_lb.empty:
        df_lb["nav_date"] = pd.to_datetime(df_lb["nav_date"])
        lb_wide = (
            df_lb.pivot_table(index="nav_date", columns="scheme_code", values="nav", aggfunc="last")
            .sort_index().ffill().reindex(columns=list(weights.keys()))
        )
        if not lb_wide.empty and not lb_wide.iloc[-1].isna().any():
            lookback_row = lb_wide.iloc[-1]

    return portfolio_sim.run_backtest(
        nav_wide=nav_wide[list(weights.keys())], weights=weights, mode=mode,
        lump_sum_amount=lump_sum_amount, sip_amount=sip_amount,
        rebalance_freq=rebalance_freq, lookback_row=lookback_row,
    )


# ============================================================
# 5. RISK QUESTIONNAIRE
# ============================================================

RISK_QUESTIONNAIRE: List[Dict[str, Any]] = [
    {
        "key": "horizon", "question": "When will you likely need this money?",
        "options": [("Within 1 year", 1), ("1-3 years", 2), ("3-5 years", 3), ("5-10 years", 4), ("10+ years", 5)],
    },
    {
        "key": "goal", "question": "What best describes your primary goal?",
        "options": [
            ("Preserve capital -- I can't afford to lose money", 1),
            ("Steady income with low volatility", 2),
            ("Balanced growth with moderate ups and downs", 3),
            ("Long-term wealth accumulation", 4),
            ("Maximum growth -- I can tolerate large swings", 5),
        ],
    },
    {
        "key": "reaction", "question": "Your portfolio drops 20% in a month. What do you do?",
        "options": [
            ("Sell everything to stop further loss", 1),
            ("Sell some to reduce risk", 2),
            ("Do nothing and wait it out", 4),
            ("Invest more -- it's a buying opportunity", 5),
        ],
    },
    {
        "key": "experience", "question": "How would you describe your investing experience?",
        "options": [
            ("None -- this would be new to me", 1),
            ("Some -- a few years, mostly funds/FDs", 2),
            ("Experienced -- comfortable with equity markets", 4),
            ("Expert -- I actively track markets", 5),
        ],
    },
    {
        "key": "dependence", "question": "How dependent are you on this money for near-term expenses?",
        "options": [
            ("Fully dependent -- I may need it any time", 1),
            ("Somewhat -- a portion may be needed soon", 3),
            ("Not dependent -- this is surplus, long-term capital", 5),
        ],
    },
    {
        "key": "emergency_fund", "question": "Do you have an emergency fund in place?",
        "options": [
            ("No emergency fund yet", 2),
            ("Partial -- a few months' expenses covered", 3),
            ("Yes -- 6+ months of expenses covered", 5),
        ],
    },
    {
        "key": "age_bracket", "question": "Your age bracket (optional context, not the sole factor)",
        "options": [("Under 30", 5), ("30-40", 4), ("40-50", 3), ("50-60", 2), ("60+", 1)],
    },
]

# Cutoffs chosen so each tier spans a roughly even band of the questionnaire's possible
# 6-33 point range (6 required questions x 1-5, age bracket optional and additive).
_SCORE_TIER_CUTOFFS: List[Tuple[int, str]] = [
    (12, "Conservative"), (17, "Moderate"), (22, "Balanced"), (27, "Growth"), (999, "Aggressive"),
]


def score_questionnaire(answers: Dict[str, int]) -> Tuple[int, str]:
    """answers: {question_key: point_value}. Returns (total_score, risk_tier)."""
    total = sum(answers.values())
    for cutoff, tier in _SCORE_TIER_CUTOFFS:
        if total <= cutoff:
            return total, tier
    return total, "Aggressive"


def suitability_warning(risk_tier: str, horizon_years: float) -> Optional[str]:
    """A heads-up, never a block: flags a tier whose equity weight typically wants a longer
    horizon than the one stated. The user can proceed regardless -- this app doesn't decide
    for them, it explains."""
    alloc = SLEEVE_ALLOCATIONS.get(risk_tier, {})
    equity_weight = alloc.get("Equity Core", 0) + alloc.get("Equity Satellite", 0) + alloc.get("International Equity", 0)
    if equity_weight > 0.45 and horizon_years < 3:
        return (
            f"The {risk_tier} tier allocates {equity_weight * 100:.0f}% to equity, which typically needs a "
            f"3+ year horizon to ride out downturns -- you selected {horizon_years:.0f} year(s). Consider a "
            "more conservative tier, or confirm this horizon is intentional."
        )
    return None
