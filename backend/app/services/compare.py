"""The Compare tab of the Compare & Simulate page: everything it says about 2-8 funds,
measured over ONE period that every fund was actually alive and publishing for.

Why this lives server-side. The page used to take whatever NAV rows /nav-history returned
for the selected window and compute each fund's "window return" from its own first row to
its own last row. Three things made those numbers unfit to put side by side:

* Funds publish on different days. On 2026-09-24 HDFC Mid Cap had published its NAV (down
  1.83% that day) and Parag Parikh Flexi Cap had not, so one fund's return included the
  day and the other's did not -- a 1.9 pp swing from a publication lag alone.
* A fund launched part-way through the window was measured over a shorter period than the
  others, with nothing on screen saying so.
* A window starting on a market holiday began at the NEXT NAV, silently dropping the move
  between the last NAV before the window and the first one inside it. The value an
  investor held on a holiday is the previous NAV, and that is what a period return starts
  from.

So the comparison period is defined once, here: every fund's value as of the same start
date to its value as of the same end date. Each fund's own-window figure is still returned
beside it, labelled as such.

Conventions, all stated in the UI's tooltips:
* "As of" a date means the last NAV on or before it.
* Volatility is annualised on the NAVs a year the fund actually published around the
  period (quant_analytics.cadence_obs_per_year: ~245-250/yr for trading-day funds, ~365/yr
  for funds that publish every calendar day, ~307/yr for Quant Liquid), never sqrt(252).
* Volatility, Sharpe and Sortino come from quant_analytics.period_risk_ratios, the same
  routine the Quant page uses, so the same fund over the same period reads the same.
* IDCW NAVs fall by every payout, so every NAV-based figure for an IDCW plan understates
  what its holder earned; the payload flags such funds rather than hiding them.
"""

import datetime
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from app import quant_analytics as qa
from app.classification import classify, is_idcw, sebi_category
from app.core.cache import cached
from app.db.connection import fetchdf, get_connection
from app.db.queries import format_scheme_display_name

#: The page's own ceiling (see MAX_SCHEMES in frontend/app/compare/page.tsx).
MAX_FUNDS = 8
#: A window that starts on a day with no NAV is valued at the last NAV before it -- but
#: only across an ordinary closure (a long weekend, Diwali). A bigger gap means the fund
#: was not publishing, and its period starts at its first NAV inside the window instead.
ANCHOR_MAX_GAP_DAYS = 10
#: A fund whose last NAV is this far behind the freshest selected fund has stopped
#: publishing (merged, wound up, suspended). Same 30-day rule as summary_table.is_active.
STALE_AFTER_DAYS = 30
#: Below this many period returns, annualised ratios are too noisy to lean on.
SHORT_SAMPLE_RETURNS = 60
MIN_OBS_FOR_CORRELATION = 20
ROLLING_DAYS = 365
#: The 1-year-ago NAV for a rolling return may sit up to this many days before the exact
#: date (a holiday); further back and there is no honest 1-year base.
ROLLING_BASE_TOLERANCE_DAYS = 7
#: AMCs accrue TER daily on a 365-day year.
TER_DAY_COUNT = 365.0
DEFAULT_RF_PCT = 6.5
RANK_TOLERANCE = 1e-4


def _r4(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, 4) if np.isfinite(f) else None


def _iso(ts: Any) -> Optional[str]:
    if ts is None or (isinstance(ts, float) and np.isnan(ts)):
        return None
    try:
        if pd.isna(ts):
            return None
    except (TypeError, ValueError):
        pass
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def _ns(dates: Any) -> pd.DatetimeIndex:
    """pandas 2 keeps each datetime column's own resolution (s, us, ns) and merge_asof
    refuses to join two that differ, so every join key is brought to ns first."""
    return pd.DatetimeIndex(pd.to_datetime(dates)).as_unit("ns")


def _text(v: Any) -> Optional[str]:
    return v.strip() if isinstance(v, str) and v.strip() else None


# --- Pure period logic ------------------------------------------------------------------

def asof(series: pd.Series, when: pd.Timestamp) -> Optional[tuple]:
    """(date, nav) of the last NAV on or before `when`, or None."""
    idx = series.index.searchsorted(when, side="right") - 1
    if idx < 0:
        return None
    return series.index[idx], float(series.iloc[idx])


def own_window(series: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> Optional[Dict[str, Any]]:
    """One fund's own period inside [start, end]: where it starts (the value held on
    `start`, or its first NAV if it began publishing later), where it ends, and the NAV
    return between the two. None when it has no NAV inside the window."""
    inside = series[(series.index >= start) & (series.index <= end)]
    if inside.empty:
        return None
    before = series[series.index < start]
    anchored = (
        inside.index[0] > start
        and not before.empty
        and (start - before.index[-1]).days <= ANCHOR_MAX_GAP_DAYS
    )
    if anchored:
        base_date, base_nav = before.index[-1], float(before.iloc[-1])
        effective_start = start
    else:
        base_date, base_nav = inside.index[0], float(inside.iloc[0])
        effective_start = inside.index[0]
    end_date, end_nav = inside.index[-1], float(inside.iloc[-1])
    n_points = len(inside) + (1 if anchored else 0)
    ret = (end_nav / base_nav - 1.0) * 100.0 if base_nav > 0 and n_points >= 2 else None
    return {
        "effective_start": effective_start,
        "base_date": base_date,
        "base_nav": base_nav,
        "end_date": end_date,
        "end_nav": end_nav,
        "anchored_before_start": bool(anchored),
        "n_points": int(n_points),
        "return_pct": ret,
        "days": int((end_date - base_date).days),
    }


def common_period(windows: Dict[int, Optional[Dict[str, Any]]]) -> Dict[str, Any]:
    """Splits the funds into those that can share a period and those that cannot, and
    returns that period.

    A fund is left out -- never silently -- when it has fewer than two NAVs in the window
    ("no_data") or stopped publishing well before the others ("stale"): letting a wound-up
    fund set the end date would drag every other fund's comparison back to its last NAV."""
    status: Dict[int, str] = {}
    with_data = {c: w for c, w in windows.items() if w is not None and w["n_points"] >= 2}
    for c, w in windows.items():
        if c not in with_data:
            status[c] = "no_data"
    if not with_data:
        return {"status": status, "start": None, "end": None}
    freshest = max(w["end_date"] for w in with_data.values())
    stale_cutoff = freshest - pd.Timedelta(days=STALE_AFTER_DAYS)
    for c, w in with_data.items():
        status[c] = "stale" if w["end_date"] < stale_cutoff else "ok"
    ok = {c: w for c, w in with_data.items() if status[c] == "ok"}
    start = max(w["effective_start"] for w in ok.values())
    end = min(w["end_date"] for w in ok.values())
    if start >= end:
        return {"status": status, "start": None, "end": None}
    return {"status": status, "start": start, "end": end}


def period_risk(nav: pd.Series, obs_per_year: float, rf_pct: float) -> Optional[Dict[str, Any]]:
    """Risk over one fund's slice of the common period, from its own base NAV to its end NAV.

    Only returns INSIDE the period count (the first is base -> next NAV). Volatility, Sharpe
    and Sortino come from quant_analytics.period_risk_ratios -- the Quant page's routine:
    each return is charged Rf over its own calendar span (a fund that skips weekend NAVs
    has 2-3 day returns; one day's Rf each gave Quant Liquid a Sharpe of +2.98 for a 6.10%
    year against 6.5%), volatility is measured around the return expected for each
    return's span (span_residuals), and Sharpe and Sortino share one numerator (annualised
    mean excess return),
    so they can differ only by their denominators, never in sign.

    `obs_per_year` should be the fund's cadence around the period (cadence_obs_per_year)."""
    nav = nav.astype(float)
    r = nav.pct_change().dropna()
    n = len(r)
    if n < 3:
        return None
    gap_days = np.asarray((nav.index[1:] - nav.index[:-1]).days, dtype=float)
    ratios = qa.period_risk_ratios(r.values, gap_days, obs_per_year, rf_pct / 100.0)
    drawdown = nav / nav.cummax() - 1.0
    trough = drawdown.idxmin()
    # The peak is the LAST date the NAV stood at that high before the fall -- idxmax() gave
    # the first, so a NAV that touched a high, dipped, and re-touched it exactly (common at
    # 4 decimals for slow accrual funds) dated the fall from the earlier touch.
    before = nav[:trough]
    peak = before.index[np.flatnonzero(before.values == before.values.max())[-1]]
    return {
        "vol_ann_pct": _r4(ratios["vol_ann"] * 100.0),
        "obs_per_year": round(float(obs_per_year), 1),
        "max_drawdown_pct": _r4(float(drawdown.min()) * 100.0),
        "max_drawdown_peak_date": _iso(peak) if drawdown.min() < 0 else None,
        "max_drawdown_trough_date": _iso(trough) if drawdown.min() < 0 else None,
        "sharpe": _r4(ratios["sharpe"]),
        "sortino": _r4(ratios["sortino"]),
        "n_returns": int(n),
        "short_sample": bool(n < SHORT_SAMPLE_RETURNS),
    }


def rolling_returns(series: pd.Series, lo: pd.Timestamp, hi: pd.Timestamp,
                    days: int = ROLLING_DAYS) -> Dict[str, Any]:
    """Trailing `days`-day NAV return on every NAV date in [lo, hi], each measured from the
    NAV as of exactly `days` earlier. The fund's full history is used for the base, so a
    rolling point exists for every date in the period once the fund is a year old.

    Summary statistics run on every point; the plotted series is thinned to one point a
    week when it would otherwise exceed about a year of daily points."""
    target = series[(series.index >= lo) & (series.index <= hi)]
    empty = {"points": [], "min": None, "median": None, "max": None, "pct_positive": None, "n": 0}
    if target.empty:
        return empty
    left = pd.DataFrame({"t": _ns(target.index), "want": _ns(target.index - pd.Timedelta(days=days)),
                         "nav": target.values.astype(float)})
    right = pd.DataFrame({"bdate": _ns(series.index), "bnav": series.values.astype(float)})
    m = pd.merge_asof(left.sort_values("want"), right, left_on="want", right_on="bdate", direction="backward")
    ok = m["bdate"].notna() & ((m["want"] - m["bdate"]).dt.days <= ROLLING_BASE_TOLERANCE_DAYS)
    m = m[ok]
    if m.empty:
        return empty
    daily = pd.Series(((m["nav"] / m["bnav"]) - 1.0).values * 100.0, index=pd.DatetimeIndex(m["t"])).sort_index()
    plotted = daily
    if len(daily) > 300:
        plotted = daily.groupby(daily.index.to_period("W-SUN")).tail(1)
    return {
        "points": [[_iso(t), _r4(v)] for t, v in plotted.items()],
        "min": _r4(daily.min()),
        "median": _r4(daily.median()),
        "max": _r4(daily.max()),
        "pct_positive": round(float((daily > 0).mean() * 100.0), 2),
        "n": int(len(daily)),
    }


def ter_drag(nav: pd.Series, ter_runs: pd.DataFrame, current_ter: Optional[float]) -> Optional[Dict[str, Any]]:
    """Fees the scheme charged over the period, as a percentage of the amount invested at
    its start.

    TER accrues every calendar day on the day's net assets, and the published NAV is
    already net of it -- so the charge is the sum over the calendar days after the start of
    (value held that day) x TER that day / 100 / 365, the value held being the one at the
    last NAV before the day. Each day takes the TER disclosed FOR that day (the ter_history
    row whose run covers it, carried forward across disclosure gaps); days before the first
    disclosure fall back to the scheme's current official TER, and the share of days that did
    so is reported. None when neither exists.

    Per day, not per NAV interval: AMFI's daily TER swings with each day's brokerage (one
    equity fund reads 0.59% on a Saturday and 4.06% on the Monday after), and pricing a
    whole Fri->Mon interval at the Monday rate charged the weekend three times the Monday
    spike -- JioBlackRock Flexi Cap's fee over Feb-Sep 2026 read 0.6954% of the amount
    instead of 0.6542%."""
    if len(nav) < 2:
        return None
    nav = nav.astype(float)
    nav_idx = _ns(nav.index)
    day_idx = pd.date_range(nav_idx[0] + pd.Timedelta(days=1), nav_idx[-1], freq="D").as_unit("ns")
    if len(day_idx) == 0:
        return None
    # The value held on each day is the one at the last NAV strictly before it.
    held = nav.values[np.searchsorted(nav_idx.values, day_idx.values, side="left") - 1] / nav.values[0]
    daily = pd.DataFrame({"day": day_idx, "growth": held})
    if ter_runs is not None and not ter_runs.empty:
        runs = ter_runs.dropna(subset=["total_ter_pct"]).sort_values("ter_date")
        runs = pd.DataFrame({"ter_date": _ns(pd.to_datetime(runs["ter_date"])), "hist_ter": runs["total_ter_pct"].astype(float).values})
        daily = pd.merge_asof(daily, runs, left_on="day", right_on="ter_date", direction="backward")
    else:
        daily["hist_ter"] = np.nan
    from_history = daily["hist_ter"].notna()
    ter = daily["hist_ter"].where(from_history, current_ter if current_ter is not None else np.nan)
    if ter.isna().any():
        return None
    total_days = float(len(daily))
    fee_frac = float((daily["growth"] * ter / 100.0 / TER_DAY_COUNT).sum())
    hist_share = float(from_history.sum() / total_days * 100.0)
    return {
        "fee_pct_of_initial": _r4(fee_frac * 100.0),
        "avg_ter_pct": _r4(float(ter.mean())),
        "history_coverage_pct": round(hist_share, 2),
        "basis": "history" if hist_share >= 99.999 else ("current" if hist_share <= 0.001 else "mixed"),
    }


def _span_residuals(navs: pd.Series) -> np.ndarray:
    """NAV-to-NAV returns net of the return expected for each one's span in calendar days
    (quant_analytics.span_residuals -- the same adjustment volatility gets)."""
    v = navs.values.astype(float)
    r = v[1:] / v[:-1] - 1.0
    g = np.asarray((navs.index[1:] - navs.index[:-1]).days, dtype=float)
    return qa.span_residuals(r, g)[0]


def _weekly(navs: pd.Series) -> pd.Series:
    """The last NAV of each Friday-ending week, plus the first NAV as the starting point."""
    last = navs.groupby(navs.index.to_period("W-FRI")).tail(1)
    return pd.concat([navs.iloc[:1], last]).loc[lambda s: ~s.index.duplicated()]


def correlation_matrix(slices: Dict[int, pd.Series]) -> Dict[str, Any]:
    """Pairwise correlation of period returns, each pair aligned on the dates BOTH funds
    published: a liquid fund's weekend NAVs then fold into its Monday return rather than
    being compared against an equity fund's missing weekend.

    WEEKLY returns (the last common NAV of each Friday-ending week) whenever the period
    holds at least MIN_OBS_FOR_CORRELATION of them for every pair; daily returns only for
    shorter periods, and the payload says which (`basis`). Funds priced on different market
    clocks do not move on the same DATE: an overseas FoF's NAV for day t is struck on the
    US close of t-1. Same-day correlation of Navi Nasdaq 100 FoF and Edelweiss US Technology
    FoF over three years read 0.46 -- their one-day-lagged co-movement is another 0.39, and
    weekly returns read 0.84. For two domestic funds weekly and daily agree (HDFC Mid Cap vs
    UTI Nifty 50: 0.855 weekly, 0.825 daily).

    Each return is taken net of the return expected for its span (span_residuals). On
    aligned dates both funds' Monday returns span the weekend, so for accrual funds the raw
    returns rose and fell together with the CALENDAR, not with each other: a PSU-bond
    target-maturity index fund (Nippon Nifty AAA PSU Bond Plus SDL Sep 2026) read 0.80
    "correlated" with SBI Overnight on a year of daily returns, and -0.02 on adjusted weekly
    returns; Quant Liquid vs SBI Overnight 0.68 -> 0.15. Where price moves dominate the
    accrual -- equity, gilt, credit -- it changes nothing material (HDFC Gilt vs SBI Credit
    Risk, weekly: 0.72 raw, 0.71 adjusted)."""
    codes = list(slices)
    n = len(codes)
    matrix: List[List[Optional[float]]] = [[None] * n for _ in range(n)]
    obs: List[List[int]] = [[0] * n for _ in range(n)]
    pairs = {}
    for i in range(n):
        for j in range(i + 1, n):
            pairs[(i, j)] = pd.concat([slices[codes[i]], slices[codes[j]]], axis=1, join="inner").dropna()
    weekly = bool(pairs) and all(len(_weekly(p)) - 1 >= MIN_OBS_FOR_CORRELATION for p in pairs.values())
    for i in range(n):
        s = _weekly(slices[codes[i]]) if weekly else slices[codes[i]]
        matrix[i][i] = 1.0
        obs[i][i] = max(len(s) - 1, 0)
    for (i, j), pair in pairs.items():
        if weekly:
            pair = _weekly(pair)
        n_ret = max(len(pair) - 1, 0)
        obs[i][j] = obs[j][i] = int(n_ret)
        if n_ret >= MIN_OBS_FOR_CORRELATION:
            a, b = _span_residuals(pair.iloc[:, 0]), _span_residuals(pair.iloc[:, 1])
            if np.std(a) > 1e-12 and np.std(b) > 1e-12:
                matrix[i][j] = matrix[j][i] = _r4(float(np.corrcoef(a, b)[0, 1]))
    return {"codes": codes, "matrix": matrix, "n_obs": obs, "min_obs": MIN_OBS_FOR_CORRELATION,
            "basis": "weekly" if weekly else "daily"}


# --- Loaders ------------------------------------------------------------------------------

def _meta_records(meta: pd.DataFrame) -> Dict[int, Dict[str, Any]]:
    """Meta rows keyed by code, with SQL NULLs as None. pandas hands a NULL text column back
    as NaN, which is truthy: `plan_type or ""` then kept the NaN and `.lower()` on it raised,
    so selecting any of the schemes AMFI lists without a plan (Axis Liquid Fund [112713])
    failed the whole comparison with a 500."""
    clean = meta.astype(object).where(meta.notna(), None)
    return {int(r["scheme_code"]): r for r in clean.to_dict(orient="records")}


def _load_meta(codes: Sequence[int]) -> pd.DataFrame:
    con = get_connection()
    try:
        return fetchdf(con.execute(
            """
            SELECT s.scheme_code, s.scheme_name, s.fund_house, s.category, s.plan_type, s.option_type,
                   s.isin, s.expense_ratio, s.ter_status, s.ter_as_of_date, s.riskometer, s.riskometer_as_of,
                   st.broad_category, st.is_active, st.latest_date, st.latest_nav,
                   st.return_30d_pct, st.return_90d_pct, st.return_1y_pct, st.return_3y_pct,
                   st.high_52w, st.dist_from_52w_high_pct
            FROM schemes s
            LEFT JOIN summary_table st ON st.scheme_code = s.scheme_code
            WHERE s.scheme_code = ANY(%s)
            """,
            (list(codes),),
        ))
    finally:
        con.close()


def _load_navs(codes: Sequence[int]) -> Dict[int, pd.Series]:
    """Full history, not just the window: the value held on a holiday start date, the
    annualisation base and the 1-year-ago base of a rolling return all sit before it."""
    con = get_connection()
    try:
        df = fetchdf(con.execute(
            "SELECT scheme_code, nav_date, nav FROM nav_history WHERE scheme_code = ANY(%s) ORDER BY scheme_code, nav_date",
            (list(codes),),
        ))
    finally:
        con.close()
    out: Dict[int, pd.Series] = {}
    if df.empty:
        return out
    df["nav_date"] = pd.to_datetime(df["nav_date"])
    for code, part in df.groupby("scheme_code"):
        s = pd.Series(part["nav"].astype(float).values, index=pd.DatetimeIndex(part["nav_date"]))
        out[int(code)] = s[s > 0]
    return out


def _load_ter_history(codes: Sequence[int], hi: datetime.date) -> Dict[int, pd.DataFrame]:
    con = get_connection()
    try:
        df = fetchdf(con.execute(
            "SELECT scheme_code, ter_date, valid_to, total_ter_pct FROM ter_history "
            "WHERE scheme_code = ANY(%s) AND ter_date <= %s ORDER BY scheme_code, ter_date",
            (list(codes), hi),
        ))
    except Exception:
        return {}
    finally:
        con.close()
    return {int(c): part for c, part in df.groupby("scheme_code")} if not df.empty else {}


def _load_snapshot(codes: Sequence[int]) -> Dict[int, Dict[str, Any]]:
    """AMFI's fund-level snapshot: assets (all plans together), SEBI benchmark, riskometer,
    and AMFI's own 1/3/5-year returns for each plan and for the benchmark."""
    con = get_connection()
    try:
        df = fetchdf(con.execute(
            """
            SELECT code AS scheme_code, f.fund_name, f.aum_cr, f.benchmark, f.riskometer, f.as_of,
                   f.return_1y_direct, f.return_1y_regular, f.return_1y_benchmark,
                   f.return_3y_direct, f.return_3y_regular, f.return_3y_benchmark,
                   f.return_5y_direct, f.return_5y_regular, f.return_5y_benchmark
            FROM amfi_fund_snapshot f
            CROSS JOIN LATERAL unnest(f.scheme_codes) AS code
            WHERE code = ANY(%s)
            """,
            (list(codes),),
        ))
    except Exception:
        return {}
    finally:
        con.close()
    return {int(r["scheme_code"]): r for r in df.drop_duplicates("scheme_code").to_dict(orient="records")}


def _load_peers(categories: Sequence[str]) -> pd.DataFrame:
    """Every active scheme whose SEBI category (sebi_category) is one of the selected funds'
    -- under whichever AMFI label it is listed: "Income/Debt Oriented Schemes - Liquid Fund"
    and "Debt Scheme - Liquid Fund" are one category. Each row carries its asset class, since
    one category label can hold several (see peer_context)."""
    wanted = {sebi_category(c) for c in categories} - {None}
    if not wanted:
        return pd.DataFrame()
    con = get_connection()
    try:
        labels = [r[0] for r in con.execute(
            "SELECT DISTINCT category FROM summary_table WHERE is_active AND category IS NOT NULL").fetchall()]
        raw = [c for c in labels if sebi_category(c) in wanted]
        if not raw:
            return pd.DataFrame()
        df = fetchdf(con.execute(
            """
            SELECT st.scheme_code, st.scheme_name, st.category, st.broad_category, st.plan_type, st.option_type,
                   st.return_1y_pct, st.return_3y_pct, st.expense_ratio, st.ter_status, st.latest_date,
                   (SELECT min(n.nav_date) FROM nav_history n WHERE n.scheme_code = st.scheme_code) AS first_nav_date
            FROM summary_table st
            WHERE st.is_active AND st.category = ANY(%s)
            """,
            (raw,),
        ))
    finally:
        con.close()
    if df.empty:
        return df
    df["peer_category"] = [sebi_category(c) for c in df["category"]]
    df["asset_class"] = [classify(c, b, n) for c, b, n in zip(df["category"], df["broad_category"], df["scheme_name"])]
    return df


def full_horizon_only(ret: Any, first_nav_date: Any, latest_date: Any, horizon_days: int) -> Optional[float]:
    """A trailing return is kept only when the fund's history actually reaches back the full
    horizon (less a holiday's tolerance). summary_table used to accept a base NAV well short
    of the horizon (330 days for "1Y"), so a fund 342 days old -- JioBlackRock Flexi Cap --
    reported its since-launch return as a 1-year return. It now takes the NAV as of exactly
    N days back and leaves young funds blank itself; this stays as a guard so Compare never
    ranks a since-launch figure even if that table is rebuilt the old way."""
    v = _r4(ret)
    if v is None:
        return None
    try:
        first, latest = pd.Timestamp(first_nav_date), pd.Timestamp(latest_date)
    except (TypeError, ValueError):
        return v
    if pd.isna(first) or pd.isna(latest):
        return v
    return v if (latest - first).days >= horizon_days - ROLLING_BASE_TOLERANCE_DAYS else None


# --- Peer, AMFI and identity blocks --------------------------------------------------------

def _cagr_from_cumulative(pct: Any, years: float) -> Optional[float]:
    """summary_table's 3Y figure is cumulative (latest NAV over the NAV ~1,095 days back);
    AMFI and every fact sheet quote 3 years annualised, so this converts before showing it."""
    v = _r4(pct)
    if v is None or v <= -100.0:
        return None
    return ((1.0 + v / 100.0) ** (1.0 / years) - 1.0) * 100.0


def peer_context(fund: Dict[str, Any], peers: pd.DataFrame) -> Dict[str, Any]:
    """Where the fund sits among ACTIVE schemes of its own SEBI category, asset class and plan
    type -- the grouping the Overview and Leaders pages use (db.queries: asset_class,
    peer_category, plan).

    * SEBI category, not AMFI's raw label: the legacy "Income/Debt Oriented Schemes - Liquid
      Fund" and "Debt Scheme - Liquid Fund" are one category, and ranking on the raw label
      split it in two (Quant Liquid Direct ranked 21 of 24 instead of 41 of 49).
    * Asset class too: "Index Funds", "Other ETFs" and "FoF Domestic" each hold equity, debt,
      gold and overseas trackers. A Nifty 50 index fund was ranked among 330 "Index Funds"
      including target-maturity debt funds, and SBI Gold Fund among equity FoFs.
    * Direct is never ranked against Regular, whose returns carry a distributor commission.
    * IDCW schemes are left out of the peer group: their NAV returns are understated by every
      payout, so they would drag the median down and flatter everyone ranked against them.
      For the same reason an IDCW fund itself gets the peer medians but no rank.
    Ties share a rank (1 + the number of peers strictly ahead)."""
    category = sebi_category(fund.get("category"))
    asset_class = fund.get("asset_class")
    plan_label = fund.get("plan_type") or "Unspecified plan"
    out: Dict[str, Any] = {
        "peer_group": f"{category or 'Uncategorised'} - {plan_label}",
        "n_1y": 0, "median_1y_pct": None, "rank_1y": None,
        "n_3y": 0, "median_3y_cagr_pct": None, "rank_3y": None,
        "n_ter": 0, "median_ter_pct": None,
        "rank_note": None,
    }
    if peers.empty or not category:
        return out
    if "peer_category" not in peers.columns:
        peers = peers.assign(peer_category=[sebi_category(c) for c in peers["category"]])
    if "asset_class" not in peers.columns:
        broad = peers["broad_category"] if "broad_category" in peers.columns else [None] * len(peers)
        peers = peers.assign(asset_class=[classify(c, b, n) for c, b, n in zip(peers["category"], broad, peers["scheme_name"])])
    same_cat = peers[peers["peer_category"] == category]
    if asset_class and same_cat["asset_class"].nunique() > 1:
        # "Index Funds (Debt) - Direct": the category alone does not say which group it is.
        out["peer_group"] = f"{category} ({asset_class}) - {plan_label}"
    plan = fund.get("plan_type") or ""
    g = same_cat[same_cat["plan_type"].fillna("") == plan]
    if asset_class:
        g = g[g["asset_class"] == asset_class]
    # A boolean ARRAY, not a list: indexing with an empty list selects no COLUMNS, and the
    # next line's g["return_1y_pct"] then raised for a fund with no active peers at all.
    g = g[np.array([not is_idcw(o, n) for o, n in zip(g["option_type"], g["scheme_name"])], dtype=bool)]
    if "first_nav_date" in g.columns:
        g = g.assign(
            return_1y_pct=[full_horizon_only(r, f, l, 365) for r, f, l in zip(g["return_1y_pct"], g["first_nav_date"], g["latest_date"])],
            return_3y_pct=[full_horizon_only(r, f, l, 1095) for r, f, l in zip(g["return_3y_pct"], g["first_nav_date"], g["latest_date"])],
        )
    r1 = g["return_1y_pct"].dropna().astype(float)
    c3 = g["return_3y_pct"].dropna().astype(float).map(lambda v: _cagr_from_cumulative(v, 3.0)).dropna()
    ter = g.loc[g["ter_status"] == "official", "expense_ratio"].dropna().astype(float)
    out.update({
        "n_1y": int(len(r1)), "median_1y_pct": _r4(r1.median()) if len(r1) else None,
        "n_3y": int(len(c3)), "median_3y_cagr_pct": _r4(c3.median()) if len(c3) else None,
        "n_ter": int(len(ter)), "median_ter_pct": _r4(ter.median()) if len(ter) else None,
    })
    if fund.get("is_idcw"):
        out["rank_note"] = "IDCW plan: its NAV return is cut by payouts, so it is not ranked against Growth plans."
        return out
    # The fund's own figure arrives rounded to 4 dp while its row in the peer set is not;
    # the tolerance keeps a fund from being counted as beating itself.
    mine_1y = _r4(fund.get("return_1y_pct"))
    if mine_1y is not None and len(r1):
        out["rank_1y"] = int((r1 > mine_1y + RANK_TOLERANCE).sum()) + 1
    mine_3y = fund.get("return_3y_cagr_pct")
    if mine_3y is not None and len(c3):
        out["rank_3y"] = int((c3 > mine_3y + RANK_TOLERANCE).sum()) + 1
    return out


def official_block(fund: Dict[str, Any], snap: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """AMFI's own published returns for the fund's plan and for its SEBI benchmark. AMFI
    computes these on the Growth NAV, so an IDCW plan gets the benchmark but no fund return."""
    if not snap:
        return None
    plan = (fund.get("plan_type") or "").lower()
    col = "direct" if plan == "direct" else ("regular" if plan == "regular" else None)
    block: Dict[str, Any] = {"benchmark": _text(snap.get("benchmark")), "as_of": _iso(snap.get("as_of")),
                             "fund_name": _text(snap.get("fund_name"))}
    for h in ("1y", "3y", "5y"):
        fund_ret = None if (fund.get("is_idcw") or col is None) else _r4(snap.get(f"return_{h}_{col}"))
        bench = _r4(snap.get(f"return_{h}_benchmark"))
        block[f"fund_{h}_pct"] = fund_ret
        block[f"bench_{h}_pct"] = bench
        block[f"excess_{h}_pp"] = _r4(fund_ret - bench) if fund_ret is not None and bench is not None else None
    return block


# --- Entry point -------------------------------------------------------------------------

def compare_funds(codes: Sequence[int], start: Optional[datetime.date] = None,
                  end: Optional[datetime.date] = None, rf_pct: float = DEFAULT_RF_PCT) -> Dict[str, Any]:
    return _compare_cached(tuple(int(c) for c in codes), start, end, float(rf_pct))


@cached(ttl=600)
def _compare_cached(codes: tuple, start: Optional[datetime.date], end: Optional[datetime.date],
                    rf_pct: float) -> Dict[str, Any]:
    meta_by_code = _meta_records(_load_meta(codes))
    navs = _load_navs(codes)

    if end is None:
        ends = [s.index[-1] for s in navs.values() if not s.empty]
        end = max(ends).date() if ends else datetime.date.today()
    if start is None:
        start = end - datetime.timedelta(days=365)
    ts_start, ts_end = pd.Timestamp(start), pd.Timestamp(end)

    windows = {c: (own_window(navs[c], ts_start, ts_end) if c in navs else None) for c in codes}
    period = common_period(windows)
    c_start, c_end = period["start"], period["end"]

    ter_hist = _load_ter_history(codes, end)
    snapshot = _load_snapshot(codes)
    peers = _load_peers(sorted({m["category"] for m in meta_by_code.values() if m.get("category")}))

    funds: List[Dict[str, Any]] = []
    slices: Dict[int, pd.Series] = {}
    for c in codes:
        m = meta_by_code.get(c)
        if m is None:
            funds.append({"scheme_code": c, "display_name": f"Scheme {c} [{c}]", "status": "not_found",
                          "status_note": f"AMFI code {c} is not in the database."})
            continue
        idcw = is_idcw(m.get("option_type"), m.get("scheme_name"))
        first_nav = navs[c].index[0] if c in navs and not navs[c].empty else None
        fund: Dict[str, Any] = {
            "scheme_code": c,
            "display_name": format_scheme_display_name(m.get("scheme_name"), m.get("plan_type"), m.get("option_type"), c),
            "scheme_name": m.get("scheme_name"),
            "fund_house": m.get("fund_house"),
            "category": m.get("category"),
            "sebi_category": sebi_category(m.get("category")),
            "asset_class": classify(m.get("category"), m.get("broad_category"), m.get("scheme_name")),
            "plan_type": m.get("plan_type"),
            "option_type": m.get("option_type"),
            "isin": m.get("isin"),
            "is_idcw": idcw,
            "is_active": bool(m["is_active"]) if isinstance(m.get("is_active"), (bool, np.bool_)) else None,
            "expense_ratio": _r4(m.get("expense_ratio")),
            "ter_status": m.get("ter_status"),
            "ter_as_of_date": _iso(m.get("ter_as_of_date")),
            "latest_nav": _r4(m.get("latest_nav")),
            "latest_date": _iso(m.get("latest_date")),
            "return_30d_pct": _r4(m.get("return_30d_pct")),
            "return_90d_pct": _r4(m.get("return_90d_pct")),
            "return_1y_pct": full_horizon_only(m.get("return_1y_pct"), first_nav, m.get("latest_date"), 365),
            "return_3y_cagr_pct": _r4(_cagr_from_cumulative(
                full_horizon_only(m.get("return_3y_pct"), first_nav, m.get("latest_date"), 1095), 3.0)),
            # summary_table measures the 52 weeks back from the market's latest NAV date, and
            # does not gate the figure on the fund still publishing: a fund that stopped months
            # ago got a "distance from its 52-week high" measured from a NAV that is no longer
            # current. Blank for such a fund, as its trailing returns already are.
            "high_52w": _r4(m.get("high_52w")) if m.get("is_active") is not False else None,
            "dist_from_52w_high_pct": _r4(m.get("dist_from_52w_high_pct")) if m.get("is_active") is not False else None,
        }
        snap = snapshot.get(c)
        fund["riskometer"] = _text(m.get("riskometer")) or (_text(snap.get("riskometer")) if snap else None)
        fund["riskometer_as_of"] = _iso(m.get("riskometer_as_of")) if _text(m.get("riskometer")) else (_iso(snap.get("as_of")) if snap else None)
        fund["aum_cr"] = _r4(snap.get("aum_cr")) if snap else None
        fund["aum_as_of"] = _iso(snap.get("as_of")) if snap else None
        fund["official"] = official_block(fund, snap)
        fund["peer"] = peer_context(fund, peers)

        w = windows.get(c)
        fund["own_window"] = None if w is None else {
            "start_date": _iso(w["base_date"]), "start_nav": _r4(w["base_nav"]),
            "end_date": _iso(w["end_date"]), "end_nav": _r4(w["end_nav"]),
            "return_pct": _r4(w["return_pct"]), "days": w["days"],
            "anchored_before_start": w["anchored_before_start"],
        }
        status = period["status"].get(c, "no_data")
        fund["status"] = status
        fund["status_note"] = None
        series = navs.get(c)
        if status == "no_data":
            fund["status_note"] = "Fewer than two NAVs in the selected window."
        elif status == "stale":
            fund["status_note"] = (f"Last NAV in the window is {_iso(w['end_date'])}, more than {STALE_AFTER_DAYS} days behind "
                                   "the other funds -- it has stopped publishing, so it is left out of the common period.")
        # The own-window series, anchor included, for the charts.
        if w is not None and series is not None:
            s = series[(series.index >= w["base_date"]) & (series.index <= w["end_date"])]
            fund["series"] = [[_iso(t), _r4(v)] for t, v in s.items()]
        else:
            fund["series"] = []

        fund["common"] = fund["risk"] = fund["cost"] = None
        fund["rolling_1y"] = None
        if status == "ok" and c_start is not None:
            base = asof(series, c_start)
            stop = asof(series, c_end)
            sl = series[(series.index >= base[0]) & (series.index <= stop[0])]
            slices[c] = sl
            total = stop[1] / base[1] - 1.0
            # The return runs over the common period's calendar dates -- the value held on
            # c_start is the NAV as of c_start -- so every fund is annualised over the same
            # span. Annualising over each fund's own NAV dates gave an equity fund anchored
            # on the Friday before a Saturday start more days than a liquid fund that priced
            # that Saturday: different exponents for one period, and near a year one fund
            # could get a CAGR and the other none.
            days = int((c_end - c_start).days)
            fund["common"] = {
                "start_date": _iso(base[0]), "start_nav": _r4(base[1]),
                "end_date": _iso(stop[0]), "end_nav": _r4(stop[1]),
                "return_pct": _r4(total * 100.0),
                # Annualising less than a year would state a rate the fund never earned.
                "cagr_pct": _r4(qa.calendar_day_cagr(total, c_start, c_end) * 100.0) if days >= 365 else None,
                "days": days,
            }
            # The cadence the fund actually had around this period (over at least a year),
            # not a high quantile of its whole history: Quant Liquid published ~355 NAVs a
            # year in 2015-17 and ~307 now, and was annualised on 354.
            obs = qa.cadence_obs_per_year(series.index, base[0], stop[0])
            fund["risk"] = period_risk(sl, obs, rf_pct)
            current_ter = fund["expense_ratio"] if fund["ter_status"] == "official" else None
            fund["cost"] = ter_drag(sl, ter_hist.get(c), current_ter)
            fund["rolling_1y"] = rolling_returns(series, c_start, c_end)
        funds.append(fund)

    common: Optional[Dict[str, Any]] = None
    if c_start is not None:
        ok = [f for f in funds if f.get("status") == "ok"]
        req_days = max((ts_end - ts_start).days, 1)
        common_days = int((c_end - c_start).days)
        common = {
            "start": _iso(c_start), "end": _iso(c_end), "days": common_days,
            "coverage_pct": round(min(common_days / req_days, 1.0) * 100.0, 2),
            # Who set the boundaries, so the page can say why the period is what it is.
            "limited_start_by": [f["scheme_code"] for f in ok if windows[f["scheme_code"]]["effective_start"] == c_start and c_start > ts_start],
            "limited_end_by": [f["scheme_code"] for f in ok if windows[f["scheme_code"]]["end_date"] == c_end
                               and c_end < max(windows[x["scheme_code"]]["end_date"] for x in ok)],
            # The newest NAV any fund in the period has, so the page can say how far behind
            # the fund that set the end is (a day's publication lag, or a matured fund).
            "latest_end": _iso(max(windows[f["scheme_code"]]["end_date"] for f in ok)),
            "fund_count": len(ok),
        }

    return {
        "requested": {"start": _iso(ts_start), "end": _iso(ts_end), "days": int((ts_end - ts_start).days)},
        "common": common,
        "rf_pct": rf_pct,
        "funds": funds,
        "correlation": correlation_matrix(slices) if len(slices) >= 2 else None,
        "limits": {"max_funds": MAX_FUNDS, "anchor_max_gap_days": ANCHOR_MAX_GAP_DAYS, "stale_after_days": STALE_AFTER_DAYS,
                   "short_sample_returns": SHORT_SAMPLE_RETURNS, "rolling_days": ROLLING_DAYS},
    }
