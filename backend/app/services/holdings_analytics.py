"""Holdings analytics: daily value series, TWR index, period returns, attribution and
allocation.

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

import logging

from app import quant_analytics
from app.core.cache import NoCache, cached
from app.db import holdings as hdb
from app.db.connection import get_data_version
from app.services import holdings_ledger as ledger
from app.services import holdings_service as svc
from app.services.holdings_config import NAV_PRINT_TOLERANCE, PENDING_NAV_DAYS
from app.services.holdings_ledger import HOLDING_FLOW_SIGN, PORTFOLIO_FLOW_SIGN

logger = logging.getLogger(__name__)

# --- Asset classes -------------------------------------------------------------
#
# Shared with the market Overview, so a fund is classed the same on both pages; see
# app/classification.py for the rules.
from app.classification import ASSET_CLASSES, classify, sebi_category  # noqa: E402,F401  (re-exported)


# --- Daily time series -------------------------------------------------------------

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


def category_daily_returns(categories: List[str], start: datetime.date, end: datetime.date,
                           failed: Optional[List[str]] = None) -> pd.DataFrame:
    """Daily peer-average returns, one column per category. A category whose query failed
    is left out like one with no peers -- but named in `failed`, because left out it reads
    as a zero peer return, which flatters the portfolio by that fund's whole move."""
    cols = {}
    for cat in sorted({c for c in categories if c}):
        try:
            df = quant_analytics.get_synthetic_category_benchmark(cat, start, end, raise_errors=True)
        except Exception:
            logger.exception("Peer returns for %s failed; the peer comparison leaves it out", cat)
            if failed is not None:
                failed.append(cat)
            continue
        if not df.empty:
            cols[cat] = df.set_index("nav_date")["daily_return"]
    return pd.DataFrame(cols)


def blend_index(weights: pd.DataFrame, code_category: Dict[int, str], index: pd.DatetimeIndex,
                last_priced: Optional[Dict[int, pd.Timestamp]] = None,
                failed: Optional[List[str]] = None) -> Optional[pd.Series]:
    """Index (base 100) of sum_i w_i,t * r_category(i),t. `weights` rows are the
    weights to apply on each date (they need not sum to 1 before normalisation).
    Days a category has no peer return count as 0 for that slice, and so do the days after
    a fund's own last published NAV (`last_priced`): the portfolio carries that fund
    unchanged until AMFI publishes it, so its peers must not be credited those days either."""
    if weights.empty or index.empty:
        return None
    cat_r = category_daily_returns(list(code_category.values()), index[0].date(), index[-1].date(), failed).reindex(index).fillna(0.0)
    if cat_r.empty:
        return None
    w = weights.reindex(index).fillna(0.0)
    w = w.div(w.sum(axis=1).replace(0.0, np.nan), axis=0).fillna(0.0)
    last_priced = last_priced or {}

    def peer_r(code: int, cat: str) -> pd.Series:
        r_cat = cat_r[cat]
        return r_cat.where(r_cat.index <= last_priced[code], 0.0) if code in last_priced else r_cat

    r = sum(w[code] * peer_r(code, cat) for code, cat in code_category.items() if cat in cat_r.columns and code in w.columns)
    if isinstance(r, int):      # no holding had a category series
        return None
    r.iloc[0] = 0.0             # the index starts at 100 on the first date
    return 100.0 * (1.0 + r).cumprod()


def blend_components(weights_now: Dict[int, float], code_category: Dict[int, str]) -> List[Dict[str, Any]]:
    """The blend's make-up by SEBI category. Grouped by sebi_category, not AMFI's raw label:
    AMFI files one category under two labels ("Debt Scheme - Liquid Fund" and the legacy
    "Income/Debt Oriented Schemes - Liquid Fund"), which printed "Liquid Fund 77% · Liquid
    Fund 13%" for one 90% liquid sleeve."""
    by_cat: Dict[str, float] = {}
    for code, w in weights_now.items():
        cat = sebi_category(code_category.get(code)) or "Unknown"
        by_cat[cat] = by_cat.get(cat, 0.0) + w
    total = sum(by_cat.values()) or 1.0
    return sorted(({"category": c, "weight_pct": v / total * 100.0} for c, v in by_cat.items()), key=lambda x: -x["weight_pct"])


def _new_money_growth(new_money: List[Tuple[pd.Timestamp, pd.Timestamp, float, float, int]], bench_code: Optional[int],
                      nav: pd.DataFrame, code_cat: Optional[Dict[int, str]], idx: pd.DatetimeIndex,
                      failed: Optional[List[str]] = None) -> List[float]:
    """For each flow, how its benchmark grew from the allotment date to the day the money
    enters the book (1.0 when it was allotted that same day, as most are)."""
    if not new_money:
        return []
    if bench_code is not None:
        b = nav[bench_code].dropna()

        def level(day: pd.Timestamp) -> float:
            v = b.asof(day) if len(b) and day >= b.index[0] else np.nan
            return float(v) if v == v else np.nan

        out = []
        for moved, alloc, _net, _cash, _code in new_money:
            lo, hi = level(alloc), level(moved)
            out.append(hi / lo if (lo == lo and hi == hi and lo > 0) else 1.0)
        return out
    earliest = min(alloc for _m, alloc, _n, _c, _k in new_money)
    cats = category_daily_returns(list((code_cat or {}).values()), earliest.date(), idx[-1].date(), failed)
    cats.index = pd.to_datetime(cats.index)
    out = []
    for moved, alloc, _net, _cash, code in new_money:
        cat = (code_cat or {}).get(code)
        if moved <= alloc or cat not in cats.columns:
            out.append(1.0)
            continue
        span = cats[cat].loc[(cats.index > alloc) & (cats.index <= moved)].dropna()
        out.append(float((1.0 + span).prod()))
    return out


def _allotment_date(raw: Optional[pd.Series], trade_date: datetime.date, txn_nav: Any) -> pd.Timestamp:
    """The date whose NAV a purchase was allotted at: the latest published NAV on or before
    the trade date (at most a week back) that equals the transaction's NAV, else the trade
    date. Liquid and overnight funds allot at the PREVIOUS day's NAV, so money in on the
    15th is already credited the 15th's accrual -- a peer comparison that buys the
    benchmark at the 15th's close would miss that day and flatter the portfolio."""
    day = pd.Timestamp(trade_date)
    try:
        target = float(txn_nav)
    except (TypeError, ValueError):
        return day
    if raw is None or not target:
        return day
    window = raw.loc[day - pd.Timedelta(days=7):day]
    # AMCs and apps round the NAV they print; a tenth of a paisa either way is a match.
    matches = window[(window - target).abs() <= max(NAV_PRINT_TOLERANCE, target * 1e-7)]
    return matches.index[-1] if len(matches) else day


def _asof_index(index: pd.DatetimeIndex, day: datetime.date) -> pd.Timestamp:
    """The NAV date a trade on `day` was allotted at: the last index date on or
    before it (weekend/holiday -> previous NAV, matching nav_on_or_before)."""
    ts = pd.Timestamp(day)
    pos = index.searchsorted(ts, side="right") - 1
    return index[max(pos, 0)]


@cached(ttl=600, maxsize=64)
def _timeseries_cached(pid_key: str, ledger_v: int, data_v: int) -> Dict[str, Any]:
    from app.db import queries as db  # read-only use of the façade

    portfolio_ids = svc.resolve_portfolio_ids(pid_key)
    results, txns = svc.replay_view(portfolio_ids)
    # The rows the ledger itself rejects (a redemption of units never held) are left out
    # here too, so this series describes the same ledger as the Holdings table and tiles.
    rejected = {e.txn_id for res in results.values() for e in res.errors}
    txns = [t for t in txns if t["id"] not in rejected]
    if not txns:
        return {"empty": True, "portfolio_ids": portfolio_ids}

    codes = sorted({int(t["scheme_code"]) for t in txns})
    first = min(t["trade_date"] for t in txns)
    bench_code = explicit_benchmark(portfolio_ids)
    load_codes = codes + ([bench_code] if bench_code and bench_code not in codes else [])
    nav_long = db.get_nav_history_dataframe(load_codes, start_date=first - datetime.timedelta(days=15))
    if nav_long.empty:
        return {"empty": True, "portfolio_ids": portfolio_ids}
    # A new frame, not an in-place edit: the loader is cached, and its result is shared.
    nav_long = nav_long.assign(nav_date=pd.to_datetime(nav_long["nav_date"]))
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
    # Switch money between the two legs: out of fund A on the SWITCH_OUT date, into fund B
    # on the SWITCH_IN date (a day or two later when B's NAV date comes later). It never
    # left the portfolio, so it stays in the book as cash meanwhile -- else a full switch
    # would read as a -100% day and leave the index at zero for good.
    in_transit = pd.Series(0.0, index=idx)
    # Every external flow as the peers must take it: (the index day the cash enters the
    # book, the date whose NAV it was allotted at -- a liquid fund's previous-day NAV, which
    # may even precede the index -- the rupees that buy units, i.e. net of stamp duty, the
    # signed cash itself, and the fund).
    new_money: List[Tuple[pd.Timestamp, pd.Timestamp, float, float, int]] = []
    raw_nav = {c: g.set_index("nav_date")["nav"].astype(float) for c, g in nav_long.groupby("scheme_code")}
    for t in txns:
        sign = PORTFOLIO_FLOW_SIGN.get(t["txn_type"], 0)
        if sign:
            code = int(t["scheme_code"])
            net = sign * float(t["amount"]) - (float(t.get("stamp_duty") or 0.0) if sign > 0 else 0.0)
            alloc = _allotment_date(raw_nav.get(code), t["trade_date"], t.get("nav")) if sign > 0 else pd.Timestamp(t["trade_date"])
            new_money.append((_asof_index(idx, t["trade_date"]), alloc, net, sign * float(t["amount"]), code))
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
        if ttype in ledger.SWITCH_LEGS and t.get("switch_group") is not None:
            in_transit.at[d] += amt if ttype == "SWITCH_OUT" else -amt

    units_held = unit_delta.cumsum().clip(lower=0.0)
    values_by_code = (units_held * held_nav.fillna(0.0)).fillna(0.0)
    value = values_by_code.sum(axis=1) + in_transit.cumsum().clip(lower=0.0)

    # Each held fund's newest published NAV. The index runs to the newest NAV of ANY held
    # fund, with the others carried forward: a fund AMFI has not priced yet for the last
    # day(s) contributes no move for them until it publishes (the whole series is rebuilt
    # then). Every published move is kept -- stopping the portfolio at the last date all
    # funds share would throw away real, already-published moves (an arbitrage fund's
    # expiry-day dip, say) to wait for a day's liquid accrual. The
    # peer blend below is made to skip the same pending days, fund by fund, so the
    # comparison stays like for like. A fund silent for over a week is stale (merged,
    # wound up) and is not reported as pending.
    latest_by_code = nav_long.groupby("scheme_code")["nav_date"].max()
    newest = idx[-1]
    held_now = [c for c in codes if units_held[c].iloc[-1] > 0]
    awaiting = [c for c in held_now if c in latest_by_code
                and newest - pd.Timedelta(days=PENDING_NAV_DAYS) <= latest_by_code[c] < newest]

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
    # shortfall to V_{t-1}. A portfolio opening with a token Rs 1,500 that then takes in a
    # deposit hundreds of times larger has ~Rs 20 of purchase friction measured against the
    # Rs 1,500 base: about -1.3% for the day, which then dominated every window containing it
    # -- a red percentage headline above a green rupee gain. Chain-linking charges it to the
    # base that actually bore it (pre_t + F_t), where it is a couple of thousandths of a percent.
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

    bench, bench_name, components, code_cat = None, None, [], None
    peer_failures: List[str] = []
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
        bench = blend_index(weights, code_cat, idx, last_priced=latest_by_code.to_dict(), failed=peer_failures)
        if bench is not None:
            bench_name = BLEND_NAME
            last = values_by_code.iloc[-1]
            components = blend_components({c: float(last[c]) for c in codes if last[c] > 0}, code_cat)

    # --- The peers, handed your new money exactly as you invested it -----------------------
    #
    # The benchmark above moves the book you held YESTERDAY (sub-period r1 of the split). On
    # a day money comes in, the portfolio's own figure also carries sub-period r2: the new
    # money's stamp duty and unit rounding, and -- for a liquid fund, allotted at the
    # previous day's NAV -- the day's accrual it has already earned. Left out of the
    # benchmark, every purchase day flattered the portfolio by that accrual and charged it
    # the stamp duty the peers never paid (0.01 pp on the owner's since-start comparison,
    # enough to move the printed figure). So the peers take the same cash, net of the same
    # stamp duty, allotted on the same date, and grow it until the day it enters the book at
    # the NEW FUND'S own benchmark -- its category (or the chosen benchmark scheme), not the
    # mix of the funds already held. The same pieces build a peer book in rupees for
    # since_start's comparison.
    peer_book = None
    if bench is not None:
        growth = _new_money_growth(new_money, bench_code if bench_name != BLEND_NAME else None, nav, code_cat, idx,
                                   failed=peer_failures)
        r_b = (bench / bench.shift(1) - 1.0).fillna(0.0)
        extra = pd.Series(0.0, index=idx)          # net x growth - cash, by entry day
        into_peers = pd.Series(0.0, index=idx)     # net x growth, by entry day
        for (moved, _alloc, net, cash, _code), g in zip(new_money, growth):
            extra.at[moved] += net * g - cash
            into_peers.at[moved] += net * g
        r2b = pd.Series(0.0, index=idx)
        r2b[funded] = extra[funded] / post_flow[funded]
        missing = bench.isna()
        bench = 100.0 * ((1.0 + r_b) * (1.0 + r2b)).cumprod()
        bench[missing] = np.nan
        book, running = [], 0.0
        for day in idx:
            running = running * (1.0 + float(r_b.at[day])) + float(into_peers.at[day])
            book.append(running)
        peer_book = pd.Series(book, index=idx)

    result = {
        "empty": False,
        "portfolio_ids": portfolio_ids,
        "index": idx,
        "value": value,
        "flows": pf_flow,
        "invested": pf_flow.cumsum(),
        "twr": twr,
        "values_by_code": values_by_code,
        "peer_book": peer_book,
        "awaiting_codes": awaiting,
        "holding_flows": hold_flow,
        "nav": held_nav,
        "bench_code": bench_code if bench is not None else None,
        "bench_name": bench_name,
        "bench_components": components,
        "bench": bench,
        # Categories whose peer returns failed to load: the comparison leaves them out.
        "bench_incomplete": sorted(set(peer_failures)),
    }
    # Shown, but not kept: once the peer query recovers, the next request computes it whole.
    return NoCache(result) if peer_failures else result


def timeseries(pid: str) -> Dict[str, Any]:
    """Cached per (view, ledger version, market-data version). Read-only."""
    return _timeseries_cached(svc.view_key(pid), svc.ledger_version(), get_data_version())


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
RECENT_WINDOWS: Tuple[int, ...] = (2, 7, 10)


def recent_changes(pid: str, windows: Tuple[int, ...] = RECENT_WINDOWS) -> Dict[str, Any]:
    """How the holdings themselves moved over the last N NAV days, per window.

    Time-weighted, not the raw change in portfolio value: money paid in during the window
    would otherwise read as a gain. The rupee figure is the market gain on that basis --
    each day's value change minus that day's own contribution or withdrawal."""
    ts = timeseries(pid)
    if ts["empty"]:
        return {"empty": True, "windows": []}

    idx: pd.DatetimeIndex = ts["index"]
    level, bench = ts["twr"], ts["bench"]
    end = idx[-1]
    market_gain = _market_gain(ts)

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
    return {"empty": False, "as_of": end.strftime("%Y-%m-%d"), "benchmark_name": ts["bench_name"], "windows": out,
            **_pending_nav_info(ts)}


def _market_gain(ts: Dict[str, Any]) -> pd.Series:
    """Daily market gain in rupees: what the holdings did, with the day's own flow removed.

    Day one has no previous value: the book went from nothing to V_0 while the first
    purchase paid in F_0, so its gain is V_0 - F_0 (the stamp duty and unit rounding), not
    -F_0. Summed over every day this telescopes to value + money out - money in: Total gain."""
    value = ts["value"]
    return value - value.shift(1).fillna(0.0) - ts["flows"].reindex(ts["index"]).fillna(0.0)


def _level_change(level: Optional[pd.Series], base: Optional[pd.Timestamp], end: pd.Timestamp) -> Optional[float]:
    """level[end] over level[base]; over 100, the level before the first day, when base is
    None (the period opens before the portfolio did)."""
    if level is None:
        return None
    if base is not None:
        return _period_return(level, base, end)
    last = float(level.loc[end])
    return last / 100.0 - 1.0 if np.isfinite(last) else None


def monthly_earnings(pid: str) -> Dict[str, Any]:
    """What the holdings earned in each calendar month of the newest NAV's year.

    A month runs from the last NAV of the month before to its own last NAV (to date for the
    newest month). The rupees are the market gain on the recent-change tiles' basis --
    money paid in or taken out is not earnings -- and the percentage is time-weighted, so
    every month's rupees since the first investment add up to Total gain. Months before the
    first investment are left out. The year follows the newest NAV, not the calendar: on
    1 January, before any NAV of the new year, the months on offer are last year's."""
    ts = timeseries(pid)
    if ts["empty"]:
        return {"empty": True, "months": []}

    idx: pd.DatetimeIndex = ts["index"]
    level, bench = ts["twr"], ts["bench"]
    end = idx[-1]
    market_gain = _market_gain(ts)

    months = []
    for m in range(1, end.month + 1):
        first_day = pd.Timestamp(end.year, m, 1)
        rows = idx[(idx >= first_day) & (idx < first_day + pd.offsets.MonthBegin(1))]
        if rows.empty:
            continue
        before = idx[idx < first_day]
        base = before[-1] if len(before) else None
        ret = _level_change(level, base, rows[-1])
        bench_ret = _level_change(bench, base, rows[-1])
        months.append({
            "month": first_day.strftime("%Y-%m"),
            # The NAV date the month is measured from; None for the month the portfolio opened.
            "start": base.strftime("%Y-%m-%d") if base is not None else None,
            "end": rows[-1].strftime("%Y-%m-%d"),
            "nav_days": len(rows),
            "gain": float(market_gain.loc[rows].sum()),
            "change_pct": ret * 100.0 if ret is not None else None,
            "benchmark_change_pct": bench_ret * 100.0 if bench_ret is not None else None,
            "excess_pp": (ret - bench_ret) * 100.0 if (ret is not None and bench_ret is not None) else None,
        })
    return {"empty": False, "year": end.year, "as_of": end.strftime("%Y-%m-%d"), "benchmark_name": ts["bench_name"],
            "months": months}


def _pending_nav_info(ts: Dict[str, Any]) -> Dict[str, Any]:
    """Which held funds have not yet published the newest NAV date, for the caption that
    says their move for it is still to come."""
    newest = ts["index"][-1]
    last_values = ts["values_by_code"].iloc[-1]
    total = float(last_values.sum()) or 1.0
    codes = ts["awaiting_codes"]
    return {
        "latest_nav_date": newest.strftime("%Y-%m-%d"),
        "awaiting_funds": len(codes),
        "awaiting_value_pct": float(sum(last_values[c] for c in codes)) / total * 100.0,
    }


def since_start(pid: str) -> Dict[str, Any]:
    """Time-weighted return since the first investment, beside the peer benchmark.

    This is the headline answer XIRR cannot give for a young portfolio: XIRR is withheld
    for the first MIN_XIRR_DAYS because annualising a few days' return exaggerates it, but
    "how have my funds done so far, and did they beat the funds they compete with" has an
    honest answer from day two. Same basis as the Performance tab's "Since start" row."""
    ts = timeseries(pid)
    if ts["empty"]:
        return {"twr_pct": None, "benchmark_pct": None, "excess_pp": None, "start": None, "benchmark_name": None,
                "as_of": None, "own_gain": None, "peer_gain": None, "gain_vs_peers": None, "peer_incomplete": []}
    idx: pd.DatetimeIndex = ts["index"]
    first, end = idx[0], idx[-1]
    ret = _period_return(ts["twr"], first, end) if len(idx) > 1 else None
    bench_ret = _period_return(ts["bench"], first, end) if (len(idx) > 1 and ts["bench"] is not None) else None
    return {
        "twr_pct": ret * 100.0 if ret is not None else None,
        "benchmark_pct": bench_ret * 100.0 if bench_ret is not None else None,
        "excess_pp": (ret - bench_ret) * 100.0 if (ret is not None and bench_ret is not None) else None,
        "start": first.strftime("%Y-%m-%d"),
        "as_of": end.strftime("%Y-%m-%d"),
        "benchmark_name": ts["bench_name"],
        "peer_incomplete": ts.get("bench_incomplete", []),
        **_versus_peers_in_rupees(ts, end),
    }


def _versus_peers_in_rupees(ts: Dict[str, Any], end: pd.Timestamp) -> Dict[str, Optional[float]]:
    """Your gain beside what the same cash, paid in on the same days, would have made in
    the peer benchmark.

    The time-weighted comparison beside it scores each day equally, whatever was invested
    that day -- right for judging the funds, misleading for a young portfolio. One that
    opens with a token amount for a fortnight, lagging its peers by 0.16 pp, and only then
    takes in the real money gets that fortnight chained in at full weight: the whole
    portfolio reads "0.14 pp behind peers" when, from the day the money went in, it was
    ahead. In rupees every day counts by the money actually at work. The peers pay the same stamp duty on each
    purchase, buy at the same allotment date (a liquid fund's previous-day NAV), and a
    withdrawal sells the same rupees' worth of the benchmark."""
    book = ts["peer_book"]
    if book is None:
        return {"own_gain": None, "peer_gain": None, "gain_vs_peers": None}
    paid_in = float(ts["flows"].loc[:end].sum())
    own_gain = float(ts["value"].loc[end]) - paid_in
    peer_gain = float(book.loc[end]) - paid_in
    return {"own_gain": own_gain, "peer_gain": peer_gain, "gain_vs_peers": own_gain - peer_gain}


def day_change(positions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The 1-day KPI tile: {gain, change_pct, benchmark_change_pct}.

    Exactly the sum of the Holdings table's 1D column -- each fund's own latest NAV move on
    the units held before it (holdings_service._position_row), which is also how brokers
    show a day's change. When AMFI has priced only some funds for the newest date, a fund
    still on the day before contributes its own latest move, not zero, and nothing already
    published is dropped. The percentage is on what those units were worth before their
    move; the peer figure is each fund's SEBI category over that same fund's last move,
    weighted the same way."""
    held = [p for p in positions if p.get("day_base")]
    gain = float(sum(p["day_change"] for p in positions))
    base = float(sum(p["day_base"] for p in held))
    if base <= 0:
        return {"gain": gain, "change_pct": None, "benchmark_change_pct": None, "peer_incomplete": []}
    failed: List[str] = []
    peer = _day_peer_pct(held, base, failed)
    return {"gain": gain, "change_pct": gain / base * 100.0, "benchmark_change_pct": peer, "peer_incomplete": sorted(set(failed))}


def _day_peer_pct(held: List[Dict[str, Any]], base: float, failed: Optional[List[str]] = None) -> Optional[float]:
    from app.db import queries as db  # read-only use of the façade

    latest = {int(p["scheme_code"]): pd.Timestamp(p["latest_date"]) for p in held if p.get("latest_date")}
    if not latest:
        return None
    start = min(latest.values()) - pd.Timedelta(days=20)
    nav = db.get_nav_history_dataframe(list(latest), start_date=start.date())
    if nav.empty:
        return None
    nav = nav.assign(nav_date=pd.to_datetime(nav["nav_date"]))     # the loader's frame is shared
    cats = category_daily_returns([p.get("category") for p in held], start.date(), max(latest.values()).date(), failed)
    total, covered = 0.0, 0.0
    for p in held:
        code, cat = int(p["scheme_code"]), p.get("category")
        if code not in latest or cat not in cats.columns:
            continue
        dates = nav.loc[(nav["scheme_code"] == code) & (nav["nav_date"] < latest[code]), "nav_date"]
        if dates.empty:
            continue
        span = cats[cat].loc[(cats.index > dates.max()) & (cats.index <= latest[code])].dropna()
        total += p["day_base"] * float((1.0 + span).prod() - 1.0)
        covered += p["day_base"]
    return total / covered * 100.0 if covered > 0 else None


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


def performance(pid: str) -> Dict[str, Any]:
    ts = timeseries(pid)
    if ts["empty"]:
        return {"empty": True}
    idx: pd.DatetimeIndex = ts["index"]
    first = idx[0]
    end = idx[-1]

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


# --- Allocation ---------------------------------------------------------------------------------

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
    }
    return out
