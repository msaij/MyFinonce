import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# What a draft may say. SWITCH is stored as a linked SWITCH_OUT + SWITCH_IN pair; the legs
# can't be sent one at a time -- a lone leg moves units with no money crossing the
# portfolio boundary, so it would read as pure gain (or loss).
TxnType = Literal["BUY", "SIP", "REDEEM", "SWITCH", "DIVIDEND_REINVEST", "DIVIDEND_PAYOUT"]

# Upper bounds a little inside the NUMERIC columns they land in -- amount NUMERIC(18,2),
# units NUMERIC(20,6), nav NUMERIC(14,4) -- so a typo with extra zeros is a 422 that says
# which field, not a database overflow and a 500.
MAX_RUPEES = 1e15
MAX_UNITS = 1e13
MAX_NAV = 1e9


class _Input(BaseModel):
    # JSON's NaN and Infinity are not amounts.
    model_config = ConfigDict(allow_inf_nan=False)


class PortfolioCreate(_Input):
    name: str = Field(..., min_length=1, max_length=80)
    owner_label: Optional[str] = Field(None, max_length=80)
    benchmark_scheme_code: Optional[int] = None
    color: Optional[str] = Field(None, max_length=20)
    notes: Optional[str] = Field(None, max_length=2000)


class PortfolioUpdate(_Input):
    name: Optional[str] = Field(None, min_length=1, max_length=80)
    owner_label: Optional[str] = Field(None, max_length=80)
    benchmark_scheme_code: Optional[int] = None
    color: Optional[str] = Field(None, max_length=20)
    notes: Optional[str] = Field(None, max_length=2000)
    archived: Optional[bool] = None


class TransactionDraft(_Input):
    """What the add/edit form sends. Amount OR units (the other is derived);
    nav omitted means "use AMFI's NAV for that date". SWITCH is a UI-level type
    that is stored as a linked SWITCH_OUT + SWITCH_IN pair.

    nav_confirmed: the owner has checked a typed NAV that is far from AMFI's (or a clean
    split ratio away from it) against their statement."""
    portfolio_id: int
    scheme_code: int
    txn_type: TxnType
    trade_date: datetime.date
    amount: Optional[float] = Field(None, ge=0, le=MAX_RUPEES)
    units: Optional[float] = Field(None, ge=0, le=MAX_UNITS)
    nav: Optional[float] = Field(None, gt=0, le=MAX_NAV)
    apply_stamp_duty: bool = True
    redeem_all: bool = False
    switch_to_scheme_code: Optional[int] = None
    switch_in_date: Optional[datetime.date] = None
    switch_in_nav: Optional[float] = Field(None, gt=0, le=MAX_NAV)
    nav_confirmed: bool = False
    notes: Optional[str] = Field(None, max_length=500)


class PreviewRequest(TransactionDraft):
    replace_txn_id: Optional[int] = None


class SipMandateCreate(_Input):
    portfolio_id: int
    scheme_code: int
    amount: float = Field(..., gt=0, le=MAX_RUPEES)
    day_of_month: int = Field(..., ge=1, le=28)
    start_date: datetime.date
    end_date: Optional[datetime.date] = None
    step_up_pct: float = Field(0.0, ge=0, le=100)
    notes: Optional[str] = Field(None, max_length=500)


class SipMandateUpdate(_Input):
    amount: Optional[float] = Field(None, gt=0, le=MAX_RUPEES)
    day_of_month: Optional[int] = Field(None, ge=1, le=28)
    end_date: Optional[datetime.date] = None
    step_up_pct: Optional[float] = Field(None, ge=0, le=100)
    active: Optional[bool] = None
    notes: Optional[str] = Field(None, max_length=500)


class GoalSave(_Input):
    name: str = Field(..., min_length=1, max_length=80)
    target_amount: float = Field(..., gt=0, le=MAX_RUPEES)
    target_date: datetime.date
    inflation_pct: float = Field(6.0, ge=0, le=30)
    portfolio_ids: List[int] = Field(default_factory=list)
    notes: Optional[str] = Field(None, max_length=1000)
    archived: bool = False


class AlertRuleCreate(_Input):
    # "drift" rules need per-portfolio targets, which nothing in the page can set.
    kind: Literal["drawdown", "stale_nav", "regular_plan"]
    portfolio_id: Optional[int] = None
    threshold: Optional[float] = Field(None, ge=0, le=100)
