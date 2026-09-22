"""Holdings analytics: daily value series, TWR index, period returns, attribution,
allocation, targets/drift and new-money rebalancing.

The core artefact is `timeseries()`: the ledger replayed day by day against AMFI
NAVs into (a) the portfolio's rupee value, (b) its external cash flows and
(c) a **time-weighted return index** -- a unitised "portfolio NAV" that starts at
100 and moves only with investment performance, not with deposits/withdrawals:

    r_t = (V_t - F_t) / V_{t-1} - 1        index_t = index_{t-1} * (1 + r_t)

where F_t is the day's net external flow (purchases +, redemptions and dividend
payouts -). Flows are treated as happening at that day's NAV, which is exactly
how a mutual-fund order is allotted, so this daily form is exact rather than an
approximation. Switches between the portfolio's own funds net to zero and are
internal. That index is also what Phase 3 hands to quant_analytics /
factor_model / stress_testing, which all expect a `nav_date`/`nav` frame.

Money-weighted (XIRR) and time-weighted numbers are both shown, side by side, for
the reason portfolio_sim's docstring gives: they answer different questions.
"""

from __future__ import annotations

import datetime
import re
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
# A *total* classification (every scheme lands in exactly one class), unlike the
# Portfolio Suggestion page's sleeves, which are a deliberately conservative
# screening filter (e.g. only "safe" debt categories qualify) and leave most
# schemes unclassified. summary_table.broad_category alone is not enough either:
# its "Other / Index / ETF" bucket mixes Nifty index funds, gilt ETFs, gold ETFs
# and Nasdaq FoFs. Hybrid funds stay "Hybrid" -- splitting them into their equity
# and debt legs needs monthly portfolio disclosures this app does not ingest.

ASSET_CLASSES = ["Equity", "International Equity", "Hybrid", "Debt", "Cash & Liquid", "Gold & Commodities", "Other"]

_INTL = re.compile(r"nasdaq|s&p ?500|sp ?500|nyse|fang|global|international|overseas|world|hang seng|"
                   r"us (equity|bluechip|opportunities|total|specific)|u\.s\.|japan|china|taiwan|europe|greater china|"
                   r"emerging market", re.I)
_GOLD = re.compile(r"\bgold\b|\bsilver\b|commodit", re.I)
_CASH = re.compile(r"liquid fund|overnight fund|money market", re.I)
_DEBT_NAME = re.compile(r"gilt|g-?sec|\bsdl\b|bond|debt|crisil ibx|nifty .*(psu|aaa|sdl|g-sec)|target maturity|"
                        r"treasury|t-?bill|corporate|psu|bharat bond|liquid|money market|overnight|income", re.I)


def classify(category: Optional[str], broad_category: Optional[str], scheme_name: Optional[str]) -> str:
    cat = category or ""
    broad = broad_category or ""
    name = scheme_name or ""
    if _GOLD.search(name) or "Gold ETF" in cat:
        return "Gold & Commodities"
    if "FoF Overseas" in cat or _INTL.search(name):
        return "International Equity"
    if _CASH.search(cat):
        return "Cash & Liquid"
    if broad == "Debt":
        return "Debt"
    if broad == "Hybrid":
        return "Hybrid"
    if broad == "Equity":
        return "Equity"
    if "Index Funds" in cat or "ETF" in cat:
        return "Debt" if _DEBT_NAME.search(name) else "Equity"
    if broad == "Solution Oriented":
        # Retirement/children's funds: their SEBI category leaves the equity/debt mix to
        # the scheme, so the scheme name is the only honest signal we have here.
        return "Debt" if _DEBT_NAME.search(name) else "Hybrid"
    if "FoF Domestic" in cat:
        return "Debt" if _DEBT_NAME.search(name) else "Hybrid"
    return "Other"


# --- Daily time series -------------------------------------------------------------

PORTFOLIO_FLOW_SIGN = {"BUY": 1, "SIP": 1, "REDEEM": -1, "DIVIDEND_PAYOUT": -1}
HOLDING_FLOW_SIGN = {"BUY": 1, "SIP": 1, "SWITCH_IN": 1, "REDEEM": -1, "SWITCH_OUT": -1, "DIVIDEND_PAYOUT": -1}
# Below this a portfolio is treated as empty: a few paise of rounding crumbs left
# after a full redemption must not become the base of a +1,000,000% "return".
MIN_BASE_VALUE = 1.0


def _benchmark_code(portfolio_ids: List[int]) -> Optional[int]:
    if len(portfolio_ids) == 1:
        p = hdb.get_portfolio(portfolio_ids[0])
        if p and p.get("benchmark_scheme_code"):
            return int(p["benchmark_scheme_code"])
    try:
        from app.factor_model import get_factor_proxies

        code = get_factor_proxies().get("factor_proxy_market")
        return int(code) if code else None
    except Exception:
        return None


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
    bench_code = benchmark or _benchmark_code(portfolio_ids)
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
    for t in txns:
        d, code, ttype, amt = _asof_index(idx, t["trade_date"]), int(t["scheme_code"]), t["txn_type"], float(t["amount"])
        unit_delta.at[d, code] += ledger.unit_sign(ttype) * float(ledger.effective_units(t))
        pf_flow.at[d] += PORTFOLIO_FLOW_SIGN.get(ttype, 0) * amt
        hold_flow.at[d, code] += HOLDING_FLOW_SIGN.get(ttype, 0) * amt

    values_by_code = (unit_delta.cumsum().clip(lower=0.0) * held_nav.fillna(0.0)).fillna(0.0)
    value = values_by_code.sum(axis=1)

    prev = value.shift(1).fillna(0.0)
    r = pd.Series(0.0, index=idx)
    has_base = prev >= MIN_BASE_VALUE
    r[has_base] = (value[has_base] - pf_flow[has_base]) / prev[has_base] - 1.0
    fresh = (~has_base) & (pf_flow > 0)
    r[fresh] = value[fresh] / pf_flow[fresh] - 1.0
    twr = 100.0 * (1.0 + r).cumprod()

    bench = None
    bench_name = None
    if bench_code and bench_code in nav.columns:
        b = nav[bench_code].reindex(idx).ffill()
        first_valid = b.first_valid_index()
        if first_valid is not None:
            bench = 100.0 * b / b.loc[first_valid]
            names = nav_long.loc[nav_long["scheme_code"] == bench_code, "scheme_name"]
            bench_name = names.iloc[0] if not names.empty else str(bench_code)

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
        contrib.append({"scheme_code": int(code), "scheme_name": meta.get(int(code), {}).get("scheme_name"),
                        "gain": gain, "end_value": float(vb[code].iloc[-1])})
    total_gain = sum(c["gain"] for c in contrib)
    for c in contrib:
        c["share_pct"] = (c["gain"] / total_gain * 100.0) if abs(total_gain) > 1e-9 else None
    contrib.sort(key=lambda c: -abs(c["gain"]))

    monthly = ts["flows"].groupby(ts["flows"].index.to_period("M"))
    monthly_flows = [
        {"month": str(p), "invested": float(g[g > 0].sum()), "withdrawn": float(-g[g < 0].sum())}
        for p, g in monthly if (g != 0).any()
    ]

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
        "benchmark": {"scheme_code": ts["bench_code"], "scheme_name": ts["bench_name"]},
        "periods": periods,
        "attribution": {"total_gain": total_gain, "holdings": contrib},
        "monthly_flows": monthly_flows,
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
        {**p, "asset_class": classify(p.get("category"), p.get("broad_category"), p.get("scheme_name"))}
        for p in summ["positions"] if not p["is_closed"] and p["current_value"] > 0
    ]
    return positions, summ


def allocation(pid: str) -> Dict[str, Any]:
    positions, summ = _open_positions(pid)
    total = sum(p["current_value"] for p in positions)
    weights = np.array([p["current_value"] / total for p in positions]) if total > 0 else np.array([])
    hhi = float((weights ** 2).sum()) if weights.size else None
    top = sorted(weights, reverse=True)
    out: Dict[str, Any] = {
        "total_value": total,
        "by_asset_class": _group(positions, lambda p: p["asset_class"]),
        "by_category": _group(positions, lambda p: p.get("category")),
        "by_amc": _group(positions, lambda p: p.get("fund_house")),
        "by_plan": _group(positions, lambda p: p.get("plan_type") or "Unspecified"),
        "by_option": _group(positions, lambda p: p.get("option_type") or "Unspecified"),
        "holdings": [
            {"scheme_code": p["scheme_code"], "scheme_name": p["scheme_name"], "asset_class": p["asset_class"],
             "category": p.get("category"), "value": p["current_value"], "weight_pct": p.get("weight_pct")}
            for p in positions
        ],
        # Herfindahl-Hirschman index and its reciprocal, the effective number of funds
        # (the same ENC definition risk_budgeting.compute_risk_budgeting reports).
        "concentration": {
            "hhi": hhi,
            "effective_funds": (1.0 / hhi) if hhi else None,
            "top1_pct": float(top[0] * 100) if top else None,
            "top3_pct": float(sum(top[:3]) * 100) if top else None,
            "fund_count": len(positions),
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
            "suggested_scheme_name": in_class[0]["scheme_name"] if (in_class and amount > 0) else None,
        })
    return {"new_money": new_money, "rows": rows,
            "note": "Uses new money only; nothing is sold. Where a class has no holding yet, pick a fund for it."}
