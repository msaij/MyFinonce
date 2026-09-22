"""Holdings page composition: draft resolution, validated ledger writes, valuation.

Three jobs, each built on something that already exists rather than new math:

1. **Resolve a draft** the user typed (fund, type, date, amount *or* units, NAV
   optional) into ledger rows: AMFI NAV auto-filled for the trade date (or the
   last NAV before it, said out loud), stamp duty, units <-> amount.
2. **Validate by replay.** Every write -- add, edit, delete, restore, SIP
   generation -- replays the affected portfolio's whole ledger through
   services.holdings_ledger with the change applied, and is refused if any
   transaction (including a *later* one) would become impossible, e.g. deleting
   a purchase that a later redemption depended on.
3. **Value** positions against the latest AMFI NAV, with XIRR from
   portfolio_sim.xirr -- the same solver Compare & Simulate already uses.

Unit splits: nav_history is split-adjusted in place (queries.normalize_nav_splits)
and keeps no record of when a split happened. A statement NAV typed for a date
before a split is therefore on a different scale than our stored NAV for that
date. `split_scale` detects that from the ratio itself and rescales the units, so
value = units x today's NAV stays right. Auto-filled NAVs are already on the
adjusted scale and are never rescaled.
"""

from __future__ import annotations

import contextlib
import csv
import datetime
import io
import threading
import uuid
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, Iterator, List, Optional, Sequence, Set, Tuple

from app import portfolio_sim
from app.core.cache import cached
from app.db import holdings as hdb
from app.db.connection import get_data_version
from app.services import holdings_ledger as ledger
from app.services.holdings_ledger import D

# Stamp duty on mutual fund purchases: 0.005%, effective 2020-07-01, levied on
# purchases, SIP instalments, switch-ins and dividend reinvestments.
STAMP_DUTY_RATE = Decimal("0.00005")
STAMP_DUTY_FROM = datetime.date(2020, 7, 1)

# Mirrors db/queries.py's _SPLIT_FACTORS / _SPLIT_TOLERANCE (private to a file
# under refactor freeze, so not imported). test_holdings.py asserts they match.
SPLIT_FACTORS = [2, 3, 4, 5, 10, 20, 25, 50, 100, 200, 500, 1000]
SPLIT_CANDIDATES = sorted(set(SPLIT_FACTORS) | {round(1.0 / f, 10) for f in SPLIT_FACTORS})
SPLIT_TOLERANCE = 0.05
# A user NAV this far from AMFI's (and not a split) earns a visible warning.
NAV_DEVIATION_WARN = 0.005

# XIRR annualises; over a few days it turns a 1% move into a three-digit rate.
MIN_XIRR_DAYS = 30

UNITS_Q = Decimal("0.001")      # AMCs allot to 3 decimals
STORED_UNITS_Q = Decimal("0.000001")
MONEY_Q = Decimal("0.01")
NAV_Q = Decimal("0.0001")


class LedgerRejected(Exception):
    """A write that would leave the ledger impossible. `errors` are LedgerError dicts."""

    def __init__(self, message: str, errors: Optional[List[Dict[str, Any]]] = None):
        super().__init__(message)
        self.errors = errors or []


def _q(value: Decimal, quantum: Decimal) -> Decimal:
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def _today() -> datetime.date:
    return datetime.date.today()


# --- Ledger version & write serialisation ----------------------------------------------
#
# _LEDGER_LOCK serialises validate-then-write so two concurrent saves can't each
# pass validation against a ledger the other is about to change (single worker
# process, see Dockerfile, so an in-process lock is enough).
#
# The ledger version is part of every holdings cache key, alongside
# connection.get_data_version() (which moves on each AMFI sync). Deliberately NOT
# bump_data_version(): that wipes every cache app-wide and tells open UI sessions
# the *market* data changed.

_LEDGER_LOCK = threading.Lock()
_LEDGER_VERSION = 0
_LEDGER_VERSION_LOCK = threading.Lock()


def bump_ledger_version() -> int:
    global _LEDGER_VERSION
    with _LEDGER_VERSION_LOCK:
        _LEDGER_VERSION += 1
        return _LEDGER_VERSION


def ledger_version() -> int:
    with _LEDGER_VERSION_LOCK:
        return _LEDGER_VERSION


@contextlib.contextmanager
def ledger_write() -> Iterator[None]:
    """Hold the ledger lock for a validate-then-write, and invalidate holdings
    caches once the write has succeeded (not if it raised)."""
    with _LEDGER_LOCK:
        yield
        bump_ledger_version()


# --- Split detection -------------------------------------------------------------

def _is_clean_split_ratio(ratio: float) -> bool:
    return ratio > 0 and any(abs(ratio - c) / c < SPLIT_TOLERANCE for c in SPLIT_CANDIDATES)


def split_scale(txn_nav: Any, amfi_nav: Optional[float]) -> Tuple[Decimal, Optional[float]]:
    """(units_scale, deviation) for a user-entered NAV against AMFI's NAV for the
    same date. units_scale is 1 unless the two differ by a clean split factor.
    deviation is the fractional difference when it is NOT a split (else None)."""
    if amfi_nav is None or amfi_nav <= 0:
        return Decimal(1), None
    k = amfi_nav / float(txn_nav)
    if abs(k - 1.0) > SPLIT_TOLERANCE and _is_clean_split_ratio(k):
        return D(1) / D(round(k, 10)), None
    return Decimal(1), k - 1.0


def with_split_scales(txns: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Copies of `txns` carrying units_scale (and _nav_deviation for user NAVs)."""
    rows = [dict(t) for t in txns]
    user_rows = [t for t in rows if t.get("nav_source") == "user"]
    amfi = hdb.navs_on_dates((t["scheme_code"], t["trade_date"]) for t in user_rows)
    for t in rows:
        t["units_scale"], t["_nav_deviation"] = Decimal(1), None
    for t in user_rows:
        t["units_scale"], t["_nav_deviation"] = split_scale(t["nav"], amfi.get((int(t["scheme_code"]), t["trade_date"])))
    return rows


# --- Draft resolution ---------------------------------------------------------------

def resolve_draft(draft: Dict[str, Any], existing: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Turns one user draft into 1 (or 2, for a switch) ledger rows.

    `existing` is the portfolio's current non-deleted ledger (used only to know
    how many units are held, for "redeem all"). Raises LedgerRejected for input
    that can't form a transaction at all; returns (rows, warnings) otherwise."""
    code = int(draft["scheme_code"])
    if draft["trade_date"] > _today():
        raise LedgerRejected("Trade date is in the future.")
    if not hdb.scheme_exists(code):
        raise LedgerRejected(f"Scheme {code} is not in the AMFI scheme master.")
    if draft["txn_type"] == "SWITCH":
        return _resolve_switch(draft, existing)
    return _resolve_single(draft, existing)


def _resolve_switch(draft: Dict[str, Any], existing: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """A switch is a SWITCH_OUT of one fund and a SWITCH_IN of the proceeds into
    another, linked by a shared switch_group so they are deleted together."""
    target = draft.get("switch_to_scheme_code")
    if not target:
        raise LedgerRejected("A switch needs a target fund.")
    if int(target) == int(draft["scheme_code"]):
        raise LedgerRejected("Switch source and target are the same fund.")
    (out,), w_out = resolve_draft({**draft, "txn_type": "SWITCH_OUT"}, existing)
    (into,), w_in = resolve_draft({
        "portfolio_id": draft["portfolio_id"], "scheme_code": int(target), "txn_type": "SWITCH_IN",
        "trade_date": draft.get("switch_in_date") or draft["trade_date"], "amount": out["amount"],
        "nav": draft.get("switch_in_nav"), "apply_stamp_duty": draft.get("apply_stamp_duty", True),
        "notes": draft.get("notes"),
    }, existing)
    out["switch_group"] = into["switch_group"] = uuid.uuid4()
    return [out, into], w_out + w_in


def _resolve_nav(code: int, trade_date: datetime.date, typed: Any) -> Tuple[Decimal, str, Optional[str]]:
    """(nav, nav_source, warning). The user's statement NAV wins; otherwise AMFI's
    NAV for the date, or the last one before it (said out loud)."""
    if typed not in (None, ""):
        nav = _q(D(typed), NAV_Q)
        if nav <= 0:
            raise LedgerRejected("NAV must be positive.")
        return nav, "user", None
    found = hdb.nav_on_or_before(code, trade_date)
    if not found:
        raise LedgerRejected("No AMFI NAV on or before the trade date.")
    nav_date, nav_f = found
    nav = _q(D(nav_f), NAV_Q)
    warning = None
    if nav_date != trade_date:
        warning = (f"No NAV was published on {trade_date.isoformat()} (weekend or market holiday); "
                   f"used the {nav_date.isoformat()} NAV of {nav}.")
    return nav, "amfi_auto", warning


def _purchase(amount: Optional[Decimal], units: Optional[Decimal], nav: Decimal, stamp_on: bool) -> Tuple[Decimal, Decimal, Decimal]:
    """(amount, units, stamp_duty) for an inflow given amount OR units. Stamp duty
    comes out of the amount paid, so fewer units are allotted."""
    if amount is None and units is None:
        raise LedgerRejected("Enter an amount or a number of units.")
    rate = STAMP_DUTY_RATE if stamp_on else Decimal(0)
    if amount is not None:
        if amount <= 0:
            raise LedgerRejected("Amount must be positive.")
        stamp = _q(amount - amount / (1 + rate), MONEY_Q)
        return amount, units if units is not None else _q((amount - stamp) / nav, UNITS_Q), stamp
    if units <= 0:
        raise LedgerRejected("Units must be positive.")
    gross = units * nav * (1 + rate)
    return _q(gross, MONEY_Q), units, _q(gross - units * nav, MONEY_Q)


def _redemption(amount: Optional[Decimal], units: Optional[Decimal], nav: Decimal) -> Tuple[Decimal, Decimal]:
    """(proceeds, units) for an outflow given amount OR units."""
    if units is None and amount is None:
        raise LedgerRejected("Enter an amount or a number of units to redeem.")
    if units is None:
        if amount <= 0:
            raise LedgerRejected("Amount must be positive.")
        units = _q(amount / nav, UNITS_Q)
    elif units <= 0:
        raise LedgerRejected("Units must be positive.")
    return (amount if amount is not None else _q(units * nav, MONEY_Q)), units


def _resolve_single(draft: Dict[str, Any], existing: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    ttype = draft["txn_type"]
    code = int(draft["scheme_code"])
    trade_date: datetime.date = draft["trade_date"]
    first = hdb.first_nav_date(code)
    if first is None:
        raise LedgerRejected("This scheme has no NAV history, so it can't be valued.")
    if trade_date < first:
        raise LedgerRejected(f"Our NAV history for this scheme starts {first.isoformat()}; the trade date is earlier.")

    nav, nav_source, warning = _resolve_nav(code, trade_date, draft.get("nav"))
    amount = D(draft["amount"]) if draft.get("amount") not in (None, "") else None
    units = D(draft["units"]) if draft.get("units") not in (None, "") else None
    stamp = Decimal(0)

    if ttype in ledger.INFLOW_TYPES:
        if ttype == "DIVIDEND_REINVEST" and amount is None and units is None:
            raise LedgerRejected("Enter the dividend amount that was reinvested.")
        stamp_on = draft.get("apply_stamp_duty", True) and trade_date >= STAMP_DUTY_FROM
        amount, units, stamp = _purchase(amount, units, nav, stamp_on)
    elif ttype in ledger.OUTFLOW_TYPES:
        if draft.get("redeem_all"):
            units = ledger.units_held_on(with_split_scales(existing), code, trade_date)
            if units <= 0:
                raise LedgerRejected(f"No units of this fund are held on {trade_date.isoformat()}.")
        amount, units = _redemption(amount, units, nav)
    elif ttype == "DIVIDEND_PAYOUT":
        if amount is None or amount <= 0:
            raise LedgerRejected("Enter the dividend amount paid out.")
        units = Decimal(0)
    else:
        raise LedgerRejected(f"Unknown transaction type {ttype!r}.")

    row = {
        "portfolio_id": int(draft["portfolio_id"]), "scheme_code": code, "txn_type": ttype,
        "trade_date": trade_date, "amount": _q(amount, MONEY_Q), "units": _q(units, STORED_UNITS_Q),
        "nav": nav, "nav_source": nav_source, "stamp_duty": stamp, "source": "manual",
        "notes": draft.get("notes") or None,
    }
    return [row], [warning] if warning else []


# --- Validation ----------------------------------------------------------------------

def as_drafts(rows: Sequence[Dict[str, Any]], txn_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Rows about to be written, marked so validation reports warnings on them only."""
    return [{**r, "id": txn_id, "_draft": True} for r in rows]


def validate_ledger(proposed: Sequence[Dict[str, Any]], touched_codes: Set[int]) -> List[str]:
    """Replays one portfolio's full candidate ledger; raises LedgerRejected if any
    touched scheme ends up impossible. Returns warnings (split / NAV deviation)
    for the draft rows."""
    rows = with_split_scales(proposed)
    errs = [e for e in ledger.replay(rows).errors if e.scheme_code in touched_codes]
    if errs:
        raise LedgerRejected(errs[0].message, [e.as_dict() for e in errs])
    warnings: List[str] = []
    for t in (r for r in rows if r.get("_draft")):
        if t["units_scale"] != 1:
            warnings.append(
                f"The NAV you entered ({t['nav']}) is on a pre-split scale; AMFI's history is split-adjusted, "
                f"so these units count as {ledger.effective_units(t).normalize():f} today's units."
            )
        dev = t["_nav_deviation"]
        if dev is not None and abs(dev) > NAV_DEVIATION_WARN:
            warnings.append(f"Your NAV differs from AMFI's for that date by {dev * 100:+.2f}%. Your value is kept.")
    return warnings


def _validate_by_portfolio(rows: Sequence[Dict[str, Any]], touched_codes: Set[int]) -> List[str]:
    by_pf: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_pf[r["portfolio_id"]].append(r)
    return [w for pf_rows in by_pf.values() for w in validate_ledger(pf_rows, touched_codes)]


# --- Public write API ----------------------------------------------------------------

def preview(draft: Dict[str, Any], replace_txn_id: Optional[int] = None) -> Dict[str, Any]:
    """Dry run of add/edit: resolved rows, units held after, warnings, or errors. Never writes."""
    existing = [t for t in hdb.list_transactions([int(draft["portfolio_id"])]) if t["id"] != replace_txn_id]
    try:
        rows, warnings = resolve_draft(draft, existing)
        candidate = existing + as_drafts(rows)
        warnings += validate_ledger(candidate, {r["scheme_code"] for r in rows})
    except LedgerRejected as e:
        return {"ok": False, "error": str(e), "errors": e.errors, "rows": [], "warnings": []}
    scaled = with_split_scales(candidate)
    held_after = {r["scheme_code"]: float(ledger.units_held_on(scaled, r["scheme_code"], _today())) for r in rows}
    return {"ok": True, "error": None, "errors": [], "rows": [to_json(r) for r in rows],
            "warnings": warnings, "units_held_after": held_after}


def insert_validated(portfolio_id: int, rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Validate `rows` against the portfolio's ledger and write them atomically.
    Shared by add_transaction and SIP instalment generation."""
    with ledger_write():
        existing = hdb.list_transactions([portfolio_id])
        warnings = validate_ledger(existing + as_drafts(rows), {r["scheme_code"] for r in rows})
        return hdb.insert_transactions(rows), warnings


def add_transaction(draft: Dict[str, Any]) -> Dict[str, Any]:
    pid = int(draft["portfolio_id"])
    require_open_portfolio(pid)
    rows, warnings = resolve_draft(draft, hdb.list_transactions([pid]))
    saved, more = insert_validated(pid, rows)
    return {"transactions": [to_json(r) for r in saved], "warnings": warnings + more}


def edit_transaction(txn_id: int, draft: Dict[str, Any]) -> Dict[str, Any]:
    current = _require_txn(txn_id)
    if current["switch_group"] is not None:
        raise LedgerRejected("Edit a switch by deleting it and re-entering it, so both legs stay consistent.")
    if draft.get("txn_type") == "SWITCH":
        raise LedgerRejected("An existing transaction can't be turned into a switch; delete it and add a switch.")
    pid = int(draft.get("portfolio_id") or current["portfolio_id"])
    if pid != current["portfolio_id"]:
        require_open_portfolio(pid)
    with ledger_write():
        others = [t for t in hdb.list_transactions(sorted({pid, current["portfolio_id"]})) if t["id"] != txn_id]
        (row,), warnings = resolve_draft({**draft, "portfolio_id": pid}, [t for t in others if t["portfolio_id"] == pid])
        # Validates the destination portfolio and, if the row moved, the source one too.
        warnings += _validate_by_portfolio(others + as_drafts([row], txn_id), {row["scheme_code"], current["scheme_code"]})
        saved = hdb.update_transaction(txn_id, row)
    return {"transaction": to_json(saved), "warnings": warnings}


def _set_deleted(txn_id: int, deleted: bool) -> List[int]:
    """Soft-deletes or restores a transaction -- both legs, for a switch -- after
    checking the ledger stays possible without (or with) them."""
    current = _require_txn(txn_id, allow_deleted=not deleted)
    ids = hdb.get_switch_group_ids(current["switch_group"]) if current["switch_group"] else [txn_id]
    legs = hdb.get_transactions_by_ids(ids)
    with ledger_write():
        existing = [t for t in hdb.list_transactions(sorted({t["portfolio_id"] for t in legs})) if t["id"] not in ids]
        after = existing if deleted else existing + legs
        try:
            _validate_by_portfolio(after, {t["scheme_code"] for t in legs})
        except LedgerRejected as e:
            if not deleted:
                raise
            raise LedgerRejected("Deleting this would make a later transaction impossible: " + str(e), e.errors) from None
        hdb.set_deleted(ids, deleted)
    return ids


def delete_transaction(txn_id: int) -> Dict[str, Any]:
    return {"deleted_ids": _set_deleted(txn_id, True)}


def restore_transaction(txn_id: int) -> Dict[str, Any]:
    return {"restored_ids": _set_deleted(txn_id, False)}


def require_open_portfolio(pid: int) -> Dict[str, Any]:
    p = hdb.get_portfolio(pid)
    if p is None:
        raise LookupError(f"Portfolio {pid} not found")
    if p["archived"]:
        raise LedgerRejected("This portfolio is archived; unarchive it to add transactions.")
    return p


def _require_txn(txn_id: int, allow_deleted: bool = False) -> Dict[str, Any]:
    rows = hdb.get_transactions_by_ids([txn_id])
    if not rows:
        raise LookupError(f"Transaction {txn_id} not found")
    if rows[0]["deleted_at"] is not None and not allow_deleted:
        raise LookupError(f"Transaction {txn_id} is deleted")
    return rows[0]


# --- Valuation ---------------------------------------------------------------------

def resolve_portfolio_ids(pid: str) -> List[int]:
    """'all' = every non-archived portfolio (the household view); "3" = one
    portfolio, archived or not (an archived portfolio is still viewable);
    "1,3" = a subset (how a goal spanning several portfolios is valued)."""
    if str(pid).lower() == "all":
        return [p["id"] for p in hdb.list_portfolios()]
    try:
        ids = sorted({int(x) for x in str(pid).split(",") if x.strip()})
    except (TypeError, ValueError):
        raise LookupError(f"Unknown portfolio {pid!r}") from None
    if not ids:
        raise LookupError(f"Unknown portfolio {pid!r}")
    for n in ids:
        if hdb.get_portfolio(n) is None:
            raise LookupError(f"Portfolio {n} not found")
    return ids


def single_portfolio_id(pid: str) -> Optional[int]:
    """The portfolio id when the view is exactly one portfolio; None for "all" or a
    subset. Per-portfolio settings (targets, rebalancing, drift) only apply then."""
    if str(pid).lower() == "all":
        return None
    ids = resolve_portfolio_ids(pid)
    return ids[0] if len(ids) == 1 else None


def view_key(pid: str) -> str:
    """Normalised cache key for a view: 'all', or sorted ids ("3,1" == "1,3")."""
    return "all" if str(pid).lower() == "all" else ",".join(str(i) for i in resolve_portfolio_ids(pid))


def compute_xirr(flows: List[Tuple[datetime.date, Decimal]], terminal_date: Optional[datetime.date],
          terminal_value: float) -> Tuple[Optional[float], Optional[str]]:
    """(annualised %, None) or (None, reason) -- never a meaningless number."""
    if not flows or terminal_date is None:
        return None, "no_flows"
    if (terminal_date - min(d for d, _ in flows)).days < MIN_XIRR_DAYS:
        return None, "too_short"
    series = [(d, float(v)) for d, v in flows]
    if terminal_value > 0:
        series.append((terminal_date, terminal_value))
    rate = portfolio_sim.xirr(sorted(series, key=lambda x: x[0]))
    return (None, "no_solution") if rate is None else (rate * 100.0, None)


def replay_view(portfolio_ids: Sequence[int]) -> Tuple[Dict[int, ledger.LedgerResult], List[Dict[str, Any]]]:
    """Each portfolio's ledger replayed separately (FIFO lots never cross
    portfolios), plus the split-scaled transactions it was built from."""
    txns = with_split_scales(hdb.list_transactions(portfolio_ids))
    by_pf: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for t in txns:
        by_pf[t["portfolio_id"]].append(t)
    return {pid: ledger.replay(rows) for pid, rows in by_pf.items()}, txns


def _position_row(pos: ledger.Position, info: Dict[str, Any], portfolio_ids: List[int], split_adjusted: bool) -> Dict[str, Any]:
    units = float(pos.units) if not pos.is_closed else 0.0
    nav = info.get("latest_nav")
    nav = float(nav) if nav is not None and nav == nav else None
    value = units * nav if nav is not None else 0.0
    cost = float(pos.cost_basis) if units else 0.0
    chg = info.get("change_1d_pct")
    day_change = units * nav * (1.0 - 1.0 / (1.0 + float(chg) / 100.0)) if (units and nav is not None and chg is not None and chg == chg) else 0.0
    xirr_pct, xirr_note = compute_xirr(pos.cash_flows, info.get("latest_date"), value)
    plan = str(info.get("plan_type") or "")
    return {
        "scheme_code": pos.scheme_code,
        **{k: info.get(k) for k in ("scheme_name", "fund_house", "category", "broad_category", "option_type",
                                    "expense_ratio", "ter_status", "latest_date")},
        "plan_type": plan or None,
        "units": units,
        "avg_cost_nav": cost / units if units else None,
        "cost_basis": cost,
        "latest_nav": nav,
        "current_value": value,
        "unrealised_gain": value - cost,
        "unrealised_pct": (value - cost) / cost * 100.0 if cost > 0 else None,
        "realised_gain": float(pos.realised_gain),
        "dividend_income": float(pos.dividend_income),
        "total_invested": float(pos.total_invested),
        "total_redeemed": float(pos.total_redeemed),
        "day_change": day_change,
        "xirr_pct": xirr_pct,
        "xirr_note": xirr_note,
        "first_date": pos.first_date,
        "txn_count": pos.txn_count,
        "portfolio_ids": portfolio_ids,
        "is_closed": units == 0,
        "flags": {
            "stale_nav": info.get("is_active") is False,
            "regular_plan": "regular" in plan.lower(),
            "split_adjusted": split_adjusted,
        },
    }


def summary(pid: str) -> Dict[str, Any]:
    """Positions and KPIs for a view. Cached per (view, ledger version, market-data
    version), so the several analytics that start from it in one request share it.
    Treat the result as read-only."""
    return _summary_cached(view_key(pid), ledger_version(), get_data_version())


@cached(ttl=600, maxsize=64)
def _summary_cached(key: str, _ledger_v: int, _data_v: int) -> Dict[str, Any]:
    portfolio_ids = resolve_portfolio_ids(key)
    results, txns = replay_view(portfolio_ids)

    # Household view: one row per scheme across portfolios (lots concatenated, not re-matched).
    by_code: Dict[int, List[ledger.Position]] = defaultdict(list)
    held_in: Dict[int, List[int]] = defaultdict(list)
    for pid_i, res in sorted(results.items()):
        for code, pos in res.positions.items():
            by_code[code].append(pos)
            held_in[code].append(pid_i)
    meta = hdb.scheme_meta(list(by_code))
    split_codes = {int(t["scheme_code"]) for t in txns if t["units_scale"] != 1}
    positions = [
        _position_row(ledger.merge_positions(parts), meta.get(code, {}), held_in[code], code in split_codes)
        for code, parts in by_code.items()
    ]

    open_pos = [p for p in positions if not p["is_closed"]]
    total_value = sum(p["current_value"] for p in open_pos)
    for p in positions:
        p["weight_pct"] = p["current_value"] / total_value * 100.0 if total_value > 0 else None
    positions.sort(key=lambda p: (p["is_closed"], -p["current_value"]))

    pf_flows = [f for res in results.values() for f in res.portfolio_cash_flows]
    as_of = max((p["latest_date"] for p in open_pos if p["latest_date"]), default=None)
    cost_total = sum(p["cost_basis"] for p in open_pos)
    realised = sum(p["realised_gain"] for p in positions)
    dividends = sum(p["dividend_income"] for p in positions)
    day_change = sum(p["day_change"] for p in open_pos)
    unrealised = total_value - cost_total
    xirr_pct, xirr_note = compute_xirr(pf_flows, as_of, total_value)
    prev_value = total_value - day_change
    return {
        "portfolio_ids": portfolio_ids,
        "as_of": as_of,
        "kpis": {
            "current_value": total_value,
            "invested": cost_total,
            "unrealised_gain": unrealised,
            "unrealised_pct": unrealised / cost_total * 100.0 if cost_total > 0 else None,
            "realised_gain": realised,
            "dividend_income": dividends,
            "total_gain": unrealised + realised + dividends,
            "net_contributed": -sum(float(v) for _, v in pf_flows),
            "xirr_pct": xirr_pct,
            "xirr_note": xirr_note,
            "day_change": day_change,
            "day_change_pct": day_change / prev_value * 100.0 if prev_value > 0 else None,
            "open_positions": len(open_pos),
            "transactions": len(txns),
        },
        "positions": positions,
    }


def position_detail(pid: str, scheme_code: int) -> Dict[str, Any]:
    from app.db import queries as db  # read-only use of the façade

    code = int(scheme_code)
    results, txns = replay_view(resolve_portfolio_ids(pid))
    rows = [t for t in txns if int(t["scheme_code"]) == code]
    if not rows:
        raise LookupError(f"No transactions for scheme {code} in this view")

    lots = [
        {"portfolio_id": pid_i, "txn_id": lot.txn_id, "date": lot.date, "units": float(lot.units),
         "cost": float(lot.cost), "cost_nav": float(lot.cost_nav) if lot.cost_nav is not None else None}
        for pid_i, res in results.items() if code in res.positions
        for lot in res.positions[code].lots
    ]

    # Running unit balance per portfolio, in replay order.
    balance: Dict[int, Decimal] = defaultdict(Decimal)
    txn_rows = []
    for t in sorted(rows, key=ledger.replay_order):
        eff = ledger.effective_units(t)
        balance[t["portfolio_id"]] += ledger.unit_sign(t["txn_type"]) * eff
        txn_rows.append({**to_json(t), "effective_units": float(eff), "units_scale": float(t["units_scale"]),
                         "balance_units": float(balance[t["portfolio_id"]])})

    nav_df = db.get_nav_history_dataframe([code], start_date=min(t["trade_date"] for t in rows) - datetime.timedelta(days=30))
    nav_series = [{"date": d, "nav": float(n)} for d, n in zip(nav_df["nav_date"], nav_df["nav"])] if not nav_df.empty else []
    position = next((p for p in summary(pid)["positions"] if p["scheme_code"] == code), None)
    return {"position": position, "lots": lots, "transactions": txn_rows, "nav_series": nav_series}


# --- Export / backup ------------------------------------------------------------------

def export_csv(pid: str) -> str:
    txns = hdb.list_transactions(resolve_portfolio_ids(pid))
    names = {p["id"]: p["name"] for p in hdb.list_portfolios(include_archived=True)}
    meta = hdb.scheme_meta({t["scheme_code"] for t in txns})
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["portfolio", "trade_date", "type", "amfi_code", "scheme_name", "plan", "option",
                "amount_inr", "units", "nav", "nav_source", "stamp_duty_inr", "switch_group", "notes"])
    for t in txns:
        m = meta.get(int(t["scheme_code"]), {})
        w.writerow([names.get(t["portfolio_id"], t["portfolio_id"]), t["trade_date"].isoformat(), t["txn_type"],
                    t["scheme_code"], m.get("scheme_name") or "", m.get("plan_type") or "", m.get("option_type") or "",
                    t["amount"], t["units"], t["nav"], t["nav_source"], t["stamp_duty"],
                    t["switch_group"] or "", t["notes"] or ""])
    return buf.getvalue()


BACKUP_FORMAT = "myfinonce-holdings-backup"
BACKUP_VERSION = 1


def backup() -> Dict[str, Any]:
    return {
        "format": BACKUP_FORMAT,
        "version": BACKUP_VERSION,
        "exported_at": datetime.datetime.now().isoformat(timespec="seconds"),
        **{table: [to_json(r) for r in rows] for table, rows in hdb.dump_all().items()},
    }


def restore(payload: Dict[str, Any]) -> Dict[str, int]:
    if payload.get("format") != BACKUP_FORMAT or payload.get("version") != BACKUP_VERSION:
        raise LedgerRejected("Not a MyFinonce holdings backup (format/version mismatch).")
    with ledger_write():
        if not hdb.ledger_is_empty():
            raise LedgerRejected("Restore only runs into an empty holdings ledger, so nothing is ever overwritten.")
        # Tables absent from an older backup (taken before a feature existed) are skipped.
        return hdb.restore_all({t: [_parse_row(r) for r in payload.get(t) or []] for t, _ in hdb.BACKUP_TABLES})


# Backup JSON carries NUMERIC as exact strings and dates as ISO strings; types
# come back by column-name convention, which every holdings table follows.
_DECIMAL_COLS = {"amount", "units", "nav", "stamp_duty", "target_pct", "target_amount",
                 "inflation_pct", "step_up_pct", "threshold"}
_DATE_COLS = {"trade_date", "start_date", "end_date", "target_date"}


def _parse_row(r: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(r)
    for k, v in out.items():
        if not isinstance(v, str):
            continue
        if k in _DECIMAL_COLS:
            out[k] = D(v)
        elif k in _DATE_COLS:
            out[k] = datetime.date.fromisoformat(v)
        elif k.endswith("_at"):
            out[k] = datetime.datetime.fromisoformat(v)
        elif k == "switch_group":
            out[k] = uuid.UUID(v)
    return out


def to_json(row: Dict[str, Any]) -> Dict[str, Any]:
    """DB row -> JSON-safe dict. NUMERIC stays an exact decimal string (the UI parses
    it for display; backups round-trip it losslessly). Keys starting with "_" and
    the in-memory units_scale are dropped."""
    out: Dict[str, Any] = {}
    for k, v in row.items():
        if k.startswith("_") or k == "units_scale":
            continue
        if isinstance(v, Decimal):
            out[k] = str(v)
        elif isinstance(v, (datetime.datetime, datetime.date)):
            out[k] = v.isoformat()
        elif isinstance(v, uuid.UUID):
            out[k] = str(v)
        else:
            out[k] = v
    return out
