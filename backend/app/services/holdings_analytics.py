"""Holdings analytics: daily value series, TWR index, period returns, attribution,
allocation, targets/drift and new-money rebalancing.

The core artefact is `timeseries()`: the ledger replayed day by day against AMFI
NAVs into (a) the portfolio's rupee value, (b) its external cash flows and
(c) a **time-weighted return index** -- a unitised "portfolio NAV" that starts at
100 and moves only with investment performance, not with deposits/withdrawals:

    r_t = (1 + pre_t / V_{t-1} - 1) * (1 + V_t / (pre_t + F_t) - 1) - 1
    index_t = index_{t-1} * (1 + r_t)

where F_t is the day's net external flow (purchases +, redemptions and dividend
payouts -) and pre_t is the book valued at that day's NAVs immediately before the
flow. A day carrying a flow is thus two sub-periods, valued at the flow and
chain-linked -- the standard time-weighted treatment, which keeps a large deposit
into a small portfolio from turning a few rupees of stamp duty into a double-digit
daily "loss". On a flow-free day pre_t == V_t and the whole thing collapses to
r_t = V_t / V_{t-1} - 1. Flows are allotted at that day's NAV, exactly as an AMC
allots an order, so the sub-period boundary is a real one rather than an
intra-day approximation. Switches between the portfolio's own funds net to zero
and are internal. That index is also what Phase 3 hands to quant_analytics /
factor_model / stress_testing, which all expect a `nav_date`/`nav` frame.

Money-weighted (XIRR) and time-weighted numbers are both shown, side by side, for
the reason portfolio_sim's docstring gives: they answer different questions.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app import quant_analytics
from app.core.cache import cached
from app.db import holdings as hdb
from app.db.connection import get_data_version
from app.services import holdings_ledger as ledger
from app.services import holdings_service as svc

# --- Asset classes -------------------------------------------------------------
#
# Shared with the market Overview, so a fund is classed the same on both pages; see
# app/classification.py for the rules.
from app.classification import ASSET_CLASSES, classify  # noqa: E402,F401  (re-exported)


# --- Daily time series -------------------------------------------------------------

PORTFOLIO_FLOW_SIGN = {"BUY": 1, "SIP": 1, "REDEEM": -1, "DIVIDEND_PAYOUT": -1}
HOLDING_FLOW_SIGN = {"BUY": 1, "SIP": 1, "SWITCH_IN": 1, "REDEEM": -1, "SWITCH_OUT": -1, "DIVIDEND_PAYOUT": -1}
# Below this a portfolio is treated as empty: a few paise of rounding crumbs left
# after a full redemption must not become the base of a +1,000,000% "return".
MIN_BASE_VALUE = 1.0


def explicit_benchmark(portfolio_ids: List[int]) -> Optional[int]:
    """A single portfolio's chosen benchmark scheme, if the owner set one. Without
    one, the benchmark is the category blend below -- never a blanket equity index,
    which would grade a liquid-fund portfolio against the stock market."""
    if len(portfolio_ids) == 1:
        p = hdb.get_portfolio(portfolio_ids[0])
        if p and p.get("benchmark_scheme_code"):
            return int(p["benchmark_scheme_code"])
    return None


# --- Category-blend benchmark ----------------------------------------------------------
#
# "Did my funds beat their peers?" Each holding is compared with the average daily
# return of its own SEBI category (quant_analytics.get_synthetic_category_benchmark,
# the Quant page's peer benchmark), weighted by how much of the portfolio sat in that
# holding on the previous day. A liquid fund is measured against liquid funds, an
# arbitrage fund against arbitrage funds, an equity fund against its equity category.

BLEND_NAME = "Category blend (each fund vs its SEBI category average)"


def category_daily_returns(categories: List[str], start: datetime.date, end: datetime.date) -> pd.DataFrame:
    """Daily peer-average returns, one column per category."""
    cols = {}
    for cat in sorted({c for c in categories if c}):
        df = quant_analytics.get_synthetic_category_benchmark(cat, start, end)
        if not df.empty:
            cols[cat] = df.set_index("nav_date")["daily_return"]
    return pd.DataFrame(cols)


def blend_index(weights: pd.DataFrame, code_category: Dict[int, str], index: pd.DatetimeIndex) -> Optional[pd.Series]:
    """Index (base 100) of sum_i w_i,t * r_category(i),t. `weights` rows are the
    weights to apply on each date (they need not sum to 1 before normalisation).
    Days a category has no peer return count as 0 for that slice."""
    if weights.empty or index.empty:
        return None
    cat_r = category_daily_returns(list(code_category.values()), index[0].date(), index[-1].date()).reindex(index).fillna(0.0)
    if cat_r.empty:
        return None
    w = weights.reindex(index).fillna(0.0)
    w = w.div(w.sum(axis=1).replace(0.0, np.nan), axis=0).fillna(0.0)
    r = sum(w[code] * cat_r[cat] for code, cat in code_category.items() if cat in cat_r.columns and code in w.columns)
    if isinstance(r, int):      # no holding had a category series
        return None
    r.iloc[0] = 0.0             # the index starts at 100 on the first date
    return 100.0 * (1.0 + r).cumprod()


def blend_components(weights_now: Dict[int, float], code_category: Dict[int, str]) -> List[Dict[str, Any]]:
    by_cat: Dict[str, float] = {}
    for code, w in weights_now.items():
        by_cat[code_category.get(code) or "Unknown"] = by_cat.get(code_category.get(code) or "Unknown", 0.0) + w
    total = sum(by_cat.values()) or 1.0
    return sorted(({"category": c, "weight_pct": v / total * 100.0} for c, v in by_cat.items()), key=lambda x: -x["weight_pct"])


def _asof_index(index: pd.DatetimeIndex, day: datetime.date) -> pd.Timestamp:
    """The NAV date a trade on `day` was allotted at: the last index date on or
    before it (weekend/holiday -> previous NAV, matching nav_on_or_before)."""
    ts = pd.Timestamp(day)
    pos = index.searchsorted(ts, side="right") - 1
    return index[max(pos, 0)]


@cached(ttl=600, maxsize=64)
def _timeseries_cached(pid_key: str, ledger_v: int, data_v: int, benchmark: Optional[int]) -> Dict[str, Any]:
    from app.db import queries as db  # read-only use of the façade

    portfolio_ids = svc.resolve_portfolio_ids(pid_key)
    txns = svc.with_split_scales(hdb.list_transactions(portfolio_ids))
    if not txns:
        return {"empty": True, "portfolio_ids": portfolio_ids}

    codes = sorted({int(t["scheme_code"]) for t in txns})
    first = min(t["trade_date"] for t in txns)
    bench_code = benchmark or explicit_benchmark(portfolio_ids)
    load_codes = codes + ([bench_code] if bench_code and bench_code not in codes else [])
    nav_long = db.get_nav_history_dataframe(load_codes, start_date=first - datetime.timedelta(days=15))
    if nav_long.empty:
        return {"empty": True, "portfolio_ids": portfolio_ids}
    nav_long["nav_date"] = pd.to_datetime(nav_long["nav_date"])
    nav = nav_long.pivot_table(index="nav_date", columns="scheme_code", values="nav", aggfunc="last").sort_index().ffill()

    held_nav = nav.reindex(columns=codes)
    # The index is every NAV date on which any held fund published, from the first
    # trade's allotment date on.
    idx = held_nav.dropna(how="all").index
    start_ts = _asof_index(idx, first)
    idx = idx[idx >= start_ts]
    held_nav = held_nav.loc[idx]

    unit_delta = pd.DataFrame(0.0, index=idx, columns=codes)
    pf_flow = pd.Series(0.0, index=idx)
    hold_flow = pd.DataFrame(0.0, index=idx, columns=codes)
    # The units an *external* flow brought in or took out, kept apart from switches and
    # dividend reinvestments (which move units without any money crossing the portfolio
    # boundary). Used below to value the book at the moment of the flow.
    flow_units = pd.DataFrame(0.0, index=idx, columns=codes)
    # A dividend paid out leaves through the NAV, not through units: no unit delta
    # carries it, so it is tracked on its own.
    payout = pd.Series(0.0, index=idx)
    for t in txns:
        d, code, ttype, amt = _asof_index(idx, t["trade_date"]), int(t["scheme_code"]), t["txn_type"], float(t["amount"])
        signed_units = ledger.unit_sign(ttype) * float(ledger.effective_units(t))
        unit_delta.at[d, code] += signed_units
        pf_flow.at[d] += PORTFOLIO_FLOW_SIGN.get(ttype, 0) * amt
        hold_flow.at[d, code] += HOLDING_FLOW_SIGN.get(ttype, 0) * amt
        if PORTFOLIO_FLOW_SIGN.get(ttype, 0):
            flow_units.at[d, code] += signed_units
        if ttype == "DIVIDEND_PAYOUT":
            payout.at[d] += amt

    units_held = unit_delta.cumsum().clip(lower=0.0)
    values_by_code = (units_held * held_nav.fillna(0.0)).fillna(0.0)
    value = values_by_code.sum(axis=1)

    # --- Daily return, sub-periods split at the cash flow ---------------------------
    #
    # A day carrying an external flow is two sub-periods, valued at the flow and
    # chain-linked:
    #
    #   pre_t  = V_t - (what the flow itself put on the book at today's NAV)
    #   r1_t   = pre_t / V_{t-1} - 1            what the holdings did before the flow
    #   r2_t   = V_t / (pre_t + F_t) - 1        what the post-flow book then did
    #   r_t    = (1 + r1_t)(1 + r2_t) - 1
    #
    # This is the standard time-weighted treatment of a large flow -- the one GIPS
    # requires -- and what it buys over the one-line r_t = (V_t - F_t)/V_{t-1} - 1 is
    # *where the friction lands*. A purchase does not add its full rupee amount to the
    # book: stamp duty comes out of it and the AMC rounds units to 3 decimals, so V_t
    # falls short of pre_t + F_t by a few rupees. The one-line form charges that whole
    # shortfall to V_{t-1}. On 2026-09-15 portfolio 2 opened at Rs 1,499.39 and took in
    # Rs 1,000,000: ~Rs 20 of purchase friction measured against a Rs 1,499 base read as
    # -1.35% for the day, and then dominated every window containing it -- a red "-1.18%"
    # headline sitting above a green "+Rs 2,925.50". Chain-linking charges it to the base
    # that actually bore it (pre_t + F_t), where it is a couple of thousandths of a percent.
    #
    # A threshold ("fall back to something else when the opening value is small next to
    # the flow") was the alternative and is rejected deliberately: it needs a constant to
    # tune, it flips behaviour discontinuously on either side of that constant, and it is
    # wrong in degree everywhere below the cut rather than only at the extreme. The split
    # has no constant, and on a flow-free day it collapses to the one-line form exactly
    # (pre_t == V_t and F_t == 0, so r2_t == 0), so settled figures do not move.
    #
    # pre_t is built by subtracting the flow's own book effect from V_t rather than by
    # revaluing yesterday's units, because the two differ for the flows that move no
    # units: a dividend payout leaves through the NAV (add it back), while a switch or a
    # dividend reinvestment moves units but no money (nothing to take out).
    flow_book = (flow_units * held_nav.fillna(0.0)).fillna(0.0).sum(axis=1)
    pre_flow = value - flow_book + payout
    post_flow = pre_flow + pf_flow
    prev = value.shift(1).fillna(0.0)

    r1 = pd.Series(0.0, index=idx)
    has_base = prev >= MIN_BASE_VALUE
    r1[has_base] = pre_flow[has_base] / prev[has_base] - 1.0
    # MIN_BASE_VALUE still guards the *empty* book at both ends: a portfolio not opened
    # yet, and the paise of rounding crumbs left after a full redemption, which must
    # never become the base of a four-digit "return".
    r2 = pd.Series(0.0, index=idx)
    funded = post_flow >= MIN_BASE_VALUE
    r2[funded] = value[funded] / post_flow[funded] - 1.0
    r = (1.0 + r1) * (1.0 + r2) - 1.0
    twr = 100.0 * (1.0 + r).cumprod()

    bench, bench_name, components = None, None, []
    if bench_code and bench_code in nav.columns:
        b = nav[bench_code].reindex(idx).ffill()
        first_valid = b.first_valid_index()
        if first_valid is not None:
            bench = 100.0 * b / b.loc[first_valid]
            bench_name = hdb.display_name(hdb.scheme_meta([bench_code]).get(bench_code, {"scheme_code": bench_code}))
    elif not bench_code:
        # Yesterday's holding weights applied to today's peer returns (day one uses its own mix).
        code_cat = {c: m.get("category") for c, m in hdb.scheme_meta(codes).items()}
        weights = values_by_code.shift(1)
        weights.iloc[0] = values_by_code.iloc[0]
        bench = blend_index(weights, code_cat, idx)
        if bench is not None:
            bench_name = BLEND_NAME
            last = values_by_code.iloc[-1]
            components = blend_components({c: float(last[c]) for c in codes if last[c] > 0}, code_cat)

    return {
        "empty": False,
        "portfolio_ids": portfolio_ids,
        "index": idx,
        "value": value,
        "flows": pf_flow,
        "invested": pf_flow.cumsum(),
        "twr": twr,
        "values_by_code": values_by_code,
        "holding_flows": hold_flow,
        "nav": held_nav,
        "bench_code": bench_code if bench is not None else None,
        "bench_name": bench_name,
        "bench_components": components,
        "bench": bench,
    }


def timeseries(pid: str, benchmark: Optional[int] = None) -> Dict[str, Any]:
    """Cached per (view, ledger version, market-data version, benchmark). Read-only."""
    return _timeseries_cached(svc.view_key(pid), svc.ledger_version(), get_data_version(), benchmark)


def _dates(idx: pd.DatetimeIndex) -> List[str]:
    return [d.strftime("%Y-%m-%d") for d in idx]


def _series(s: Optional[pd.Series], ndigits: int = 4) -> Optional[List[Optional[float]]]:
    if s is None:
        return None
    return [None if (v is None or not np.isfinite(v)) else round(float(v), ndigits) for v in s.values]


# --- Performance -----------------------------------------------------------------------

PERIODS: List[Tuple[str, Optional[int]]] = [
    ("1M", 30), ("3M", 91), ("6M", 182), ("1Y", 365), ("3Y", 1095), ("5Y", 1826), ("Since start", None),
]


def _period_return(level: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> Optional[float]:
    s = level.loc[:start]
    if s.empty or not np.isfinite(s.iloc[-1]) or s.iloc[-1] <= 0:
        return None
    return float(level.loc[end] / s.iloc[-1] - 1.0)


def _window_xirr(ts: Dict[str, Any], start: pd.Timestamp, end: pd.Timestamp, since_start: bool) -> Optional[float]:
    """XIRR over a window: the portfolio's value at the window start counts as the
    opening investment, then every external flow inside it, then the end value."""
    value, flows = ts["value"], ts["flows"]
    in_window = (flows.index <= end) & ((flows.index > start) | since_start)
    cfs = [(d.date(), -float(f)) for d, f in flows[in_window].items() if f != 0]
    if not since_start and float(value.loc[:start].iloc[-1]) > 0:
        cfs.append((start.date(), -float(value.loc[:start].iloc[-1])))
    return svc.compute_xirr(cfs, end.date(), float(value.loc[end]))[0]


def _annualise(ret: Optional[float], start: pd.Timestamp, end: pd.Timestamp) -> Optional[float]:
    """Period return in %, annualised (calendar-day CAGR) for spans of a year or more."""
    if ret is None:
        return None
    return (quant_analytics.calendar_day_cagr(ret, start, end) if (end - start).days >= 365 else ret) * 100.0


#: Trailing windows for the Holdings tab's tiles, counted in NAV days rather than calendar
#: days -- "the last 2 days" over a weekend would otherwise be two days in which nothing
#: could possibly have changed.
RECENT_WINDOWS: Tuple[int, ...] = (2, 5, 7, 10)


def recent_changes(pid: str, windows: Tuple[int, ...] = RECENT_WINDOWS) -> Dict[str, Any]:
    """How the holdings themselves moved over the last N NAV days, per window.

    Time-weighted, not the raw change in portfolio value: money paid in during the window
    would otherwise read as a gain. The rupee figure is the market gain on that basis --
    each day's value change minus that day's own contribution or withdrawal."""
    ts = timeseries(pid)
    if ts["empty"]:
        return {"empty": True, "windows": []}

    idx: pd.DatetimeIndex = ts["index"]
    level, value, flows, bench = ts["twr"], ts["value"], ts["flows"], ts["bench"]
    end = idx[-1]
    # Daily market gain in rupees: what the holdings did, with the day's own flow removed.
    market_gain = value.diff().fillna(0.0) - flows.reindex(idx).fillna(0.0)

    out = []
    for days in windows:
        # N NAV days means N daily changes, so the window opens one row earlier.
        available = len(idx) >= days + 1
        start = idx[-(days + 1)] if available else idx[0]
        ret = _period_return(level, start, end) if available else None
        # The same window of the peer benchmark the Performance tab plots, so a tile can say
        # whether a move was the funds or just the market they sit in: +0.04% reads very
        # differently when the funds' own categories did +0.10% over the same days.
        bench_ret = _period_return(bench, start, end) if (available and bench is not None) else None
        gain = float(market_gain.loc[start:end].iloc[1:].sum()) if available else None
        out.append({
            "days": days,
            "available": available,
            "start": start.strftime("%Y-%m-%d") if available else None,
            "change_pct": ret * 100.0 if ret is not None else None,
            # The per-day rate that compounds to the window's return, not a plain mean:
            # the plain mean of daily returns would not reproduce the figure above it.
            "avg_daily_pct": ((1.0 + ret) ** (1.0 / days) - 1.0) * 100.0 if ret is not None else None,
            "gain": gain,
            "benchmark_change_pct": bench_ret * 100.0 if bench_ret is not None else None,
            "excess_pp": (ret - bench_ret) * 100.0 if (ret is not None and bench_ret is not None) else None,
            "nav_days_held": max(len(idx) - 1, 0),
            # NAV days, not calendar days: holidays make a date promise unkeepable.
            "days_needed": 0 if available else days + 1 - len(idx),
        })
    return {"empty": False, "as_of": end.strftime("%Y-%m-%d"), "benchmark_name": ts["bench_name"], "windows": out}


def since_start(pid: str) -> Dict[str, Any]:
    """Time-weighted return since the first investment, beside the peer benchmark.

    This is the headline answer XIRR cannot give for a young portfolio: XIRR is withheld
    for the first MIN_XIRR_DAYS because annualising a few days' return exaggerates it, but
    "how have my funds done so far, and did they beat the funds they compete with" has an
    honest answer from day two. Same basis as the Performance tab's "Since start" row."""
    ts = timeseries(pid)
    if ts["empty"]:
        return {"twr_pct": None, "benchmark_pct": None, "excess_pp": None, "start": None, "benchmark_name": None}
    idx: pd.DatetimeIndex = ts["index"]
    first, end = idx[0], idx[-1]
    ret = _period_return(ts["twr"], first, end) if len(idx) > 1 else None
    bench_ret = _period_return(ts["bench"], first, end) if (len(idx) > 1 and ts["bench"] is not None) else None
    return {
        "twr_pct": ret * 100.0 if ret is not None else None,
        "benchmark_pct": bench_ret * 100.0 if bench_ret is not None else None,
        "excess_pp": (ret - bench_ret) * 100.0 if (ret is not None and bench_ret is not None) else None,
        "start": first.strftime("%Y-%m-%d"),
        "benchmark_name": ts["bench_name"],
    }


def day_change(pid: str) -> Dict[str, Any]:
    """The portfolio's 1-day change for the headline KPI tile: {gain, change_pct, as_of}.

    Just the shortest trailing window, from the same engine as the "Last N days" tiles
    that sit directly beneath it, so the two rows cannot contradict each other.

    It used to be computed separately, as units x nav x (1 - 1/(1 + change_1d_pct/100)),
    which was wrong twice over: it rebuilt yesterday's value from a percentage
    summary_table rounds to 4 decimals (Rs 0.63 out on a real portfolio), and it applied
    today's NAV move to units bought *today*, which had not been held for it. Those are
    both properties of the formula, not bugs in it -- the only durable fix is to have one
    engine rather than two."""
    rc = recent_changes(pid, windows=(1,))
    window = rc["windows"][0] if rc["windows"] else None
    if window is None or not window["available"]:
        # One NAV day of history (or none): there is no previous close to move from.
        return {"gain": 0.0, "change_pct": None, "benchmark_change_pct": None, "as_of": rc.get("as_of")}
    return {"gain": window["gain"], "change_pct": window["change_pct"],
            "benchmark_change_pct": window["benchmark_change_pct"], "as_of": rc["as_of"]}


def gain_shares(gains: List[float]) -> Tuple[List[float], float, float]:
    """Each fund's share of the gains (or of the losses), plus the gross gain and gross loss.

    Shares are taken against the GROSS gains and GROSS losses separately, not against the
    net total. A share of the net is only well-defined while every fund is up: once one
    fund loses money the net shrinks, the winners' shares climb past 100%, and near a net of
    zero they run off to thousands of percent with the sign flipping as it crosses. Split
    this way, winners always sum to 100% of what was made and losers to -100% of what was
    lost -- and while nothing is down, it is identical to the plain share of the total."""
    gross_gain = sum(g for g in gains if g > 0)
    gross_loss = sum(g for g in gains if g < 0)
    shares = [g / gross_gain * 100.0 if g > 0
              else g / -gross_loss * 100.0 if g < 0
              else 0.0
              for g in gains]
    return shares, gross_gain, gross_loss


def performance(pid: str, benchmark: Optional[int] = None) -> Dict[str, Any]:
    ts = timeseries(pid, benchmark)
    if ts["empty"]:
        return {"empty": True}
    idx: pd.DatetimeIndex = ts["index"]
    end = idx[-1]
    first = idx[0]

    periods = []
    for label, days in PERIODS:
        since = days is None
        start = first if since else end - pd.Timedelta(days=days)
        if not since and start < first:
            continue
        twr = _annualise(_period_return(ts["twr"], start, end), start, end)
        bench = _annualise(_period_return(ts["bench"], start, end), start, end) if ts["bench"] is not None else None
        periods.append({
            "label": label,
            "start": start.strftime("%Y-%m-%d"),
            "annualised": (end - start).days >= 365,
            "twr_pct": twr,
            "benchmark_pct": bench,
            "excess_pct": twr - bench if twr is not None and bench is not None else None,
            "xirr_pct": _window_xirr(ts, start, end, since),
        })

    # Attribution since start: each holding's rupee gain = end value - start value -
    # net money put into it (switches included -- at the holding level they are real flows).
    vb, hf = ts["values_by_code"], ts["holding_flows"]
    meta = hdb.scheme_meta(list(vb.columns))
    contrib = []
    for code in vb.columns:
        gain = float(vb[code].iloc[-1] - hf[code].sum())
        contrib.append({"scheme_code": int(code), "scheme_name": hdb.display_name(meta.get(int(code), {"scheme_code": int(code)})),
                        "gain": gain, "end_value": float(vb[code].iloc[-1])})
    total_gain = sum(c["gain"] for c in contrib)
    total_value = sum(c["end_value"] for c in contrib)
    shares, gross_gain, gross_loss = gain_shares([c["gain"] for c in contrib])
    for c, share in zip(contrib, shares):
        c["gain_share_pct"] = share
        # Weight by value today, so a fund you have fully exited reads 0% of your money
        # beside whatever share of the gain it banked on the way out.
        c["weight_pct"] = c["end_value"] / total_value * 100.0 if total_value > 0 else None
    contrib.sort(key=lambda c: -abs(c["gain"]))

    return {
        "empty": False,
        "as_of": end.strftime("%Y-%m-%d"),
        "series": {
            "dates": _dates(idx),
            "value": _series(ts["value"], 2),
            "invested": _series(ts["invested"], 2),
            "twr_index": _series(ts["twr"]),
            "benchmark_index": _series(ts["bench"]),
        },
        "benchmark": {"scheme_code": ts["bench_code"], "scheme_name": ts["bench_name"],
                      "kind": "scheme" if ts["bench_code"] else "category_blend",
                      "components": ts["bench_components"]},
        "periods": periods,
        "attribution": {"total_gain": total_gain, "gross_gain": gross_gain, "gross_loss": gross_loss,
                        "holdings": contrib},
    }


# --- Allocation, targets, rebalancing ---------------------------------------------------------

DRIFT_BAND_PCT = 5.0


def _group(positions: List[Dict[str, Any]], key) -> List[Dict[str, Any]]:
    total = sum(p["current_value"] for p in positions)
    buckets: Dict[str, float] = {}
    for p in positions:
        k = key(p) or "Unknown"
        buckets[k] = buckets.get(k, 0.0) + p["current_value"]
    rows = [{"bucket": k, "value": v, "weight_pct": (v / total * 100.0) if total > 0 else 0.0} for k, v in buckets.items()]
    return sorted(rows, key=lambda r: -r["value"])


def _open_positions(pid: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Open positions tagged with their asset class. New dicts: summary() is a
    shared cached object and must not be mutated."""
    summ = svc.summary(pid)
    positions = [
        {**p, "asset_class": p.get("asset_class") or classify(p.get("category"), p.get("broad_category"), p.get("scheme_name"))}
        for p in summ["positions"] if not p["is_closed"] and p["current_value"] > 0
    ]
    return positions, summ


def _by_portfolio(summ: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    """Household view only: each portfolio's value, share and asset-class mix, so the
    combined picture can be traced back to whose money it is."""
    ids = summ["portfolio_ids"]
    if len(ids) < 2:
        return None
    rows = []
    for pf in ids:
        positions, _ = _open_positions(str(pf))
        value = sum(p["current_value"] for p in positions)
        if value <= 0:
            continue
        meta = hdb.get_portfolio(pf) or {}
        rows.append({
            "portfolio_id": pf,
            "bucket": meta.get("name") or f"Portfolio {pf}",
            "value": value,
            "fund_count": len(positions),
            "by_asset_class": {r["bucket"]: r["weight_pct"] for r in _group(positions, lambda p: p["asset_class"])},
        })
    total = sum(r["value"] for r in rows)
    for r in rows:
        r["weight_pct"] = r["value"] / total * 100.0 if total > 0 else 0.0
    return sorted(rows, key=lambda r: -r["value"])


def allocation(pid: str) -> Dict[str, Any]:
    positions, summ = _open_positions(pid)
    total = sum(p["current_value"] for p in positions)
    weights = np.array([p["current_value"] / total for p in positions]) if total > 0 else np.array([])
    hhi = float((weights ** 2).sum()) if weights.size else None
    top = sorted(weights, reverse=True)
    by_amc = _group(positions, lambda p: p.get("fund_house"))
    out: Dict[str, Any] = {
        "total_value": total,
        # Every weight on this tab is by market value today (units x latest NAV), not by
        # the money paid in: allocation is about where the money IS.
        "basis": "current_value",
        "as_of": summ.get("as_of"),
        "by_asset_class": _group(positions, lambda p: p["asset_class"]),
        "by_category": _group(positions, lambda p: svc.sebi_category(p.get("category"))),
        "by_amc": by_amc,
        "by_plan": _group(positions, lambda p: p.get("plan_type") or "Unspecified"),
        "by_option": _group(positions, lambda p: p.get("option_type") or "Unspecified"),
        "by_portfolio": _by_portfolio(summ),
        "holdings": sorted([
            {"scheme_code": p["scheme_code"], "scheme_name": p["display_name"], "asset_class": p["asset_class"],
             "category": svc.sebi_category(p.get("category")), "fund_house": p.get("fund_house"),
             "riskometer": p.get("riskometer"), "value": p["current_value"], "weight_pct": p.get("weight_pct")}
            for p in positions
        ], key=lambda h: (ASSET_CLASSES.index(h["asset_class"]), -h["value"])),
        # Herfindahl-Hirschman index and its reciprocal, the effective number of funds
        # (the same ENC definition risk_budgeting.compute_risk_budgeting reports).
        "concentration": {
            "hhi": hhi,
            "effective_funds": (1.0 / hhi) if hhi else None,
            "top1_pct": float(top[0] * 100) if top else None,
            "top3_pct": float(sum(top[:3]) * 100) if top else None,
            "fund_count": len(positions),
            # Fund-house concentration is a separate risk from fund concentration: an
            # AMC-level event (Franklin Templeton's 2020 debt wind-up) hits every one of
            # its schemes at once, however many of them you spread across.
            "amc_count": len(by_amc),
            "top_amc": by_amc[0]["bucket"] if by_amc else None,
            "top_amc_pct": by_amc[0]["weight_pct"] if by_amc else None,
        },
        "asset_classes": ASSET_CLASSES,
        "targets": None,
        "drift": None,
        "drift_band_pct": DRIFT_BAND_PCT,
    }
    single = svc.single_portfolio_id(pid)
    if single is not None:
        targets = hdb.get_targets(single)
        out["targets"] = targets or None
        if targets:
            out["drift"] = drift_table(out["by_asset_class"], targets)
    return out


def drift_table(by_class: List[Dict[str, Any]], targets: Dict[str, float]) -> List[Dict[str, Any]]:
    actual = {r["bucket"]: r["weight_pct"] for r in by_class}
    rows = []
    for c in ASSET_CLASSES:
        a, t = actual.get(c, 0.0), float(targets.get(c, 0.0))
        if a == 0 and t == 0:
            continue
        d = a - t
        rows.append({"asset_class": c, "actual_pct": a, "target_pct": t, "drift_pct": d,
                     "status": "over" if d > DRIFT_BAND_PCT else "under" if d < -DRIFT_BAND_PCT else "ok"})
    return rows


def validate_targets(targets: Dict[str, float]) -> Dict[str, float]:
    unknown = [k for k in targets if k not in ASSET_CLASSES]
    if unknown:
        raise svc.LedgerRejected(f"Unknown asset class(es): {', '.join(unknown)}")
    cleaned = {k: round(float(v), 3) for k, v in targets.items() if float(v) > 0}
    if any(v < 0 or v > 100 for v in targets.values()):
        raise svc.LedgerRejected("Each target must be between 0 and 100.")
    total = sum(cleaned.values())
    if cleaned and abs(total - 100.0) > 0.01:
        raise svc.LedgerRejected(f"Targets must add up to 100% (they add up to {total:.2f}%).")
    return cleaned


def rebalance_with_new_money(pid: str, new_money: float) -> Dict[str, Any]:
    """Where to put the next `new_money` rupees to move closest to target.

    Inflows only -- never a sell. Fill each under-weight class's rupee shortfall
    against target at (current + new) total; if the money covers every shortfall,
    split the remainder by target weights. Deterministic and explainable."""
    single = svc.single_portfolio_id(pid)
    if single is None:
        raise svc.LedgerRejected("Targets are set per portfolio; choose one portfolio.")
    targets = hdb.get_targets(single)
    if not targets:
        raise svc.LedgerRejected("Set target allocations for this portfolio first.")
    if new_money <= 0:
        raise svc.LedgerRejected("New money must be positive.")
    positions, _ = _open_positions(pid)
    current: Dict[str, float] = {}
    for p in positions:
        current[p["asset_class"]] = current.get(p["asset_class"], 0.0) + p["current_value"]
    total_after = sum(current.values()) + new_money
    shortfall = {c: max(0.0, total_after * t / 100.0 - current.get(c, 0.0)) for c, t in targets.items()}
    need = sum(shortfall.values())
    if need >= new_money and need > 0:
        alloc = {c: new_money * s / need for c, s in shortfall.items()}
    else:
        remainder = new_money - need
        alloc = {c: shortfall[c] + remainder * t / 100.0 for c, t in targets.items()}

    rows = []
    for c in ASSET_CLASSES:
        if c not in targets and c not in current:
            continue
        amount = alloc.get(c, 0.0)
        # Suggest topping up the largest existing holding in the class, preferring Direct plans.
        in_class = sorted(
            [p for p in positions if p["asset_class"] == c],
            key=lambda p: ("direct" not in str(p.get("plan_type") or "").lower(), -p["current_value"]),
        )
        after = current.get(c, 0.0) + amount
        rows.append({
            "asset_class": c,
            "amount": amount,
            "before_pct": current.get(c, 0.0) / (total_after - new_money) * 100.0 if total_after > new_money else 0.0,
            "after_pct": after / total_after * 100.0,
            "target_pct": float(targets.get(c, 0.0)),
            "suggested_scheme_code": in_class[0]["scheme_code"] if (in_class and amount > 0) else None,
            "suggested_scheme_name": in_class[0]["display_name"] if (in_class and amount > 0) else None,
        })
    return {"new_money": new_money, "rows": rows,
            "note": "Uses new money only; nothing is sold. Where a class has no holding yet, pick a fund for it."}
