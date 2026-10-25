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
date -- and so is an auto-filled NAV once a split AMFI publishes LATER has
back-adjusted the history it was read from. `split_scale` detects either from the
ratio itself and rescales the units, so value = units x today's NAV stays right.
"""

from __future__ import annotations

import contextlib
import csv
import datetime
import io
import logging
import math
import threading
import uuid
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, Iterator, List, Optional, Sequence, Set, Tuple

import psycopg

from app import portfolio_sim
from app.core.cache import NoCache, cached
from app.db import holdings as hdb
from app.db.connection import get_data_version
from app.services import holdings_ledger as ledger
from app.services.holdings_config import NAV_PRINT_TOLERANCE, PENDING_NAV_DAYS
from app.services.holdings_ledger import D

logger = logging.getLogger(__name__)

# Stamp duty on mutual fund purchases: 0.005%, effective 2020-07-01, levied on
# purchases, SIP instalments, switch-ins and dividend reinvestments.
STAMP_DUTY_RATE = Decimal("0.00005")
STAMP_DUTY_FROM = datetime.date(2020, 7, 1)

# Mirrors db/queries.py's _SPLIT_FACTORS / _SPLIT_TOLERANCE (private to a file
# under refactor freeze, so not imported). test_holdings.py asserts they match.
SPLIT_FACTORS = [2, 3, 4, 5, 10, 20, 25, 50, 100, 200, 500, 1000]
SPLIT_CANDIDATES = sorted(set(SPLIT_FACTORS) | {round(1.0 / f, 10) for f in SPLIT_FACTORS})
SPLIT_TOLERANCE = 0.05
# A user NAV this far from AMFI's (and not a split) earns a visible warning...
NAV_DEVIATION_WARN = 0.005
# ...and this far, or a clean split ratio away, is refused until the owner confirms it:
# 45.10 typed for 15.10, or the IDCW NAV against the Growth code, would otherwise multiply
# the units (as a "split") or misprice them, and wreck value, gain and XIRR.
NAV_DEVIATION_BLOCK = 0.20

# XIRR annualises; over a few days it turns a 1% move into a three-digit rate.
MIN_XIRR_DAYS = 30

from app.classification import is_equity_oriented, is_exchange_traded, sebi_category  # noqa: E402  (sebi_category re-exported: svc.sebi_category)


def _as_date(value: Any) -> Optional[datetime.date]:
    if value is None or value != value:     # NaN / NaT compare unequal to themselves
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if hasattr(value, "date"):          # pandas Timestamp
        return value.date()
    return datetime.date.fromisoformat(str(value)[:10])


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
    """Copies of `txns` carrying units_scale, and _nav_deviation for user-typed NAVs.

    Every row is checked against AMFI's NAV for its date, not only the typed ones. An
    auto-filled NAV was on AMFI's scale when it was entered, but a split AMFI publishes
    later back-adjusts every earlier NAV in nav_history, leaving the row's NAV and units on
    the old scale: after a 10:1 split its value would read a tenth of the truth. The ratio
    to today's history is then a clean split factor, and the units are rescaled exactly as
    for a pre-split statement NAV."""
    rows = [dict(t) for t in txns]
    priced = [t for t in rows if t.get("nav") is not None]
    amfi = hdb.navs_on_dates((t["scheme_code"], t["trade_date"]) for t in priced)
    for t in rows:
        t["units_scale"], t["_nav_deviation"], t["_amfi_nav"] = Decimal(1), None, None
    for t in priced:
        t["_amfi_nav"] = amfi.get((int(t["scheme_code"]), t["trade_date"]))
        scale, deviation = split_scale(t["nav"], t["_amfi_nav"])
        t["units_scale"] = scale
        t["_nav_deviation"] = deviation if t.get("nav_source") == "user" else None
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
    if draft.get("switch_in_date") and draft["switch_in_date"] < draft["trade_date"]:
        raise LedgerRejected("The switch-in date can't be before the switch-out date.")
    (out,), w_out = resolve_draft({**draft, "txn_type": "SWITCH_OUT"}, existing)
    (into,), w_in = resolve_draft({
        "portfolio_id": draft["portfolio_id"], "scheme_code": int(target), "txn_type": "SWITCH_IN",
        "trade_date": draft.get("switch_in_date") or draft["trade_date"], "amount": out["amount"],
        "nav": draft.get("switch_in_nav"), "apply_stamp_duty": draft.get("apply_stamp_duty", True),
        "notes": draft.get("notes"), "nav_confirmed": draft.get("nav_confirmed", False),
    }, existing)
    out["switch_group"] = into["switch_group"] = uuid.uuid4()
    return [out, into], w_out + w_in


def _resolve_nav(code: int, trade_date: datetime.date, typed: Any) -> Tuple[Decimal, str, datetime.date, Optional[str]]:
    """(nav, nav_source, nav_date, warning). The statement NAV the user typed wins, on
    the trade date as typed. Otherwise AMFI's NAV for the trade date.

    For a date the fund published no NAV (a weekend or a market holiday), the NEXT
    business day's NAV -- SEBI's rule for an order made on a non-business day, and what
    SIP generation already does -- and the transaction moves to that date, so it is
    valued from the day it was really allotted. A date the fund has not published yet is
    refused, not priced at an older NAV: units bought at the wrong NAV would stay wrong."""
    if typed not in (None, ""):
        nav = _q(D(typed), NAV_Q)
        if nav <= 0:
            raise LedgerRejected("NAV must be positive.")
        return nav, "user", trade_date, None
    before = hdb.nav_on_or_before(code, trade_date)
    if before is not None and before[0] == trade_date:
        return _q(D(before[1]), NAV_Q), "amfi_auto", trade_date, None
    after = hdb.nav_on_or_after(code, trade_date)
    if after is not None:
        nav_date, nav_f = after
        nav = _q(D(nav_f), NAV_Q)
        return nav, "amfi_auto", nav_date, (
            f"No NAV was published on {trade_date.isoformat()} (a weekend or market holiday), so this is allotted at the "
            f"next business day's NAV, {nav} on {nav_date.isoformat()}, and recorded on that date.")
    last = before[0] if before is not None else None
    if last is None:
        raise LedgerRejected("No AMFI NAV on or before the trade date.")
    market = hdb.latest_market_nav_date()
    if market is not None and (market - last).days > PENDING_NAV_DAYS:
        raise LedgerRejected(f"This fund's last published NAV is from {last.isoformat()}, so it may have been merged or "
                             "wound up. Type the NAV from your statement.")
    raise LedgerRejected(f"AMFI hasn't published this fund's NAV for {trade_date.isoformat()} yet (its latest is "
                         f"{last.isoformat()}). Type the NAV from your statement, or add this after tonight's NAV sync.")


def _check_amount_matches_units(amount: Decimal, units: Decimal, nav: Decimal, rate: Decimal = Decimal(0)) -> None:
    """When a draft gives both an amount and units, they must describe the same trade at
    the NAV: amount ~ units x NAV (x 1 + stamp duty for a purchase). The slack covers a
    statement's rounding -- a paisa-level amount, units to 3 decimals (half a thousandth of
    a unit of NAV) -- and nothing more. Without it, 1 unit for Rs 10 lakh wrote a cash flow
    and a cost that disagreed by the whole amount."""
    expected = units * nav * (1 + rate)
    slack = max(Decimal(1), amount * Decimal("0.001"), nav * Decimal("0.001"))
    if abs(amount - expected) > slack:
        raise LedgerRejected(f"Rs {amount:,.2f} doesn't match {units.normalize():f} units at NAV {nav} "
                             f"(about Rs {_q(expected, MONEY_Q):,.2f}). Enter one of them and let the other be worked out.")


def _purchase(amount: Optional[Decimal], units: Optional[Decimal], nav: Decimal, stamp_on: bool) -> Tuple[Decimal, Decimal, Decimal]:
    """(amount, units, stamp_duty) for an inflow given amount OR units (or both, when they
    agree). Stamp duty comes out of the amount paid, so fewer units are allotted."""
    if amount is None and units is None:
        raise LedgerRejected("Enter an amount or a number of units.")
    rate = STAMP_DUTY_RATE if stamp_on else Decimal(0)
    if amount is not None:
        if amount <= 0:
            raise LedgerRejected("Amount must be positive.")
        if units is not None:
            if units <= 0:
                raise LedgerRejected("Units must be positive.")
            _check_amount_matches_units(amount, units, nav, rate)
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
    elif amount is not None:
        _check_amount_matches_units(amount, units, nav)
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

    nav, nav_source, trade_date, warning = _resolve_nav(code, trade_date, draft.get("nav"))
    # A typed NAV far from AMFI's is refused until confirmed (validate_ledger); an edit that
    # keeps the NAV already on record needs no second confirmation.
    nav_confirmed = nav_source == "user" and (
        bool(draft.get("nav_confirmed")) or (draft.get("_confirmed_nav") is not None and nav == draft["_confirmed_nav"]))
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
        "notes": draft.get("notes") or None, "_nav_confirmed": nav_confirmed,
    }
    return [row], [warning] if warning else []


# --- Validation ----------------------------------------------------------------------

def as_drafts(rows: Sequence[Dict[str, Any]], txn_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Rows about to be written, marked so validation reports warnings on them only."""
    return [{**r, "id": txn_id, "_draft": True} for r in rows]


def _unconfirmed_nav(t: Dict[str, Any], message: str) -> LedgerRejected:
    """The refusal for a typed NAV far from AMFI's; its error carries code "nav_unconfirmed"
    so the form can offer the confirmation."""
    return LedgerRejected(message, [{"txn_id": t.get("id"), "scheme_code": int(t["scheme_code"]),
                                     "trade_date": t["trade_date"].isoformat(), "message": message,
                                     "code": "nav_unconfirmed"}])


def validate_ledger(proposed: Sequence[Dict[str, Any]], touched_codes: Set[int]) -> List[str]:
    """Replays one portfolio's full candidate ledger; raises LedgerRejected if any
    touched scheme ends up impossible, or a draft's typed NAV is far from AMFI's and not
    confirmed. Returns warnings (split / NAV deviation) for the draft rows."""
    rows = with_split_scales(proposed)
    errs = [e for e in ledger.replay(rows).errors + ledger.unpaired_switch_legs(rows) if e.scheme_code in touched_codes]
    if errs:
        raise LedgerRejected(errs[0].message, [e.as_dict() for e in errs])
    warnings: List[str] = []
    for t in (r for r in rows if r.get("_draft")):
        unconfirmed = t.get("nav_source") == "user" and not t.get("_nav_confirmed")
        amfi, day = t.get("_amfi_nav"), t["trade_date"].isoformat()
        if t["units_scale"] != 1:
            if unconfirmed:
                ratio = float(t["nav"]) / amfi
                times = f"{ratio:.4g} times" if ratio >= 1 else f"1/{1 / ratio:.4g} of"
                raise _unconfirmed_nav(t, (
                    f"The NAV you typed ({t['nav']}) is {times} AMFI's NAV for {day} ({amfi:.4f}). A statement from "
                    "before a unit split shows that; a typo, or the wrong plan or option, does too. Check it, or "
                    "confirm that your statement says so."))
            warnings.append(
                f"The NAV you entered ({t['nav']}) is on a pre-split scale; AMFI's history is split-adjusted, "
                f"so these units count as {ledger.effective_units(t).normalize():f} today's units."
            )
        dev = t["_nav_deviation"]
        if dev is not None and abs(dev) > NAV_DEVIATION_BLOCK and unconfirmed:
            raise _unconfirmed_nav(t, (
                f"The NAV you typed ({t['nav']}) is {(float(t['nav']) / amfi - 1) * 100:+.1f}% away from AMFI's NAV for "
                f"{day} ({amfi:.4f}). Check it, and the fund's plan and option, or confirm that your statement says so."))
        if dev is not None and abs(dev) > NAV_DEVIATION_WARN:
            warnings.append(f"Your NAV differs from AMFI's for that date by {dev * 100:+.2f}%. Your value is kept.")
    return warnings


def _validate_by_portfolio(rows: Sequence[Dict[str, Any]], touched_codes: Set[int]) -> List[str]:
    by_pf: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_pf[r["portfolio_id"]].append(r)
    return [w for pf_rows in by_pf.values() for w in validate_ledger(pf_rows, touched_codes)]


# --- Public write API ----------------------------------------------------------------

def _kept_row(current: Dict[str, Any], draft: Dict[str, Any], pid: int) -> Optional[Dict[str, Any]]:
    """The row exactly as stored -- only its portfolio and note taken from the draft --
    when the draft leaves the transaction's economics as they were; else None.

    The edit form re-enters a purchase by its amount and an AMFI-priced row with its NAV
    blank, so resolving an unchanged row again would re-derive its units from the amount
    (rounded to 3 decimals) and re-price it at whatever AMFI's history says today (after a
    split, a correction, or a change in how dates resolve). Changing a note must not move
    units or money."""
    if (int(draft["scheme_code"]) != int(current["scheme_code"]) or draft["txn_type"] != current["txn_type"]
            or draft["trade_date"] != current["trade_date"] or draft.get("redeem_all")):
        return None
    typed = draft.get("nav")
    if typed in (None, ""):
        if current["nav_source"] != "amfi_auto":
            return None
    elif current["nav_source"] != "user" or _q(D(typed), NAV_Q) != current["nav"]:
        return None
    amount, units = draft.get("amount"), draft.get("units")
    if amount in (None, "") and units in (None, ""):
        return None
    if amount not in (None, "") and _q(D(amount), MONEY_Q) != current["amount"]:
        return None
    if units not in (None, "") and _q(D(units), STORED_UNITS_Q) != current["units"]:
        return None
    if current["txn_type"] in ledger.INFLOW_TYPES:
        stamp_on = bool(draft.get("apply_stamp_duty", True)) and current["trade_date"] >= STAMP_DUTY_FROM
        if stamp_on != (current["stamp_duty"] > 0):
            return None
    kept = {k: current[k] for k in ("scheme_code", "txn_type", "trade_date", "amount", "units", "nav", "nav_source",
                                    "stamp_duty", "source")}
    return {**kept, "portfolio_id": pid, "notes": draft.get("notes") or None, "_nav_confirmed": True}


def _edit_rows(current: Dict[str, Any], draft: Dict[str, Any], others: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """The row an edit would write (see _kept_row), and its warnings."""
    if current["switch_group"] is not None:
        raise LedgerRejected("Edit a switch by deleting it and re-entering it, so both legs stay consistent.")
    if draft.get("txn_type") == "SWITCH":
        raise LedgerRejected("An existing transaction can't be turned into a switch; delete it and add a switch.")
    pid = int(draft.get("portfolio_id") or current["portfolio_id"])
    kept = _kept_row(current, draft, pid)
    if kept is not None:
        return [kept], []
    confirmed_nav = current["nav"] if current["nav_source"] == "user" else None
    (row,), warnings = resolve_draft({**draft, "portfolio_id": pid, "_confirmed_nav": confirmed_nav},
                                     [t for t in others if t["portfolio_id"] == pid])
    row["source"] = current["source"]
    return [row], warnings


def preview(draft: Dict[str, Any], replace_txn_id: Optional[int] = None) -> Dict[str, Any]:
    """Dry run of add/edit: resolved rows, units held after, warnings, or errors. Never
    writes. An edit is checked exactly as the save will be -- both portfolios when the row
    moves -- so a preview that passes never turns into a refused save."""
    pid = int(draft["portfolio_id"])
    try:
        current = _require_txn(replace_txn_id) if replace_txn_id is not None else None
        pids = sorted({pid} | ({current["portfolio_id"]} if current else set()))
        for p in pids:
            require_open_portfolio(p)
        others = [t for t in hdb.list_transactions(pids) if t["id"] != replace_txn_id]
        if current is not None:
            rows, warnings = _edit_rows(current, draft, others)
        else:
            rows, warnings = resolve_draft(draft, [t for t in others if t["portfolio_id"] == pid])
        candidate = others + as_drafts(rows, replace_txn_id)
        touched = {r["scheme_code"] for r in rows} | ({current["scheme_code"]} if current else set())
        warnings += _validate_by_portfolio(candidate, touched)
    except LedgerRejected as e:
        return {"ok": False, "error": str(e), "errors": e.errors, "rows": [], "warnings": []}
    scaled = with_split_scales([t for t in candidate if t["portfolio_id"] == pid])
    held_after = {r["scheme_code"]: float(ledger.units_held_on(scaled, r["scheme_code"], _today())) for r in rows}
    return {"ok": True, "error": None, "errors": [], "rows": [to_json(r) for r in rows],
            "warnings": warnings, "units_held_after": held_after}


def insert_validated(portfolio_id: int, rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Validate `rows` against the portfolio's ledger and write them atomically."""
    with ledger_write():
        return insert_validated_locked(portfolio_id, rows)


def insert_validated_locked(portfolio_id: int, rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """insert_validated for a caller already holding the ledger lock -- SIP generation,
    which works out what to write under the same lock it writes under."""
    existing = hdb.list_transactions([portfolio_id])
    warnings = validate_ledger(existing + as_drafts(rows), {r["scheme_code"] for r in rows})
    return hdb.insert_transactions(rows), warnings


def add_transaction(draft: Dict[str, Any]) -> Dict[str, Any]:
    pid = int(draft["portfolio_id"])
    with ledger_write():
        require_open_portfolio(pid)
        rows, warnings = resolve_draft(draft, hdb.list_transactions([pid]))
        saved, more = insert_validated_locked(pid, rows)
    return {"transactions": [to_json(r) for r in saved], "warnings": warnings + more}


def edit_transaction(txn_id: int, draft: Dict[str, Any]) -> Dict[str, Any]:
    with ledger_write():
        # Read under the lock: a delete landing between the read and the write would
        # otherwise turn into a 500.
        current = _require_txn(txn_id)
        require_open_portfolio(current["portfolio_id"])
        pid = int(draft.get("portfolio_id") or current["portfolio_id"])
        if pid != current["portfolio_id"]:
            require_open_portfolio(pid)
        others = [t for t in hdb.list_transactions(sorted({pid, current["portfolio_id"]})) if t["id"] != txn_id]
        (row,), warnings = _edit_rows(current, draft, others)
        # Validates the destination portfolio and, if the row moved, the source one too.
        warnings += _validate_by_portfolio(others + as_drafts([row], txn_id), {row["scheme_code"], current["scheme_code"]})
        saved = hdb.update_transaction(txn_id, row)
    return {"transaction": to_json(saved), "warnings": warnings}


def _set_deleted(txn_id: int, deleted: bool) -> List[int]:
    """Soft-deletes or restores a transaction -- both legs, for a switch -- after
    checking the ledger stays possible without (or with) them."""
    with ledger_write():
        current = _require_txn(txn_id, allow_deleted=not deleted)
        ids = hdb.get_switch_group_ids(current["switch_group"]) if current["switch_group"] else [txn_id]
        legs = hdb.get_transactions_by_ids(ids)
        for p in sorted({t["portfolio_id"] for t in legs}):
            require_open_portfolio(p)
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
        raise LedgerRejected("This portfolio is archived; unarchive it to add or change its transactions.")
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
    subset. Per-portfolio settings (the chosen benchmark) only apply then."""
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


def ledger_errors(results: Dict[int, ledger.LedgerResult], txns: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Rows on record that the ledger can't replay -- a redemption of more units than were
    held, a switch leg without its partner. Every write refuses to create one, but a restored
    file, a later NAV restatement or a write outside the app can still leave one. replay()
    skips such a row, so the page must say so rather than quietly value the rest."""
    by_pf: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for t in txns:
        by_pf[t["portfolio_id"]].append(t)
    out = [{**e.as_dict(), "portfolio_id": pid} for pid, res in sorted(results.items()) for e in res.errors]
    out += [{**e.as_dict(), "portfolio_id": pid} for pid, rows in sorted(by_pf.items()) for e in ledger.unpaired_switch_legs(rows)]
    return out


def _position_row(pos: ledger.Position, info: Dict[str, Any], portfolio_ids: List[int], split_adjusted: bool,
                  units_new_today: Decimal = Decimal(0)) -> Dict[str, Any]:
    units = float(pos.units) if not pos.is_closed else 0.0
    nav = info.get("latest_nav")
    nav = float(nav) if nav is not None and nav == nav else None
    # Held but with no NAV to value it at: shown as unpriced and left out of the totals,
    # never valued at Rs 0 (which would read as a 100% loss).
    unpriced = bool(units) and nav is None
    value = units * nav if nav is not None else 0.0
    cost = float(pos.cost_basis) if units else 0.0
    # The day's rupee move is earned by the units held at the PREVIOUS close:
    # units_then x (nav - nav_1d_ago). Units allotted on the latest NAV date were bought
    # at that NAV and have not moved yet; units redeemed on it were held through the move
    # and sold at its end, so they are added back. This is the broker convention, and it
    # is what makes the column add up to the portfolio's 1-day tile. The NAV pair is used
    # directly: rebuilding yesterday's value from change_1d_pct, which summary_table rounds
    # to 4 dp, was paise off per fund.
    prev_nav = info.get("nav_1d_ago")
    prev_nav = float(prev_nav) if prev_nav is not None and prev_nav == prev_nav else None
    units_then = units - float(units_new_today)
    day_change = units_then * (nav - prev_nav) if (units_then and nav is not None and prev_nav is not None) else 0.0
    # What those units were worth at the previous close: the base the 1-day tile's
    # percentage is taken on, summed across funds.
    day_base = units_then * prev_nav if (units_then and nav is not None and prev_nav is not None) else 0.0
    xirr_pct, xirr_note = compute_xirr(pos.cash_flows, info.get("latest_date"), value)
    first_flow = min((d for d, _ in pos.cash_flows), default=None)
    ter = info.get("expense_ratio")
    ter = float(ter) if ter is not None and ter == ter else None
    riskometer = info.get("riskometer")
    plan = str(info.get("plan_type") or "")
    return {
        "scheme_code": pos.scheme_code,
        **{k: info.get(k) for k in ("scheme_name", "fund_house", "category", "broad_category", "option_type",
                                    "expense_ratio", "ter_status", "latest_date")},
        "sebi_category": sebi_category(info.get("category")),
        "riskometer": riskometer if isinstance(riskometer, str) and riskometer else None,
        # What the expense ratio costs a year at today's value. The TER is deducted from
        # the NAV daily, so this is already inside the returns shown -- it is not a bill.
        "annual_fee": value * ter / 100.0 if (ter is not None and value > 0) else None,
        "plan_type": plan or None,
        "display_name": info.get("display_name") or hdb.display_name({"scheme_code": pos.scheme_code}),
        "units": units,
        "avg_cost_nav": cost / units if units else None,
        "cost_basis": cost,
        "latest_nav": nav,
        "current_value": value,
        "unrealised_gain": None if unpriced else value - cost,
        "unrealised_pct": (value - cost) / cost * 100.0 if (cost > 0 and not unpriced) else None,
        "realised_gain": float(pos.realised_gain),
        "dividend_income": float(pos.dividend_income),
        "dividend_reinvested": float(pos.dividend_reinvested),
        "stamp_duty": float(pos.stamp_duty),
        "total_invested": float(pos.total_invested),
        "total_redeemed": float(pos.total_redeemed),
        "day_change": day_change,
        "day_base": day_base,
        "xirr_pct": xirr_pct,
        "xirr_note": xirr_note,
        "xirr_available_on": (first_flow + datetime.timedelta(days=MIN_XIRR_DAYS)).isoformat()
                             if (xirr_note == "too_short" and first_flow is not None) else None,
        "first_date": pos.first_date,
        "txn_count": pos.txn_count,
        "portfolio_ids": portfolio_ids,
        "is_closed": units == 0,
        "flags": {
            "stale_nav": info.get("is_active") is False,
            "regular_plan": "regular" in plan.lower(),
            "split_adjusted": split_adjusted,
            "unpriced": unpriced,
            # Identity and latest NAV came from the fallback lookup (the price cache was
            # missing or lagging), which has no previous NAV: no 1-day move for this fund.
            "no_day_change": bool(info.get("meta_fallback")),
        },
    }


#: "Earning now": each fund's NAV growth over this many calendar days, annualised. Long
#: enough to smooth a liquid fund's day-to-day noise and about one F&O expiry cycle for an
#: arbitrage fund; short enough to follow a change in interest rates within weeks (a 90-day
#: window still showed a liquid fund at 6.50% a year when it had slipped to 5.73%).
EARNING_NOW_DAYS = 30
#: Funds whose recent rate is a meaningful income rate. A month of an equity fund scaled to
#: a year is noise (+3% in a month reads as 40% a year), so equity is shown as "-".
EARNING_NOW_CLASSES = ("Cash & Liquid", "Debt")
#: A fund earning this much less (percentage points a year) than another one held in the
#: same SEBI category is flagged.
EARNING_NOW_GAP_PP = 0.25


def _attach_earning_now(positions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Per fund: what it is earning at today's rates -- its last EARNING_NOW_DAYS of NAV
    growth, annualised (the NAV is already net of the fund's expenses) -- and that rate on
    today's value per day. Flags a fund trailing another held in the same SEBI category.
    Returns the portfolio-level figures for the KPIs: the value-weighted rate over the funds
    that have one, rupees a day, and the share of the money it covers.

    A recent rate, not a forecast: liquid yields follow RBI rates, arbitrage returns move
    with the monthly expiry cycle."""
    for p in positions:
        p.update(earning_now_pct=None, earning_now_per_day=None, earning_now_from=None, earning_now_behind=None)
    eligible = [p for p in positions if not p["is_closed"] and p.get("asset_class") in EARNING_NOW_CLASSES
                and p.get("latest_nav") and _as_date(p.get("latest_date"))]
    starts = hdb.navs_on_or_before({p["scheme_code"]: _as_date(p["latest_date"]) - datetime.timedelta(days=EARNING_NOW_DAYS)
                                    for p in eligible})
    for p in eligible:
        start = starts.get(p["scheme_code"])
        if not start:
            continue    # younger than the window: no rate rather than a guess
        start_date, start_nav = start
        days = (_as_date(p["latest_date"]) - start_date).days
        if days <= 0 or start_nav <= 0:
            continue
        # The fund's average day over the window, compounded to a year. Rupees a day are
        # that day's growth on today's value -- not the yearly rate / 365, which overstates
        # a day by about half the rate (~2.8% at 5.5% a year) because the rate compounds.
        daily = (p["latest_nav"] / start_nav) ** (1.0 / days) - 1.0
        p["earning_now_pct"] = ((1.0 + daily) ** 365.0 - 1.0) * 100.0
        p["earning_now_per_day"] = p["current_value"] * daily
        p["earning_now_from"] = start_date.isoformat()

    by_category: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for p in eligible:
        if p["earning_now_pct"] is not None and p.get("sebi_category"):
            by_category[p["sebi_category"]].append(p)
    for peers in by_category.values():
        best = max(peers, key=lambda q: q["earning_now_pct"])
        for p in peers:
            gap = best["earning_now_pct"] - p["earning_now_pct"]
            if p is not best and gap >= EARNING_NOW_GAP_PP:
                p["earning_now_behind"] = {"pp": gap, "than": best["display_name"],
                                           "rupees_per_year": p["current_value"] * gap / 100.0}

    rated = [p for p in eligible if p["earning_now_pct"] is not None]
    rated_value = sum(p["current_value"] for p in rated)
    total = sum(p["current_value"] for p in positions if not p["is_closed"])
    per_day = sum(p["earning_now_per_day"] for p in rated)
    return {
        # The mix's own day compounded to a year, so the tile's rate and its rupees a day
        # describe the same money. A value-weighted average of the funds' yearly rates
        # overstates it slightly: compounding is convex.
        "earning_now_pct": ((1.0 + per_day / rated_value) ** 365.0 - 1.0) * 100.0 if rated_value > 0 else None,
        "earning_now_per_day": per_day if rated else None,
        "earning_now_coverage_pct": rated_value / total * 100.0 if total > 0 else None,
        "earning_now_days": EARNING_NOW_DAYS,
    }


#: STT on redeeming units of an equity-oriented fund, paid by the seller: 0.001%.
SELL_STT_RATE = 0.001 / 100.0


def _attach_sell_now(positions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Per fund: what selling the whole holding at its latest NAV would give.

    you get = units x latest NAV - STT (0.001%, equity-oriented funds only); profit = what
    you get + any money already taken out (redemptions, dividends paid out) - the cash paid
    in (stamp duty included), i.e. the rupees you would be up after selling. A reinvested
    dividend is not money taken out: its units are already in what you get. Assumes no exit
    load (the owner sells only once it has lapsed) and leaves income tax out; nothing else
    is deducted on a redemption -- no cess on STT, no stamp duty, GST or TDS, and no
    platform charge for direct plans. The estimate is at the latest NAV: a redemption is
    paid at the NAV of the day it is placed.

    ETFs and closed-ended schemes are sold on the exchange at market price with trading
    charges, so they get no estimate and are left out of the totals."""
    you_get = stt_total = profit_total = 0.0
    excluded = 0
    for p in positions:
        p.update(sell_now_value=None, sell_now_stt=None, sell_now_profit=None, sell_now_note=None)
        if p["is_closed"]:
            continue
        if is_exchange_traded(p.get("category"), p.get("scheme_name")):
            p["sell_now_note"] = "exchange_traded"
            excluded += 1
            continue
        if p["flags"]["unpriced"]:
            p["sell_now_note"] = "unpriced"
            excluded += 1
            continue
        stt = p["current_value"] * SELL_STT_RATE if is_equity_oriented(p.get("category"), p.get("broad_category"), p.get("scheme_name")) else 0.0
        get = p["current_value"] - stt
        paid_out = p["dividend_income"] - p["dividend_reinvested"]
        profit = get + p["total_redeemed"] + paid_out - p["total_invested"]
        p.update(sell_now_value=get, sell_now_stt=stt, sell_now_profit=profit)
        you_get += get
        stt_total += stt
        profit_total += profit
    estimated = any(p["sell_now_value"] is not None for p in positions)
    return {
        "sell_now_value": you_get if estimated else None,
        "sell_now_stt": stt_total if estimated else None,
        "sell_now_profit": profit_total if estimated else None,
        "sell_now_excluded": excluded,
    }


TER_MIN_PEERS = 10
TER_HIGH_VS_P90 = 1.25      # > 1.25 x the category's 90th percentile
TER_LOW_VS_P10 = 0.5        # < half the category's 10th percentile
TER_SUM_TOLERANCE = 0.02    # components vs total, in percentage points


def ter_context(meta: Dict[int, Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    """Per scheme: its TER, the disclosed breakdown, where it sits in its category
    (same plan type), and a verdict. Under SEBI's current disclosure a TER includes
    brokerage, transaction costs and statutory levies, so e.g. a 0.10%-base
    arbitrage fund can correctly total 1.00% -- judging a TER against its own
    category's spread is what makes that readable."""
    spread = hdb.category_ter_spread([(m.get("category"), m.get("plan_type")) for m in meta.values()])
    out: Dict[int, Dict[str, Any]] = {}
    for code, m in meta.items():
        ter = m.get("expense_ratio")
        ter = float(ter) if ter is not None and ter == ter else None
        parts = {k: m.get(f"ter_{k}") for k in ("base_expense_ratio", "brokerage_cost_pct", "transaction_cost_pct", "statutory_levies_pct")}
        parts = {k: float(v) for k, v in parts.items() if v is not None and v == v}
        s = spread.get((m.get("category"), m.get("plan_type")))
        status, reason = "ok", None
        if ter is None:
            status, reason = "missing", "No expense ratio on record."
        elif m.get("ter_status") != "official":
            status, reason = "unverified", "Not from AMFI's official TER disclosure; treat as an estimate."
        elif len(parts) == 4 and abs(sum(parts.values()) - ter) > TER_SUM_TOLERANCE:
            status, reason = "inconsistent", f"Its disclosed components add up to {sum(parts.values()):.2f}%, not {ter:.2f}%."
        elif s and s["peers"] >= TER_MIN_PEERS and ter > s["p90"] * TER_HIGH_VS_P90:
            status, reason = "high", f"Well above its peers: {ter:.2f}% vs a category median of {s['median']:.2f}% (90% of peers are at or below {s['p90']:.2f}%)."
        elif s and s["peers"] >= TER_MIN_PEERS and ter < s["p10"] * TER_LOW_VS_P10:
            status, reason = "low", f"Unusually low for its category: {ter:.2f}% vs a median of {s['median']:.2f}%. Possibly an incomplete disclosure."
        out[code] = {"ter_pct": ter, "breakdown": parts or None, "as_of": m.get("ter_as_of_date"),
                     "category_spread": s, "status": status, "reason": reason}
    return out


def _nav_matches(a: Any, b: Any) -> bool:
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return False
    return a == a and b == b and abs(a - b) <= max(NAV_PRINT_TOLERANCE, abs(b) * 1e-7)


def _allotted_at_latest(t: Dict[str, Any], info: Dict[str, Any]) -> bool:
    """Whether a transaction's units were allotted at the fund's latest NAV, and so have not
    been held through its latest move (the 1D column and tile leave them out).

    Read off the NAV the units were allotted at, not the trade date, which gets it wrong
    both ways: a liquid fund bought on the latest date is allotted at the PREVIOUS day's
    NAV and has earned the latest accrual, while an order placed on a Saturday is allotted
    at Monday's NAV and has not earned Friday-to-Monday. The trade date decides only when
    the NAV settles nothing (no NAV on record, a split-rescaled row, or equal NAVs)."""
    latest = _as_date(info.get("latest_date"))
    if latest is None:
        return False
    trade = _as_date(t["trade_date"])
    if t.get("units_scale", 1) == 1 and trade >= latest - datetime.timedelta(days=7):
        at_latest = _nav_matches(t.get("nav"), info.get("latest_nav"))
        at_previous = _nav_matches(t.get("nav"), info.get("nav_1d_ago"))
        if at_latest != at_previous:
            return at_latest
    return trade >= latest


def summary(pid: str) -> Dict[str, Any]:
    """Positions and KPIs for a view. Cached per (view, ledger version, market-data
    version), so the several analytics that start from it in one request share it.
    Treat the result as read-only."""
    return _summary_cached(view_key(pid), ledger_version(), get_data_version())


@cached(ttl=600, maxsize=64)
def _summary_cached(key: str, _ledger_v: int, _data_v: int) -> Dict[str, Any]:
    portfolio_ids = resolve_portfolio_ids(key)
    results, txns = replay_view(portfolio_ids)
    errors = ledger_errors(results, txns)
    if errors:
        logger.warning("Holdings view %s: %d transaction(s) can't be replayed and are left out of the values: %s",
                       key, len(errors), ", ".join(str(e["txn_id"]) for e in errors[:20]))

    # Household view: one row per scheme across portfolios (lots concatenated, not re-matched).
    by_code: Dict[int, List[ledger.Position]] = defaultdict(list)
    held_in: Dict[int, List[int]] = defaultdict(list)
    for pid_i, res in sorted(results.items()):
        for code, pos in res.positions.items():
            by_code[code].append(pos)
            held_in[code].append(pid_i)
    meta = hdb.scheme_meta(list(by_code))
    split_codes = {int(t["scheme_code"]) for t in txns if t["units_scale"] != 1}
    # Net units allotted AT each fund's latest NAV: not yet held for its latest move.
    units_new_today: Dict[int, Decimal] = defaultdict(Decimal)
    for t in txns:
        code = int(t["scheme_code"])
        if _allotted_at_latest(t, meta.get(code, {})):
            units_new_today[code] += ledger.unit_sign(t["txn_type"]) * ledger.effective_units(t)
    from app.services import holdings_analytics as ha  # local: ha imports this module
    positions = [
        _position_row(ledger.merge_positions(parts), meta.get(code, {}), held_in[code], code in split_codes,
                      units_new_today[code])
        for code, parts in by_code.items()
    ]
    # Where each fund's 1D move starts: it spans several days after a holiday or a skipped NAV.
    prev_dates = hdb.previous_nav_dates({p["scheme_code"]: _as_date(p.get("latest_date")) for p in positions})
    for p in positions:
        p["asset_class"] = ha.classify(p.get("category"), p.get("broad_category"), p.get("scheme_name"))
        p["prev_nav_date"] = prev_dates.get(p["scheme_code"])

    open_pos = [p for p in positions if not p["is_closed"]]
    priced = [p for p in open_pos if not p["flags"]["unpriced"]]
    total_value = sum(p["current_value"] for p in open_pos)
    for p in positions:
        p["weight_pct"] = p["current_value"] / total_value * 100.0 if total_value > 0 else None
    positions.sort(key=lambda p: (p["is_closed"], -p["current_value"]))
    earning = _attach_earning_now(positions)
    selling = _attach_sell_now(positions)

    pf_flows = [f for res in results.values() for f in res.portfolio_cash_flows]
    as_of = max((p["latest_date"] for p in open_pos if p["latest_date"]), default=None)
    cost_total = sum(p["cost_basis"] for p in priced)
    realised = sum(p["realised_gain"] for p in positions)
    dividends = sum(p["dividend_income"] for p in positions)
    stamp_duty = sum(p["stamp_duty"] for p in positions)
    unrealised = total_value - cost_total
    net_contributed = -sum(float(v) for _, v in pf_flows)
    # A view with nothing left open still has a money-weighted return: its flows, to the
    # last of them (every rupee is back out by then).
    xirr_pct, xirr_note = compute_xirr(pf_flows, _as_date(as_of) or max((d for d, _ in pf_flows), default=None),
                                       total_value)

    # The 1-day tile is the sum of the table's 1D column: each fund's own latest NAV move on
    # the units held before it. It used to be a one-day window of the time-weighted
    # series, which on a day AMFI had priced only some funds counted the rest as
    # unchanged, and then briefly the last date all funds shared, which dropped moves
    # already published (and disagreed with the table beside it). Only each fund's own
    # latest move is complete.
    day = ha.day_change(positions)
    # Value-weighted expense ratio over the funds that have one, and how much of the
    # money that covers -- a weighted TER over half the portfolio must say so.
    with_ter = [p for p in open_pos if p["annual_fee"] is not None]
    ter_value = sum(p["current_value"] for p in with_ter)
    annual_fee = sum(p["annual_fee"] for p in with_ter)
    since = ha.since_start(key)
    first_flow = min((d for d, _ in pf_flows), default=None)
    # Measured to the valuation date the gain is priced at; a fully exited view has none,
    # so its money is counted to its last flow (every rupee is back out by then anyway).
    days_to = _as_date(as_of) or max((d for d, _ in pf_flows), default=None)
    avg_days = ledger.average_days_invested(results.values(), days_to)
    # The portfolio XIRR waits until the MONEY has been invested for MIN_XIRR_DAYS on
    # average, not merely until the first rupee has: a small opening purchase followed a
    # fortnight later by almost all of the money would otherwise publish a yearly rate
    # annualised from ~12 days of the money's actual life, where one arbitrage expiry-day dip
    # swings it by whole percentage points. The date assumes no further purchases (each one
    # delays it).
    xirr_available_on = (first_flow + datetime.timedelta(days=MIN_XIRR_DAYS)).isoformat() \
        if (xirr_note == "too_short" and first_flow is not None) else None
    if avg_days is not None and avg_days < MIN_XIRR_DAYS and days_to is not None:
        xirr_pct, xirr_note = None, "too_short"
        xirr_available_on = (days_to + datetime.timedelta(days=math.ceil(MIN_XIRR_DAYS - avg_days))).isoformat()
    peer_incomplete = sorted(set(since.get("peer_incomplete", [])) | set(day.get("peer_incomplete", [])))
    result = {
        "portfolio_ids": portfolio_ids,
        "as_of": as_of,
        "kpis": {
            "current_value": total_value,
            "invested": cost_total,
            "unrealised_gain": unrealised,
            "unrealised_pct": unrealised / cost_total * 100.0 if cost_total > 0 else None,
            "realised_gain": realised,
            "dividend_income": dividends,
            "stamp_duty": stamp_duty,
            # Straight from the cash: value + money taken out - money paid in. It
            # equals unrealised + realised + dividends - stamp duty up to paise of
            # AMC unit rounding (units x NAV rarely equals amount - stamp exactly),
            # which is why it isn't built from those parts.
            "total_gain": total_value - net_contributed,
            # On the cash actually put in, net of withdrawals. Undefined once more has been
            # taken out than put in -- a percentage of a negative base is a sign error, not
            # a return.
            "total_gain_pct": (total_value - net_contributed) / net_contributed * 100.0 if net_contributed > 0 else None,
            "net_contributed": net_contributed,
            "xirr_pct": xirr_pct,
            "xirr_note": xirr_note,
            # When a withheld XIRR will start to show, so the tile can promise a date rather
            # than repeat a reason. Calendar days, from the money-weighted rule above.
            "xirr_available_on": xirr_available_on,
            # The oldest latest-NAV among funds held: when it trails as_of, some funds have
            # not published the newest date yet (the value uses each fund's latest NAV).
            "oldest_nav_date": min((p["latest_date"] for p in open_pos if p["latest_date"] and not p["flags"]["stale_nav"]),
                                   default=None),
            "first_investment_date": first_flow.isoformat() if first_flow is not None else None,
            # What the money is earning at today's rates (see _attach_earning_now).
            **earning,
            # What selling everything at the latest NAVs would give (see _attach_sell_now).
            **selling,
            # How long the money behind Total gain has been invested: each rupee put in counts
            # from its purchase to the valuation date (or to the redemption that took it out,
            # oldest first), averaged by amount. For a SIP about half the time since the first
            # instalment -- which is how long the gain has actually had to build.
            "avg_days_invested": avg_days,
            "days_since_first_investment": (days_to - first_flow).days if (days_to and first_flow) else None,
            # Time-weighted, since the first investment, beside the funds' own peer groups.
            "twr_since_start_pct": since["twr_pct"],
            "benchmark_since_start_pct": since["benchmark_pct"],
            "excess_since_start_pp": since["excess_pp"],
            "benchmark_name": since["benchmark_name"],
            # The same comparison in rupees: your gain vs the same cash in the peers.
            "gain_vs_peers": since["gain_vs_peers"],
            "peer_gain": since["peer_gain"],
            "day_change": day["gain"],
            "day_change_pct": day["change_pct"],
            "day_benchmark_pct": day["benchmark_change_pct"],
            "weighted_ter_pct": annual_fee / ter_value * 100.0 if ter_value > 0 else None,
            "annual_fee": annual_fee if with_ter else None,
            "ter_coverage_pct": ter_value / total_value * 100.0 if total_value > 0 else None,
            "open_positions": len(open_pos),
            "transactions": len(txns),
            # Rows the replay could not apply (see ledger_errors): the values leave them out.
            "ledger_errors": errors,
            # Held funds with no NAV to value them at: left out of value, invested and gain.
            "unpriced_funds": len(open_pos) - len(priced),
            # Held funds whose 1-day move is unknown (only the fallback price lookup had them).
            "funds_without_day_change": sum(1 for p in open_pos if p["flags"]["no_day_change"]),
            # Categories whose peer returns failed to load just now: the "vs peers" figures
            # leave them out until the next load.
            "peer_comparison_incomplete": peer_incomplete,
        },
        "positions": positions,
    }
    # A partial peer comparison is shown, never cached: the next request computes it whole.
    return NoCache(result) if peer_incomplete else result


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
    ter = ter_context(hdb.scheme_meta([code])).get(code)
    errors = [e for e in ledger_errors(results, txns) if e["scheme_code"] == code]
    return {"position": position, "ter": ter, "lots": lots, "transactions": txn_rows, "nav_series": nav_series,
            "ledger_errors": errors}


# --- Export / backup ------------------------------------------------------------------

def _csv_text(v: Any) -> Any:
    """A text cell a spreadsheet won't run as a formula: Excel and Sheets evaluate a cell
    that starts with = + - @ (or a tab / carriage return before one), so a note or a
    portfolio name typed that way is prefixed with an apostrophe, the spreadsheets' own
    "this is text" mark. Numbers are written as they are."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


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
        w.writerow([_csv_text(v) for v in (
            names.get(t["portfolio_id"], t["portfolio_id"]), t["trade_date"].isoformat(), t["txn_type"],
            t["scheme_code"], m.get("scheme_name") or "", m.get("plan_type") or "", m.get("option_type") or "",
            t["amount"], t["units"], t["nav"], t["nav_source"], t["stamp_duty"],
            t["switch_group"] or "", t["notes"] or "")])
    return buf.getvalue()


BACKUP_FORMAT = "myfinonce-holdings-backup"
BACKUP_VERSION = 1
#: After this many days the page asks for a fresh backup, once the data has changed since.
BACKUP_REMIND_DAYS = 7


def backup() -> Dict[str, Any]:
    out = {
        "format": BACKUP_FORMAT,
        "version": BACKUP_VERSION,
        "exported_at": datetime.datetime.now().isoformat(timespec="seconds"),
        **{table: [to_json(r) for r in rows] for table, rows in hdb.dump_all().items()},
    }
    hdb.record_backup(datetime.datetime.now(datetime.timezone.utc))
    return out


def backup_status() -> Dict[str, Any]:
    """When the ledger was last downloaded, and whether it changed since: the ledger lives
    only in the database volume, so the page asks for a backup when one is due."""
    s = hdb.backup_status()
    last, changed = s["last_backup_at"], s["last_change_at"]
    now = datetime.datetime.now(datetime.timezone.utc)
    days = (now - last).total_seconds() / 86400.0 if last else None
    changed_since = changed is not None and (last is None or changed > last)
    return {
        "last_backup_at": last.isoformat() if last else None,
        "days_since_backup": days,
        "changed_since_backup": changed_since,
        "backup_due": changed_since and (days is None or days >= BACKUP_REMIND_DAYS),
        "remind_after_days": BACKUP_REMIND_DAYS,
    }


def restore(payload: Dict[str, Any]) -> Dict[str, int]:
    """Loads a backup file into an EMPTY ledger. The file is checked before anything is
    written: only the tables' real columns, finite numbers, sane ids, funds that exist in
    the scheme master, and a ledger that replays (no redemption of units never held, no
    switch leg without its partner). A file that fails says why, as a 422."""
    if not isinstance(payload, dict) or payload.get("format") != BACKUP_FORMAT or payload.get("version") != BACKUP_VERSION:
        raise LedgerRejected("Not a MyFinonce holdings backup (format/version mismatch).")
    with ledger_write():
        if not hdb.ledger_is_empty():
            raise LedgerRejected("Restore only runs into an empty holdings ledger, so nothing is ever overwritten.")
        # Tables absent from an older backup (taken before a feature existed) are skipped.
        tables = {t: _parse_table(t, payload.get(t)) for t, _ in hdb.BACKUP_TABLES}
        # Backups from before goals carried amount_as_of / mandates carried pauses: the same
        # defaults the schema migration gave the live tables.
        for g in tables["goals"]:
            if "amount_as_of" not in g:
                g["amount_as_of"] = g["created_at"].date() if g.get("created_at") else datetime.date.today()
        if "sip_mandate_pauses" not in payload:
            tables["sip_mandate_pauses"] = [
                {"mandate_id": m["id"], "paused_from": m["updated_at"].date() if m.get("updated_at") else datetime.date.today()}
                for m in tables["sip_mandates"] if not m.get("active", True)
            ]
        _check_backup_ledger(tables)
        try:
            return hdb.restore_all(tables)
        except (psycopg.errors.IntegrityError, psycopg.errors.DataError) as e:
            raise LedgerRejected(f"The backup could not be loaded, so nothing was restored: {e.diag.message_primary or e}") from None


# Backup JSON carries NUMERIC as exact strings and dates as ISO strings; types
# come back by column-name convention, which every holdings table follows.
_DECIMAL_COLS = {"amount", "units", "nav", "stamp_duty", "target_pct", "target_amount",
                 "inflation_pct", "step_up_pct", "threshold"}
_DATE_COLS = {"trade_date", "start_date", "end_date", "target_date", "amount_as_of", "paused_from", "resumed_on",
              "scheduled_date"}
_MAX_ID = 2 ** 31 - 1


def _parse_table(table: str, raw: Any) -> List[Dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(r, dict) for r in raw):
        raise LedgerRejected(f"Backup table {table} is not a list of rows.")
    allowed = hdb.table_columns(table)
    rows = []
    for i, r in enumerate(raw, start=1):
        unknown = sorted(set(r) - allowed)
        if unknown:
            raise LedgerRejected(f"Backup table {table}, row {i}: unknown column(s) {', '.join(unknown[:5])}.")
        try:
            rows.append(_parse_row(r))
        except (ValueError, TypeError, ArithmeticError) as e:
            raise LedgerRejected(f"Backup table {table}, row {i}: {e}") from None
    return rows


def _parse_row(r: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(r)
    for k, v in out.items():
        if isinstance(v, float) and not math.isfinite(v):
            raise ValueError(f"{k} is not a finite number")
        if k == "id" and (isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= _MAX_ID):
            raise ValueError(f"id {v!r} is not a valid row id")
        if not isinstance(v, str):
            continue
        if k in _DECIMAL_COLS:
            d = D(v)
            if not d.is_finite():
                raise ValueError(f"{k} is not a finite number")
            out[k] = d
        elif k in _DATE_COLS:
            out[k] = datetime.date.fromisoformat(v)
        elif k.endswith("_at"):
            out[k] = datetime.datetime.fromisoformat(v)
        elif k == "switch_group":
            out[k] = uuid.UUID(v)
    return out


def _check_backup_ledger(tables: Dict[str, List[Dict[str, Any]]]) -> None:
    """Refuses a backup whose funds aren't in the scheme master yet, or whose ledger can't
    be replayed -- loading it would leave rows every view silently skips."""
    txns = tables["holding_transactions"]
    codes = {int(r["scheme_code"]) for r in txns + tables["sip_mandates"] if r.get("scheme_code") is not None}
    missing = sorted(codes - hdb.existing_scheme_codes(codes))
    if missing:
        raise LedgerRejected(f"The backup holds funds that are not in the AMFI scheme master yet "
                             f"({', '.join(str(c) for c in missing[:10])}). Run a full AMFI sync, then restore again.")
    live = [t for t in txns if t.get("deleted_at") is None]
    needed = ("portfolio_id", "scheme_code", "txn_type", "trade_date", "amount", "units", "nav")
    for t in live:
        absent = [k for k in needed if t.get(k) is None]
        if absent:
            raise LedgerRejected(f"Backup transaction {t.get('id')} has no {', '.join(absent)}.")
    by_pf: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
    for t in live:
        by_pf[t["portfolio_id"]].append(t)
    errors: List[ledger.LedgerError] = []
    for rows in by_pf.values():
        scaled = with_split_scales(rows)
        errors += ledger.replay(scaled).errors + ledger.unpaired_switch_legs(scaled)
    if errors:
        raise LedgerRejected(f"The backup's ledger can't be replayed, so nothing was restored. Transaction "
                             f"{errors[0].txn_id}: {errors[0].message}.", [e.as_dict() for e in errors])


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
