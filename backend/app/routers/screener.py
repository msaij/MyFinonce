import datetime
import io
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.core.serialize import df_to_records, sanitize_floats
from app.db import queries as db

router = APIRouter(prefix="/api/screener", tags=["screener"])


class ScreenerFilters:
    """Shared query params across all three screener endpoints -- FastAPI
    dependency so they're declared once instead of repeated per route."""

    def __init__(
        self,
        amc: Optional[str] = None,
        broad_cat: Optional[str] = None,
        sub_cat: Optional[str] = None,
        plan_type: Optional[str] = None,
        option_type: Optional[str] = None,
        search_term: Optional[str] = None,
        start: Optional[datetime.date] = None,
        end: Optional[datetime.date] = None,
        scheme_code: Optional[int] = None,
        max_expense_ratio: Optional[float] = None,
        official_ter_only: bool = False,
    ):
        self.amc = amc
        self.broad_cat = broad_cat
        self.sub_cat = sub_cat
        self.plan_type = plan_type
        self.option_type = option_type
        self.search_term = search_term
        self.start = start
        self.end = end
        self.scheme_code = scheme_code
        self.max_expense_ratio = max_expense_ratio
        self.official_ter_only = official_ter_only

    def as_kwargs(self) -> dict:
        start = self.start
        end = self.end
        if start and end and start > end:
            start, end = end, start
        return dict(
            amc=self.amc, broad_cat=self.broad_cat, sub_cat=self.sub_cat,
            plan_type=self.plan_type, option_type=self.option_type,
            search_term=self.search_term, start_date=start, end_date=end,
            scheme_code=self.scheme_code, max_expense_ratio=self.max_expense_ratio,
            official_ter_only=self.official_ter_only,
        )


@router.get("")
def screener(
    f: ScreenerFilters = Depends(),
    sort_by: Optional[str] = None,
    ascending: bool = True,
    limit: int = 1000,
) -> list[dict]:
    df = db.get_screener_dataframe(sort_by=sort_by, ascending=ascending, limit=limit, **f.as_kwargs())
    return df_to_records(df)


@router.get("/kpis")
def screener_kpis(f: ScreenerFilters = Depends()) -> dict:
    kpis = db.get_kpis(**f.as_kwargs())
    return sanitize_floats(kpis)


@router.get("/export.csv")
def screener_export_csv(
    f: ScreenerFilters = Depends(),
    sort_by: Optional[str] = None,
    ascending: bool = True,
) -> StreamingResponse:
    df = db.get_screener_dataframe(sort_by=sort_by, ascending=ascending, limit=100_000, **f.as_kwargs())
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=screener_export.csv"},
    )
