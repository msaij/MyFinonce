"""Ordered schema migrations for durable tables (Alembic-compatible version table).

summary_table is a rebuildable cache and is NOT versioned here.
"""

from __future__ import annotations

import logging

from app.db.connection import get_connection

logger = logging.getLogger(__name__)

BASELINE_SQL = """
CREATE TABLE IF NOT EXISTS schemes (
    scheme_code BIGINT PRIMARY KEY,
    scheme_name TEXT,
    fund_house TEXT,
    category TEXT,
    plan_type TEXT,
    option_type TEXT,
    isin TEXT,
    expense_ratio DOUBLE PRECISION,
    ter_status TEXT,
    ter_source TEXT,
    ter_source_url TEXT,
    ter_as_of_date DATE,
    ter_base_expense_ratio DOUBLE PRECISION,
    ter_brokerage_cost_pct DOUBLE PRECISION,
    ter_transaction_cost_pct DOUBLE PRECISION,
    ter_statutory_levies_pct DOUBLE PRECISION,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS nav_history (
    scheme_code BIGINT,
    nav_date DATE,
    nav DOUBLE PRECISION,
    PRIMARY KEY (scheme_code, nav_date)
);
CREATE TABLE IF NOT EXISTS ter_history (
    scheme_code BIGINT,
    ter_date DATE,
    base_expense_ratio_pct DOUBLE PRECISION,
    brokerage_cost_pct DOUBLE PRECISION,
    transaction_cost_pct DOUBLE PRECISION,
    statutory_levies_pct DOUBLE PRECISION,
    total_ter_pct DOUBLE PRECISION,
    source_url TEXT,
    PRIMARY KEY (scheme_code, ter_date)
);
CREATE TABLE IF NOT EXISTS sync_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS sync_checkpoint (
    job TEXT NOT NULL,
    unit TEXT NOT NULL,
    state TEXT NOT NULL,
    rows_written BIGINT DEFAULT 0,
    detail TEXT,
    updated_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (job, unit)
);
CREATE INDEX IF NOT EXISTS idx_schemes_house ON schemes(fund_house);
CREATE INDEX IF NOT EXISTS idx_schemes_cat ON schemes(category);
CREATE INDEX IF NOT EXISTS idx_ter_history_code ON ter_history(scheme_code);
CREATE INDEX IF NOT EXISTS idx_nav_history_date ON nav_history(nav_date);
CREATE INDEX IF NOT EXISTS idx_nav_history_cov ON nav_history (scheme_code, nav_date) INCLUDE (nav);
"""

MIGRATIONS = [
    ("0001_baseline", BASELINE_SQL),
]


def run_migrations() -> None:
    con = get_connection()
    try:
        con.execute(
            "CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) PRIMARY KEY)"
        )
        applied = {r[0] for r in con.execute("SELECT version_num FROM alembic_version").fetchall()}
        for version, sql in MIGRATIONS:
            if version in applied:
                continue
            con.execute(sql)
            logger.info("Applied schema migration %s", version)
        con.execute("DROP TABLE IF EXISTS data_quality_snapshot")
        head = MIGRATIONS[-1][0]
        con.execute("DELETE FROM alembic_version")
        con.execute("INSERT INTO alembic_version (version_num) VALUES (%s)", (head,))
    finally:
        con.close()
