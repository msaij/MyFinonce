from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class TerBackfillStartRequest(BaseModel):
    n_months: int = 12
    #: False forgets the job's checkpoints first, so months already marked done are fetched
    #: again. Needed to repair stored data: with the default the run would skip exactly the
    #: months that need correcting, because being wrong is not the same as being absent.
    resume: bool = True


class HistoricalBackfillStartRequest(BaseModel):
    start_year: int = 2020
    max_chunks: Optional[int] = None
    # Resume by default: skip date ranges a previous run already completed, which
    # is what makes stopping the backfill cheap. False forgets that progress and
    # re-downloads everything -- only useful if AMFI is believed to have restated
    # history, since any overlapping run already corrects changed NAVs in place.
    resume: bool = True


class AuditSample(BaseModel):
    """One offending row. `values` holds whatever makes the contradiction visible -- the two
    TERs of a Direct/Regular pair, the ISIN two codes share -- so it varies by check."""
    scheme_code: Optional[int] = None
    label: str
    values: Dict[str, Any] = Field(default_factory=dict)


class AuditCheck(BaseModel):
    name: str
    title: str
    severity: Literal["error", "warning", "info"]
    #: "not_run" is a check whose input does not exist yet (no restatement run recorded,
    #: no provenance columns); "error" is a check whose own query failed.
    status: Literal["pass", "flagged", "not_run", "error"]
    count: Optional[int] = None
    description: str
    samples: List[AuditSample] = Field(default_factory=list)
    detail: Optional[Dict[str, Any]] = None
    elapsed_ms: Optional[int] = None


class AuditSummary(BaseModel):
    errors: int
    error_rows: int
    warnings: int
    infos: int
    check_failures: int


class AuditRun(BaseModel):
    #: True when no audit has ever been stored; every other field is then empty.
    empty: bool = False
    run_id: Optional[int] = None
    run_at: Optional[str] = None
    trigger: Optional[str] = None
    elapsed_ms: Optional[int] = None
    summary: Optional[AuditSummary] = None
    checks: List[AuditCheck] = Field(default_factory=list)
