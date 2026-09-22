import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

TxnType = Literal[
    "BUY", "SIP", "REDEEM", "SWITCH", "SWITCH_IN", "SWITCH_OUT",
    "DIVIDEND_REINVEST", "DIVIDEND_PAYOUT",
]


class PortfolioCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    owner_label: Optional[str] = Field(None, max_length=80)
    benchmark_scheme_code: Optional[int] = None
    color: Optional[str] = Field(None, max_length=20)
    notes: Optional[str] = Field(None, max_length=2000)


class PortfolioUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=80)
    owner_label: Optional[str] = Field(None, max_length=80)
    benchmark_scheme_code: Optional[int] = None
    color: Optional[str] = Field(None, max_length=20)
    notes: Optional[str] = Field(None, max_length=2000)
    archived: Optional[bool] = None


class TransactionDraft(BaseModel):
    """What the add/edit form sends. Amount OR units (the other is derived);
    nav omitted means "use AMFI's NAV for that date". SWITCH is a UI-level type
    that is stored as a linked SWITCH_OUT + SWITCH_IN pair."""
    portfolio_id: int
    scheme_code: int
    txn_type: TxnType
    trade_date: datetime.date
    amount: Optional[float] = Field(None, ge=0)
    units: Optional[float] = Field(None, ge=0)
    nav: Optional[float] = Field(None, gt=0)
    apply_stamp_duty: bool = True
    redeem_all: bool = False
    switch_to_scheme_code: Optional[int] = None
    switch_in_date: Optional[datetime.date] = None
    switch_in_nav: Optional[float] = Field(None, gt=0)
    notes: Optional[str] = Field(None, max_length=500)


class PreviewRequest(TransactionDraft):
    replace_txn_id: Optional[int] = None


class TargetsUpdate(BaseModel):
    """Asset class -> target percent. Must total 100; an empty map clears targets."""
    targets: Dict[str, float]


class SipMandateCreate(BaseModel):
    portfolio_id: int
    scheme_code: int
    amount: float = Field(..., gt=0)
    day_of_month: int = Field(..., ge=1, le=28)
    start_date: datetime.date
    end_date: Optional[datetime.date] = None
    step_up_pct: float = Field(0.0, ge=0, le=100)
    notes: Optional[str] = Field(None, max_length=500)


class SipMandateUpdate(BaseModel):
    amount: Optional[float] = Field(None, gt=0)
    day_of_month: Optional[int] = Field(None, ge=1, le=28)
    end_date: Optional[datetime.date] = None
    step_up_pct: Optional[float] = Field(None, ge=0, le=100)
    active: Optional[bool] = None
    notes: Optional[str] = Field(None, max_length=500)


class GoalSave(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    target_amount: float = Field(..., gt=0)
    target_date: datetime.date
    inflation_pct: float = Field(6.0, ge=0, le=30)
    portfolio_ids: List[int] = Field(default_factory=list)
    notes: Optional[str] = Field(None, max_length=1000)
    archived: bool = False


class AlertRuleCreate(BaseModel):
    kind: Literal["drift", "drawdown", "stale_nav", "regular_plan"]
    portfolio_id: Optional[int] = None
    threshold: Optional[float] = Field(None, ge=0, le=100)


class AlertRuleUpdate(BaseModel):
    threshold: Optional[float] = Field(None, ge=0, le=100)
    active: Optional[bool] = None
