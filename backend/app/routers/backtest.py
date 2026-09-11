import datetime
from typing import Dict, List

from fastapi import APIRouter
from pydantic import BaseModel

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
