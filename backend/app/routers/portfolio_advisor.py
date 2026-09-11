import datetime
from typing import Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app import portfolio_advisor
from app.core.serialize import df_to_records, sanitize_floats

router = APIRouter(prefix="/api/portfolio-advisor", tags=["portfolio-advisor"])


@router.get("/questionnaire")
def questionnaire() -> dict:
    return {
        "questions": portfolio_advisor.RISK_QUESTIONNAIRE,
        "risk_tiers": portfolio_advisor.RISK_TIERS,
        "sleeve_allocations": portfolio_advisor.SLEEVE_ALLOCATIONS,
        "score_weights": portfolio_advisor.SCORE_WEIGHTS,
    }


class ScoreRequest(BaseModel):
    answers: Dict[str, int]  # {question_key: point_value}
    horizon_years: Optional[float] = None


@router.post("/score")
def score(req: ScoreRequest) -> dict:
    total, tier = portfolio_advisor.score_questionnaire(req.answers)
    result = {"score": total, "risk_tier": tier}
    if req.horizon_years is not None:
        result["suitability_warning"] = portfolio_advisor.suitability_warning(tier, req.horizon_years)
    return result


class SuggestRequest(BaseModel):
    risk_tier: str
    budget: float
    mode: str  # "Lump Sum" or "SIP (Monthly)"
    lump_sum_amount: float = 0.0
    sip_amount: float = 0.0
    start_date: datetime.date
    end_date: datetime.date


def _serialize_backtest(bt: Optional[dict]) -> Optional[dict]:
    if bt is None:
        return None
    if "error" in bt:
        return {"error": bt["error"]}
    bt = dict(bt)
    df_result = bt.pop("df_result")
    return sanitize_floats({**bt, "df_result": df_to_records(df_result)})


@router.post("/suggest")
def suggest(req: SuggestRequest) -> dict:
    bt_mode = "SIP" if req.mode.startswith("SIP") else "Lump Sum"

    rules_result = portfolio_advisor.build_rules_based_portfolio(req.risk_tier, req.start_date, req.end_date, req.budget)
    if "error" in rules_result:
        return {"rules_result": {"error": rules_result["error"]}, "mvo_result": None, "rules_backtest": None, "mvo_backtest": None}

    mvo_result = portfolio_advisor.build_mvo_portfolio(req.risk_tier, req.start_date, req.end_date, req.budget, rules_result)

    rules_backtest = portfolio_advisor.backtest_portfolio(
        rules_result.get("weights", {}), req.start_date, req.end_date, bt_mode, req.lump_sum_amount, req.sip_amount
    )
    mvo_backtest = None
    if "error" not in mvo_result:
        mvo_backtest = portfolio_advisor.backtest_portfolio(
            mvo_result.get("weights", {}), req.start_date, req.end_date, bt_mode, req.lump_sum_amount, req.sip_amount
        )

    # shortlists holds DataFrames -- internal plumbing for build_mvo_portfolio, never
    # rendered client-side in the original page either. Strip before JSON serialization.
    rules_result_out = {k: v for k, v in rules_result.items() if k != "shortlists"}
    mvo_result_out = {k: v for k, v in mvo_result.items() if k != "shortlists"} if mvo_result else mvo_result

    return {
        "rules_result": sanitize_floats(rules_result_out),
        "mvo_result": sanitize_floats(mvo_result_out),
        "rules_backtest": _serialize_backtest(rules_backtest),
        "mvo_backtest": _serialize_backtest(mvo_backtest),
    }
