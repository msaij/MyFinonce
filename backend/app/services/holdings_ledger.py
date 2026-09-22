"""Holdings ledger replay: transactions in, positions / FIFO lots / cash flows out.

Pure functions -- no database, no NAV lookups, no clock. The DB layer hands in
fully-resolved transaction rows (units already on AMFI's split-adjusted scale via
`units_scale`, see holdings_valuation.split_scale) and gets back positions. That
keeps every rule here unit-testable with plain literals.

Arithmetic is `Decimal` end to end: the ledger's columns are NUMERIC, and the
over-redemption check ("you can't sell more units than you held on that date")
is an equality-sensitive comparison that binary floats get subtly wrong.

Conventions, stated once because every screen depends on them:

* `amount` is money that crossed the investor's bank account. For a purchase it
  is the GROSS amount paid (stamp duty included), so it is also the lot's cost
  basis. For a redemption it is the proceeds received.
* Realised gain uses FIFO lots within one portfolio -- the same convention AMC
  and RTA statements use. It is a performance number, not a tax computation
  (tax features are out of scope by product decision).
* DIVIDEND_REINVEST adds a lot at its NAV, costed at the dividend amount. It is
  NOT an external cash flow: the money never left the fund.
* DIVIDEND_PAYOUT adds no units; it is income and an external inflow.
* SWITCH_OUT/SWITCH_IN are a redemption + purchase pair. At the holding level
  they are real flows (money left fund A, entered fund B); at the portfolio
  level they net out, because the money never left the portfolio.
"""

from __future__ import annotations

import datetime
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple

INFLOW_TYPES = ("BUY", "SIP", "SWITCH_IN", "DIVIDEND_REINVEST")
OUTFLOW_TYPES = ("REDEEM", "SWITCH_OUT")
ALL_TYPES = INFLOW_TYPES + OUTFLOW_TYPES + ("DIVIDEND_PAYOUT",)

# Statements print units to 3 decimals (some AMCs 4) while the ledger stores 6, so
# "redeem everything" typed from a statement can exceed the replayed balance by a
# rounding crumb. Anything within this tolerance is treated as the full balance.
UNIT_TOLERANCE = Decimal("0.0005")

ZERO = Decimal("0")


def D(value: Any) -> Decimal:
    """Decimal from DB NUMERIC (already Decimal), int, str or float -- floats go
    via str() so 0.1 becomes Decimal('0.1'), not its binary approximation."""
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


@dataclass
class Lot:
    txn_id: Any
    date: datetime.date
    units: Decimal
    cost: Decimal

    @property
    def cost_nav(self) -> Optional[Decimal]:
        return (self.cost / self.units) if self.units > 0 else None


@dataclass
class Position:
    scheme_code: int
    lots: Deque[Lot] = field(default_factory=deque)
    total_invested: Decimal = ZERO       # gross purchases + switch-ins (not reinvested dividends)
    total_redeemed: Decimal = ZERO       # redemption + switch-out proceeds
    realised_gain: Decimal = ZERO        # FIFO capital gain on units sold
    dividend_income: Decimal = ZERO      # payouts + reinvested dividends
    dividend_reinvested: Decimal = ZERO
    first_date: Optional[datetime.date] = None
    last_date: Optional[datetime.date] = None
    # Holding-level external flows (investor perspective: out of pocket is negative).
    cash_flows: List[Tuple[datetime.date, Decimal]] = field(default_factory=list)
    txn_count: int = 0

    @property
    def units(self) -> Decimal:
        return sum((lot.units for lot in self.lots), ZERO)

    @property
    def cost_basis(self) -> Decimal:
        return sum((lot.cost for lot in self.lots), ZERO)

    @property
    def avg_cost_nav(self) -> Optional[Decimal]:
        u = self.units
        return (self.cost_basis / u) if u > 0 else None

    @property
    def is_closed(self) -> bool:
        return self.units <= UNIT_TOLERANCE


@dataclass
class LedgerError:
    txn_id: Any
    scheme_code: int
    trade_date: datetime.date
    message: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "txn_id": self.txn_id,
            "scheme_code": self.scheme_code,
            "trade_date": self.trade_date.isoformat(),
            "message": self.message,
        }


@dataclass
class LedgerResult:
    positions: Dict[int, Position]
    errors: List[LedgerError]
    # Portfolio-level external flows: switches excluded (internal to the portfolio).
    portfolio_cash_flows: List[Tuple[datetime.date, Decimal]]

    @property
    def ok(self) -> bool:
        return not self.errors


def replay_order(t: Dict[str, Any]) -> tuple:
    """Sort key for replay. Same-day ordering puts inflows before outflows, so
    "bought and sold the same day" replays as possible rather than as an
    over-redemption; then insertion order (unsaved drafts last)."""
    rank = 0 if t["txn_type"] in INFLOW_TYPES else (2 if t["txn_type"] in OUTFLOW_TYPES else 1)
    tid = t.get("id")
    return (t["trade_date"], rank, tid if isinstance(tid, int) else 10**18)


def unit_sign(txn_type: str) -> int:
    """+1 if the transaction adds units, -1 if it removes them, 0 otherwise."""
    return 1 if txn_type in INFLOW_TYPES else -1 if txn_type in OUTFLOW_TYPES else 0


def effective_units(t: Dict[str, Any]) -> Decimal:
    """Units on AMFI's split-adjusted scale (see module docstring)."""
    return D(t["units"]) * D(t.get("units_scale", 1))


def merge_positions(parts: Iterable[Position]) -> Position:
    """One scheme's positions from several portfolios combined for the household
    view. Lots are concatenated, never re-matched across portfolios (FIFO runs
    per portfolio); totals and cash flows add."""
    parts = list(parts)
    merged = Position(scheme_code=parts[0].scheme_code)
    for p in parts:
        merged.lots.extend(p.lots)
        merged.cash_flows.extend(p.cash_flows)
        for f in ("total_invested", "total_redeemed", "realised_gain", "dividend_income", "dividend_reinvested", "txn_count"):
            setattr(merged, f, getattr(merged, f) + getattr(p, f))
        merged.first_date = min(filter(None, (merged.first_date, p.first_date)), default=None)
        merged.last_date = max(filter(None, (merged.last_date, p.last_date)), default=None)
    return merged


def replay(transactions: Iterable[Dict[str, Any]]) -> LedgerResult:
    """Replays one portfolio's transactions (any schemes, any order).

    Each transaction needs: id, scheme_code, txn_type, trade_date (date), amount,
    units, and optionally units_scale (default 1). Deleted rows must already be
    filtered out by the caller.

    Never raises on a bad ledger: problems come back as LedgerError entries so
    the write path can refuse a change *and* say exactly which later
    transaction it would break."""
    positions: Dict[int, Position] = {}
    errors: List[LedgerError] = []
    pf_flows: List[Tuple[datetime.date, Decimal]] = []

    for t in sorted(transactions, key=replay_order):
        code = int(t["scheme_code"])
        ttype = t["txn_type"]
        date = t["trade_date"]
        amount = D(t["amount"])
        units = effective_units(t)
        pos = positions.setdefault(code, Position(scheme_code=code))
        pos.txn_count += 1
        pos.first_date = date if pos.first_date is None else min(pos.first_date, date)
        pos.last_date = date if pos.last_date is None else max(pos.last_date, date)

        if ttype not in ALL_TYPES:
            errors.append(LedgerError(t.get("id"), code, date, f"Unknown transaction type {ttype!r}"))
            continue

        if ttype in INFLOW_TYPES:
            if units <= 0:
                errors.append(LedgerError(t.get("id"), code, date, "A purchase must add a positive number of units"))
                continue
            pos.lots.append(Lot(txn_id=t.get("id"), date=date, units=units, cost=amount))
            if ttype == "DIVIDEND_REINVEST":
                pos.dividend_income += amount
                pos.dividend_reinvested += amount
            else:
                pos.total_invested += amount
                pos.cash_flows.append((date, -amount))
                if ttype != "SWITCH_IN":
                    pf_flows.append((date, -amount))

        elif ttype in OUTFLOW_TYPES:
            held = pos.units
            if units <= 0:
                errors.append(LedgerError(t.get("id"), code, date, "A redemption must remove a positive number of units"))
                continue
            if units > held + UNIT_TOLERANCE:
                errors.append(LedgerError(
                    t.get("id"), code, date,
                    f"Redeems {units.normalize():f} units but only {held.normalize():f} were held on {date.isoformat()}",
                ))
                continue
            to_sell = min(units, held)
            cost_consumed = ZERO
            while to_sell > 0 and pos.lots:
                lot = pos.lots[0]
                take = min(lot.units, to_sell)
                portion = lot.cost * (take / lot.units) if lot.units > 0 else ZERO
                cost_consumed += portion
                lot.units -= take
                lot.cost -= portion
                to_sell -= take
                if lot.units <= UNIT_TOLERANCE:
                    pos.lots.popleft()
            # Selling "everything" within the tolerance clears any crumb lots too.
            if pos.units <= UNIT_TOLERANCE:
                cost_consumed += pos.cost_basis
                pos.lots.clear()
            pos.realised_gain += amount - cost_consumed
            pos.total_redeemed += amount
            pos.cash_flows.append((date, amount))
            if ttype != "SWITCH_OUT":
                pf_flows.append((date, amount))

        else:  # DIVIDEND_PAYOUT
            pos.dividend_income += amount
            pos.cash_flows.append((date, amount))
            pf_flows.append((date, amount))

    return LedgerResult(positions=positions, errors=errors, portfolio_cash_flows=pf_flows)


def units_held_on(transactions: Iterable[Dict[str, Any]], scheme_code: int, on: datetime.date) -> Decimal:
    """Units of one scheme held at the end of `on` (used by the add-transaction
    preview to show "you hold N units" and to cap a full redemption)."""
    subset = [t for t in transactions if int(t["scheme_code"]) == int(scheme_code) and t["trade_date"] <= on]
    pos = replay(subset).positions.get(int(scheme_code))
    return pos.units if pos else ZERO
