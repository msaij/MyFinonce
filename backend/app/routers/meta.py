from fastapi import APIRouter

from app.core.config import product_flags
from app.db import queries as db
from app.db.connection import get_data_version
from app import amfi_sync
from app.schemas.meta import MetaFilters, MetaStatus

router = APIRouter(prefix="/api/meta", tags=["meta"])


@router.get("/status", response_model=MetaStatus)
def status() -> MetaStatus:
    """One consolidated poll target for the frontend's global date-range store
    (see the migration plan) -- data_version + DB bounds/counts + staleness."""
    stats = db.get_database_stats()
    is_stale, _current_max, expected = amfi_sync.is_database_stale()
    return MetaStatus(
        data_version=get_data_version(),
        schemes_count=stats["schemes_count"],
        amc_count=stats["amc_count"],
        nav_count=stats["nav_count"],
        file_size_mb=stats["file_size_mb"],
        min_date=stats.get("min_date"),
        max_date=stats.get("max_date"),
        db_path=stats["db_path"],
        is_stale=is_stale,
        expected_date=expected,
        ter_records_count=stats.get("ter_count", 0),
        ter_official_schemes=stats.get("ter_official_schemes", 0),
        flags=product_flags(),
    )


@router.get("/data-quality")
def data_quality() -> dict:
    return db.get_data_quality()


@router.get("/filters", response_model=MetaFilters)
def filters(broad_category: str | None = None) -> MetaFilters:
    return MetaFilters(
        amcs=db.get_amcs(),
        broad_categories=db.get_broad_categories(),
        sub_categories=db.get_subcategories(broad_category),
        options=db.get_options_list(),
        plans=db.get_plans_list(),
    )
