from typing import Optional

from pydantic import BaseModel


class TerBackfillStartRequest(BaseModel):
    n_months: int = 12


class HistoricalBackfillStartRequest(BaseModel):
    start_year: int = 2020
    max_chunks: Optional[int] = None
    # Resume by default: skip date ranges a previous run already completed, which
    # is what makes stopping the backfill cheap. False forgets that progress and
    # re-downloads everything -- only useful if AMFI is believed to have restated
    # history, since any overlapping run already corrects changed NAVs in place.
    resume: bool = True
