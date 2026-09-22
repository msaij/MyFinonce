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
is linear in the monthly SIP,

    terminal_i(s) = A_i + s * B_i

(A_i: today's value grown along path i; B_i: a Re 1/month SIP with the step-up
schedule, grown along the same path). So the SIP that reaches the target on a
fraction q of paths is just the q-quantile of (target - A_i) / B_i -- and the
success probability of the current SIP is the share of paths with
A_i + s*B_i >= target. Both come from the same paths, so they always agree.
"""

from __future__ import annotations

import calendar
import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from app.db import holdings as hdb
from app.services import holdings_risk as risk_svc
from app.services import holdings_service as svc
from app.services.holdings_ledger import D

INSTALMENT_MATCH_DAYS = 7     # an existing SIP row within a week after a scheduled date covers it
GOAL_SIMS = 2000
TRADING_DAYS_PER_MONTH = 21


# --- SIP mandates ----------------------------------------------------------------------

def _add_months(d: datetime.date, n: int, day: int) -> datetime.date:
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    return datetime.date(y, m, min(day, calendar.monthrange(y, m)[1]))


def schedule(m: Dict[str, Any], through: datetime.date) -> List[Tuple[datetime.date, Decimal]]:
    """Scheduled (date, amount) instalments from start to `through` (inclusive).
    Step-up applies on each anniversary of the first instalment."""
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


def pending_instalments(m: Dict[str, Any]) -> List[Tuple[datetime.date, Decimal]]:
    """Scheduled instalments up to today with no recorded SIP row yet (a row
    allotted within a week after the scheduled date counts as recorded)."""
    recorded = hdb.mandate_installment_dates(m["id"])
    return [(d, a) for d, a in schedule(m, datetime.date.today())
            if not any(0 <= (r - d).days <= INSTALMENT_MATCH_DAYS for r in recorded)]


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
    name = hdb.scheme_meta([m["scheme_code"]]).get(int(m["scheme_code"]), {}).get("scheme_name")
    return {**svc.to_json(m), "scheme_name": name, "current_amount": float(current_instalment(m)),
            "instalments_recorded": len(hdb.mandate_installment_dates(m["id"])),
            "instalments_pending": len(pending_instalments(m)), "upcoming": upcoming(m)}


def validate_mandate(fields: Dict[str, Any]) -> None:
    svc.require_open_portfolio(int(fields["portfolio_id"]))
    first = hdb.first_nav_date(int(fields["scheme_code"]))
    if first is None:
        raise svc.LedgerRejected(f"Scheme {fields['scheme_code']} has no NAV history.")
    if fields["start_date"] < first:
        raise svc.LedgerRejected(f"Our NAV history for this scheme starts {first}; the SIP can't start earlier.")


# --- Goals --------------------------------------------------------------------------------

def _contributions(months: int, step_up_pct: float) -> np.ndarray:
    """Re 1/month SIP with an annual step-up: c[t-1] is the month-t contribution."""
    return (1.0 + step_up_pct / 100.0) ** (np.arange(months) // 12)


def _simulate(daily_returns: np.ndarray, months: int, sims: int, step_up_pct: float, seed: int = 42):
    """Monthly GBM with the daily drift/vol aggregated over 21 trading days.
    Returns (G, B): G[i, t] = growth of Re 1 held from month 0 to t on path i;
    B[i, t] = value at t of a Re 1/month SIP (with annual step-up) on path i."""
    mu, sigma = float(np.mean(daily_returns)), float(np.std(daily_returns, ddof=1))
    n = TRADING_DAYS_PER_MONTH
    f = np.exp((mu - 0.5 * sigma ** 2) * n + sigma * np.sqrt(n) * np.random.default_rng(seed).normal(0, 1, size=(sims, months)))
    c = _contributions(months, step_up_pct)
    G = np.ones((sims, months + 1))
    B = np.zeros((sims, months + 1))
    for t in range(1, months + 1):
        G[:, t] = G[:, t - 1] * f[:, t - 1]
        B[:, t] = B[:, t - 1] * f[:, t - 1] + c[t - 1]
    return G, B


def goal_status(goal: Dict[str, Any], sip_override: Optional[float] = None) -> Dict[str, Any]:
    today = datetime.date.today()
    years = (goal["target_date"] - today).days / 365.25
    base = {"goal": svc.to_json(goal), "years_left": years}
    if not goal["portfolio_ids"]:
        return {**base, "state": "no_portfolios"}
    key = ",".join(str(p) for p in goal["portfolio_ids"])
    value = float(svc.summary(key)["kpis"]["current_value"])
    infl = float(goal["inflation_pct"]) / 100.0
    target_today = float(goal["target_amount"])
    target_future = target_today * (1.0 + infl) ** max(years, 0.0)

    # Active SIPs feeding the goal: their current instalment, and an amount-weighted step-up.
    sips = [(float(current_instalment(m)), float(m["step_up_pct"])) for m in hdb.list_sip_mandates(goal["portfolio_ids"])
            if m["active"] and (m["end_date"] is None or m["end_date"] >= today)]
    current_sip = sum(a for a, _ in sips)
    step_up = sum(a * s for a, s in sips) / current_sip if current_sip > 0 else 0.0
    sip = current_sip if sip_override is None else float(sip_override)
    out = {**base, "current_value": value, "target_today": target_today, "target_future": target_future,
           "inflation_pct": infl * 100, "current_sip": current_sip, "sip_used": sip, "step_up_pct": step_up,
           "progress_pct": value / target_future * 100.0 if target_future > 0 else None}
    if years <= 0:
        return {**out, "state": "reached" if value >= target_today else "past_due"}

    bc = risk_svc.backcast(key)
    rets = risk_svc.recent_returns(bc).values if not bc["empty"] else []
    if len(rets) < risk_svc.MIN_TRADING_DAYS:
        return {**out, "state": "insufficient_history"}
    months = max(1, int(round(years * 12)))
    G, B = _simulate(rets, months, GOAL_SIMS, step_up)
    A_T, B_T = value * G[:, -1], B[:, -1]
    terminal = A_T + sip * B_T
    need = np.maximum(0.0, (target_future - A_T) / np.where(B_T > 0, B_T, np.nan))
    required = {f"p{int(q * 100)}": float(np.nanquantile(need, q)) for q in (0.5, 0.75, 0.9)}

    ts = sorted(set(range(0, months + 1, max(1, months // 120))) | {months})   # at most ~120 chart points
    p10, p50, p90 = np.percentile((value * G + sip * B)[:, ts], [10, 50, 90], axis=0)
    contributed = value + sip * np.concatenate([[0.0], np.cumsum(_contributions(months, step_up))])[ts]
    return {
        **out, "state": "projected", "months": months, "n_simulations": GOAL_SIMS,
        "probability_pct": float((terminal >= target_future).mean() * 100.0),
        "median_terminal": float(np.median(terminal)),
        "required_sip": required,
        "projection": {"month": ts, "p10": p10.tolist(), "p50": p50.tolist(), "p90": p90.tolist(),
                       "contributed": contributed.tolist()},
    }