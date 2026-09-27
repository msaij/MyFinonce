import datetime
from typing import Dict, List, Literal, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.serialize import df_to_records, sanitize_floats
from app.services import backtest as backtest_service

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


class BacktestRequest(BaseModel):
    scheme_codes: List[int]
    weights: Dict[int, float]  # raw (not necessarily normalized) weights, keyed by scheme_code
    mode: str  # "Lump Sum" or "SIP (Monthly)"
    lump_sum_amount: float = 0.0
    sip_amount: float = 0.0
    rebalance_freq: str = "None"
    start_date: datetime.date
    end_date: datetime.date
    # Day of the month each SIP instalment is due (1-31, clamped to month end); None = the
    # day of the window's start date. A holiday rolls to the next date every fund priced.
    sip_day: Optional[int] = None
    # 0.005% on purchases, SIP instalments and switch-ins from 2020-07-01 (as the Holdings ledger).
    apply_stamp_duty: bool = True
    # Optional exit load on rebalancing sells of units held under a year (0 = frictionless).
    exit_load_pct: float = Field(0.0, ge=0.0, le=5.0)
    benchmark: Literal["none", "category", "scheme"] = "none"
    benchmark_code: Optional[int] = None


@router.post("")
def run_backtest(req: BacktestRequest) -> dict:
    result = backtest_service.run_portfolio_backtest(
        scheme_codes=req.scheme_codes,
        weights=req.weights,
        mode=req.mode,
        lump_sum_amount=req.lump_sum_amount,
        sip_amount=req.sip_amount,
        rebalance_freq=req.rebalance_freq,
        start_date=req.start_date,
        end_date=req.end_date,
        sip_day=req.sip_day,
        apply_stamp_duty=req.apply_stamp_duty,
        exit_load_pct=req.exit_load_pct,
        benchmark=req.benchmark,
        benchmark_code=req.benchmark_code,
    )
    if "error" in result:
        return {"error": result["error"], "missing_codes": result.get("missing_codes", [])}

    df_result = result.pop("df_result")
    return sanitize_floats(
        {
            **result,
            "df_result": df_to_records(df_result),
        }
    )
