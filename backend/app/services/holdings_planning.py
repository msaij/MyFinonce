"""Holdings Phase 4 planning: SIP mandates and goals.

SIP mandates are a *schedule*, not an autopilot. "Generate instalments" turns the
schedule into ordinary ledger rows priced at real AMFI NAVs -- previewed first,
written only on confirm, each tagged with sip_mandate_id so it is never
generated twice. A SIP date that falls on a holiday is allotted at the next
business day's NAV, the way an AMC processes it.

Goals project the linked portfolios forward with the same geometric Brownian
motion model quant_analytics.run_monte_carlo_simulation uses (drift and
volatility of the current mix's daily returns), stepped monthly so a 30-year
goal stays cheap. One observation makes the SIP question exact rather than
iterative: on a fixed set of simulated market paths, each path's terminal value
is linear in an extra monthly SIP,

    terminal_i(s) = A_i + C_i + s * B_i

(A_i: today's value grown along path i; C_i: the recorded mandates' instalments,
each on its own schedule, grown along the same path; B_i: Re 1 a month from today,
grown along it). So the extra SIP that reaches the target on a fraction q of paths
is just the q-quantile of (target - A_i - C_i) / B_i -- and the success probability
is the share of paths with A_i + C_i + s*B_i >= target. Both come from the same
paths, so they always agree.

A goal's amount is stated in rupees of the day it was set (amount_as_of), and is
inflated from that day to the target date -- a fixed number of rupees, not one that
shrinks each day the page is opened closer to the date.
"""

from __future__ import annotations

import calendar
import datetime
import math
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.db import holdings as hdb
from app.services import holdings_risk as risk_svc
from app.services import holdings_service as svc
from app.services.holdings_ledger import D

INSTALMENT_MATCH_DAYS = 7     # an existing SIP row within a week after a scheduled date covers it
GOAL_SIMS = 2000
TRADING_DAYS_PER_MONTH = 21
MONTH_DAYS = 365.25 / 12


# --- SIP mandates ----------------------------------------------------------------------

def _add_months(d: datetime.date, n: int, day: int) -> datetime.date:
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    return datetime.date(y, m, min(day, calendar.monthrange(y, m)[1]))


Pause = Tuple[datetime.date, Optional[datetime.date]]


def _paused(d: datetime.date, pauses: Sequence[Pause]) -> bool:
    """A pause covers paused_from up to (not including) resumed_on; an open one, everything after."""
    return any(start <= d and (resumed is None or d < resumed) for start, resumed in pauses)


def schedule(m: Dict[str, Any], through: datetime.date, pauses: Sequence[Pause] = ()) -> List[Tuple[datetime.date, Decimal]]:
    """Scheduled (date, amount) instalments from start to `through` (inclusive), leaving out
    those that fell inside a pause. Step-up applies on each anniversary of the first
    instalment -- a pause does not move the anniversary."""
    day = int(m["day_of_month"])
    start: datetime.date = m["start_date"]
    first = datetime.date(start.year, start.month, day)
    if first < start:
        first = _add_months(first, 1, day)
    end = m.get("end_date")
    step = D(m.get("step_up_pct") or 0) / 100
    out = []
    k = 0
    while True:
        dte = _add_months(first, k, day)
        if dte > through or (end and dte > end):
            break
        if not _paused(dte, pauses):
            amount = (D(m["amount"]) * (1 + step) ** (k // 12)).quantize(Decimal("0.01"))
            out.append((dte, amount))
        k += 1
    return out


def current_instalment(m: Dict[str, Any], on: Optional[datetime.date] = None) -> Decimal:
    past = schedule(m, on or datetime.date.today())
    return past[-1][1] if past else D(m["amount"])


def upcoming(m: Dict[str, Any], n: int = 3) -> List[Dict[str, Any]]:
    if not m.get("active"):
        return []
    today = datetime.date.today()
    horizon = _add_months(today, n + 1, 28)
    return [{"date": d.isoformat(), "amount": float(a)} for d, a in schedule(m, horizon) if d > today][:n]


def due_instalments(m: Dict[str, Any], pauses: Sequence[Pause]) -> List[Tuple[datetime.date, Decimal]]:
    """Instalments that should have been debited by today: scheduled, and not inside a pause."""
    return schedule(m, datetime.date.today(), pauses)


def pending_instalments(m: Dict[str, Any], pauses: Optional[Sequence[Pause]] = None) -> List[Tuple[datetime.date, Decimal]]:
    """Due instalments with no recorded SIP row yet (a row allotted within a week after the
    scheduled date counts as recorded, whether the mandate wrote it or it was typed in)."""
    recorded = hdb.mandate_installment_dates(m)
    due = due_instalments(m, hdb.mandate_pauses(m["id"]) if pauses is None else pauses)
    return [(d, a) for d, a in due if not any(0 <= (r - d).days <= INSTALMENT_MATCH_DAYS for r in recorded)]


def generate_instalments(mandate_id: int, confirm: bool) -> Dict[str, Any]:
    m = hdb.get_sip_mandate(mandate_id)
    if m is None:
        raise LookupError(f"SIP mandate {mandate_id} not found")
    today = datetime.date.today()
    rows: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for sched_date, amount in pending_instalments(m):
        found = hdb.nav_on_or_after(int(m["scheme_code"]), sched_date)
        if not found or found[0] > today:
            skipped.append({"scheduled_date": sched_date.isoformat(), "reason": "NAV not published yet"})
            continue
        draft = {
            "portfolio_id": m["portfolio_id"], "scheme_code": m["scheme_code"], "txn_type": "SIP",
            "trade_date": found[0], "amount": amount, "apply_stamp_duty": True,
            "notes": f"SIP mandate #{mandate_id}, scheduled {sched_date.isoformat()}",
        }
        (row,), _ = svc.resolve_draft(draft, [])
        rows.append({**row, "sip_mandate_id": mandate_id})

    written = bool(confirm and rows)
    if written:
        svc.insert_validated(m["portfolio_id"], rows)
    return {"mandate_id": mandate_id, "count": len(rows), "total_amount": float(sum((r["amount"] for r in rows), Decimal(0))),
            "rows": [svc.to_json(r) for r in rows], "skipped": skipped, "written": written}


def mandate_view(m: Dict[str, Any]) -> Dict[str, Any]:
    code = int(m["scheme_code"])
    name = hdb.display_name(hdb.scheme_meta([code]).get(code, {"scheme_code": code}))
    pauses = hdb.mandate_pauses(m["id"])
    due, pending = len(due_instalments(m, pauses)), len(pending_instalments(m, pauses))
    paused_since = next((start for start, resumed in reversed(pauses) if resumed is None), None) if not m["active"] else None
    return {**svc.to_json(m), "scheme_name": name, "current_amount": float(current_instalment(m)),
            "instalments_recorded": due - pending, "instalments_pending": pending, "upcoming": upcoming(m),
            "paused_since": paused_since.isoformat() if paused_since else None}


def set_active(before: Dict[str, Any], after: Dict[str, Any]) -> None:
    """Pausing opens a pause from today and resuming closes it, so the months in between are
    never offered for the ledger."""
    if before["active"] != after["active"]:
        hdb.record_pause(after["id"], paused=not after["active"], on=datetime.date.today())


def validate_mandate(fields: Dict[str, Any]) -> None:
    svc.require_open_portfolio(int(fields["portfolio_id"]))
    first = hdb.first_nav_date(int(fields["scheme_code"]))
    if first is None:
        raise svc.LedgerRejected(f"Scheme {fields['scheme_code']} has no NAV history.")
    if fields["start_date"] < first:
        raise svc.LedgerRejected(f"Our NAV history for this scheme starts {first}; the SIP can't start earlier.")


# --- Goals --------------------------------------------------------------------------------

def _simulate(daily_returns: np.ndarray, steps: int, sims: int, flows: Sequence[np.ndarray] = (), seed: int = 42,
              obs_per_year: float = TRADING_DAYS_PER_MONTH * 12, last_step: float = 1.0):
    """Monthly GBM with the per-observation drift/vol aggregated over a month's worth of
    observations -- obs_per_year / 12, measured from the series itself. The last step may
    be a fraction of a month, so the horizon ends on the target date rather than on the
    nearest whole month.

    Time runs over month boundaries 0..steps (boundary 0 is today, `steps` the target date).
    Returns (G, V): G[i, t] = growth of Re 1 held from boundary 0 to t on path i; V[k][i, t] =
    value at boundary t on path i of the cash-flow schedule flows[k], where flows[k][j] rupees
    are invested at boundary j.

    A fixed 21 was right only for a trading-day series. A portfolio holding a liquid fund
    ticks every calendar day (~30 a month), so 21 steps covered ~70% of each month: on the
    owner's portfolio the projected year's median growth was 4.73% against 6.92% realised,
    goal odds too low and the required SIP too high."""
    mu, sigma = float(np.mean(daily_returns)), float(np.std(daily_returns, ddof=1))
    h = np.ones(steps)
    h[-1] = last_step
    n = obs_per_year / 12.0 * h
    f = np.exp((mu - 0.5 * sigma ** 2) * n + sigma * np.sqrt(n) * np.random.default_rng(seed).normal(0, 1, size=(sims, steps)))
    G = np.ones((sims, steps + 1))
    V = [np.zeros((sims, steps + 1)) for _ in flows]
    for v, c in zip(V, flows):
        v[:, 0] = c[0]
    for t in range(1, steps + 1):
        G[:, t] = G[:, t - 1] * f[:, t - 1]
        for v, c in zip(V, flows):
            v[:, t] = v[:, t - 1] * f[:, t - 1] + c[t]
    return G, V


def _sip_flows(mandates: Sequence[Dict[str, Any]], today: datetime.date, until: datetime.date, steps: int) -> np.ndarray:
    """Rupees the mandates will invest at each month boundary 0..steps between today and the
    target date: each one's own schedule -- its start and end dates, its step-up on its own
    anniversary -- with every instalment placed on the nearest boundary."""
    c = np.zeros(steps + 1)
    for m in mandates:
        for dte, amount in schedule(m, until):
            if dte > today:
                c[min(steps, round((dte - today).days / MONTH_DAYS))] += float(amount)
    return c


def _money_weighted_rate(value: float, flows: np.ndarray, years_at: np.ndarray, years: float, terminal: float) -> Optional[float]:
    """The yearly rate r at which today's value, plus each future instalment flows[j] made at
    years_at[j], grows to `terminal` by `years` -- XIRR on the projection. A plain return on
    everything put in would understate it: an instalment a month before the date has had
    almost no time to grow. With no instalments it is just (terminal / value)^(1/years) - 1."""
    if years <= 0 or terminal <= 0 or value + flows.sum() <= 0:
        return None

    def gap(r: float) -> float:
        return value * (1.0 + r) ** years + float(np.sum(flows * (1.0 + r) ** (years - years_at))) - terminal

    lo, hi = -0.99, 10.0
    if gap(lo) > 0 or gap(hi) < 0:
        return None
    for _ in range(200):                 # bisection: gap() rises with r, so this always converges
        mid = (lo + hi) / 2.0
        if gap(mid) < 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def target_in_rupees(goal: Dict[str, Any], on: datetime.date) -> float:
    """The goal's amount, stated in rupees of its amount_as_of day, inflated to `on`."""
    years = max(0, (on - goal["amount_as_of"]).days) / 365.25
    return float(goal["target_amount"]) * (1.0 + float(goal["inflation_pct"]) / 100.0) ** years


def goal_status(goal: Dict[str, Any], extra_sip: Optional[float] = None) -> Dict[str, Any]:
    """Projects the linked portfolios to the target date with their SIP mandates as recorded,
    plus an optional flat `extra_sip` a month (the what-if) invested from today.

    The required SIP is that extra amount: what to add on top of the mandates. Each path's
    terminal value is A_i + C_i + s * B_i (today's value grown, the mandates' instalments grown,
    and Re 1 a month grown), so the s that works on a fraction q of paths is the q-quantile of
    (target - A_i - C_i) / B_i."""
    today = datetime.date.today()
    days_left = (goal["target_date"] - today).days
    years = days_left / 365.25
    base = {"goal": svc.to_json(goal), "years_left": years}
    if not goal["portfolio_ids"]:
        return {**base, "state": "no_portfolios"}
    key = ",".join(str(p) for p in goal["portfolio_ids"])
    kpis = svc.summary(key)["kpis"]
    value = float(kpis["current_value"])
    target_future = target_in_rupees(goal, goal["target_date"])

    # Mandates still running; a paused one is taken to stay paused.
    mandates = [m for m in hdb.list_sip_mandates(goal["portfolio_ids"]) if m["active"]]
    current_sip = sum(float(current_instalment(m)) for m in mandates
                      if m["start_date"] <= today and (m["end_date"] is None or m["end_date"] >= today))
    extra = float(extra_sip or 0.0)
    out = {**base, "current_value": value, "amount_as_of": goal["amount_as_of"].isoformat(),
           "target_today": target_in_rupees(goal, min(today, goal["target_date"])), "target_future": target_future,
           "inflation_pct": float(goal["inflation_pct"]), "current_sip": current_sip, "extra_sip": extra,
           "progress_pct": value / target_future * 100.0 if target_future > 0 else None}
    if days_left <= 0:
        return {**out, "state": "reached" if value >= target_future else "past_due"}

    bc = risk_svc.backcast(key)
    rets = risk_svc.recent_returns(bc).values if not bc["empty"] else []
    if len(rets) < risk_svc.MIN_TRADING_DAYS:
        return {**out, "state": "insufficient_history"}
    months = days_left / MONTH_DAYS
    steps = max(1, math.ceil(months - 1e-9))
    sips = _sip_flows(mandates, today, goal["target_date"], steps)
    unit = np.zeros(steps + 1)
    unit[:steps] = 1.0                 # Re 1 today and on each month after, while before the target date
    G, (C, B) = _simulate(rets, steps, GOAL_SIMS, (sips, unit), obs_per_year=bc["obs_per_year"],
                          last_step=months - (steps - 1))
    A_T, C_T, B_T = value * G[:, -1], C[:, -1], B[:, -1]
    terminal = A_T + C_T + extra * B_T
    need = np.maximum(0.0, (target_future - A_T - C_T) / B_T)
    required = {f"p{int(q * 100)}": float(np.quantile(need, q)) for q in (0.5, 0.75, 0.9)}

    ts = sorted(set(range(0, steps + 1, max(1, steps // 120))) | {steps})   # at most ~120 chart points
    p10, p50, p90 = np.percentile((value * G + C + extra * B)[:, ts], [10, 50, 90], axis=0)
    future_flows = sips + extra * unit
    to_invest = value + np.cumsum(future_flows)[ts]

    # The median outcome as a gain: measured from today's value plus what is still to be
    # invested (the projection starts today, so gains already made are not part of it), and
    # separately against all the cash put in since the first investment.
    median = float(np.median(terminal))
    still_to_invest = float(future_flows.sum())
    money_in = value + still_to_invest
    put_in_total = float(kpis["net_contributed"]) + still_to_invest
    years_at = np.minimum(np.arange(steps + 1), months) / 12.0
    rate = _money_weighted_rate(value, future_flows, years_at, months / 12.0, median)
    low, high = (float(x) for x in np.percentile(terminal, [10, 90]))
    return {
        **out, "state": "projected", "months": months, "n_simulations": GOAL_SIMS,
        "sip_total_to_come": float(sips.sum()),
        "probability_pct": float((terminal >= target_future).mean() * 100.0),
        "median_terminal": median,
        "median_outcome": {
            "money_in": money_in, "still_to_invest": still_to_invest,
            "gain": median - money_in, "gain_pct": (median - money_in) / money_in * 100.0 if money_in > 0 else None,
            "rate_pct": rate * 100.0 if rate is not None else None,
            "p10": low, "p90": high,
            "put_in_total": put_in_total, "gain_since_start": median - put_in_total,
            "vs_target": median - target_future,
        },
        "required_sip": required,
        "projection": {"month": [min(float(t), months) for t in ts], "p10": p10.tolist(), "p50": p50.tolist(),
                       "p90": p90.tolist(), "contributed": to_invest.tolist()},
    }