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

# Holdings ledger: the owner's own portfolios and hand-entered transactions.
# Unlike every table above, nothing here can be re-downloaded from AMFI -- see the
# pgdata volume comment in docker-compose.yml before ever running `down -v`.
#
# Money and units are NUMERIC, not DOUBLE PRECISION: this is a ledger that has to
# reconcile to the paisa and to an AMC statement's 3-4 decimal units, and binary
# floats cannot represent 0.1 exactly. nav_history stays DOUBLE (it is market
# data, and the refactor design explicitly leaves its schema alone).
#
# Portfolios and transactions are never hard-deleted by the app (archive / soft
# delete), so ON DELETE RESTRICT on both foreign keys is a guard, not a workflow.
# schemes rows are only ever upserted by the AMFI merge, never deleted.
HOLDINGS_CORE_SQL = """
CREATE TABLE IF NOT EXISTS portfolios (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    owner_label TEXT,
    benchmark_scheme_code BIGINT,
    color TEXT,
    notes TEXT,
    archived BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_portfolios_active_name
    ON portfolios (lower(name)) WHERE NOT archived;
CREATE TABLE IF NOT EXISTS holding_transactions (
    id BIGSERIAL PRIMARY KEY,
    portfolio_id INTEGER NOT NULL REFERENCES portfolios(id) ON DELETE RESTRICT,
    scheme_code BIGINT NOT NULL REFERENCES schemes(scheme_code) ON DELETE RESTRICT,
    txn_type TEXT NOT NULL CHECK (txn_type IN (
        'BUY', 'SIP', 'REDEEM', 'SWITCH_IN', 'SWITCH_OUT',
        'DIVIDEND_REINVEST', 'DIVIDEND_PAYOUT')),
    trade_date DATE NOT NULL,
    amount NUMERIC(18, 2) NOT NULL CHECK (amount >= 0),
    units NUMERIC(20, 6) NOT NULL CHECK (units >= 0),
    nav NUMERIC(14, 4) NOT NULL CHECK (nav > 0),
    nav_source TEXT NOT NULL DEFAULT 'amfi_auto' CHECK (nav_source IN ('amfi_auto', 'user')),
    stamp_duty NUMERIC(12, 2) NOT NULL DEFAULT 0 CHECK (stamp_duty >= 0),
    switch_group UUID,
    sip_mandate_id BIGINT,
    source TEXT NOT NULL DEFAULT 'manual',
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_htxn_portfolio_date
    ON holding_transactions (portfolio_id, trade_date) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_htxn_portfolio_scheme
    ON holding_transactions (portfolio_id, scheme_code);
CREATE INDEX IF NOT EXISTS idx_htxn_switch_group
    ON holding_transactions (switch_group) WHERE switch_group IS NOT NULL;
"""

# Per-portfolio target allocation by asset class (holdings_analytics.ASSET_CLASSES).
# Percentages, not fractions, because that is what the owner types.
HOLDINGS_TARGETS_SQL = """
CREATE TABLE IF NOT EXISTS portfolio_targets (
    portfolio_id INTEGER NOT NULL REFERENCES portfolios(id) ON DELETE RESTRICT,
    asset_class TEXT NOT NULL,
    target_pct NUMERIC(6, 3) NOT NULL CHECK (target_pct >= 0 AND target_pct <= 100),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (portfolio_id, asset_class)
);
"""

# Phase 4: SIP mandates (a schedule that *generates* ledger rows on request --
# never silently), goals spanning portfolios, and in-app alerts.
# day_of_month is capped at 28 so every month has the date.
# holding_alerts.dedupe_key makes evaluation idempotent: re-running it after
# every sync fires each condition once per period, not once per sync.
HOLDINGS_PLANNING_SQL = """
CREATE TABLE IF NOT EXISTS sip_mandates (
    id BIGSERIAL PRIMARY KEY,
    portfolio_id INTEGER NOT NULL REFERENCES portfolios(id) ON DELETE RESTRICT,
    scheme_code BIGINT NOT NULL REFERENCES schemes(scheme_code) ON DELETE RESTRICT,
    amount NUMERIC(18, 2) NOT NULL CHECK (amount > 0),
    day_of_month SMALLINT NOT NULL CHECK (day_of_month BETWEEN 1 AND 28),
    start_date DATE NOT NULL,
    end_date DATE,
    step_up_pct NUMERIC(6, 3) NOT NULL DEFAULT 0 CHECK (step_up_pct >= 0 AND step_up_pct <= 100),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (end_date IS NULL OR end_date >= start_date)
);
CREATE INDEX IF NOT EXISTS idx_sip_mandates_portfolio ON sip_mandates (portfolio_id);
CREATE INDEX IF NOT EXISTS idx_htxn_sip_mandate
    ON holding_transactions (sip_mandate_id) WHERE sip_mandate_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS goals (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    target_amount NUMERIC(18, 2) NOT NULL CHECK (target_amount > 0),
    target_date DATE NOT NULL,
    inflation_pct NUMERIC(6, 3) NOT NULL DEFAULT 6 CHECK (inflation_pct >= 0 AND inflation_pct <= 30),
    notes TEXT,
    archived BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS goal_portfolios (
    goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    portfolio_id INTEGER NOT NULL REFERENCES portfolios(id) ON DELETE RESTRICT,
    PRIMARY KEY (goal_id, portfolio_id)
);
CREATE TABLE IF NOT EXISTS holding_alert_rules (
    id SERIAL PRIMARY KEY,
    portfolio_id INTEGER REFERENCES portfolios(id) ON DELETE RESTRICT,
    kind TEXT NOT NULL CHECK (kind IN ('drift', 'drawdown', 'stale_nav', 'regular_plan')),
    threshold NUMERIC(8, 3),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS holding_alerts (
    id BIGSERIAL PRIMARY KEY,
    rule_id INTEGER NOT NULL REFERENCES holding_alert_rules(id) ON DELETE CASCADE,
    dedupe_key TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'warning',
    message TEXT NOT NULL,
    fired_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ack_at TIMESTAMPTZ,
    UNIQUE (rule_id, dedupe_key)
);
"""

MIGRATIONS = [
    ("0001_baseline", BASELINE_SQL),
    ("0002_holdings_core", HOLDINGS_CORE_SQL),
    ("0003_holdings_targets", HOLDINGS_TARGETS_SQL),
    ("0004_holdings_planning", HOLDINGS_PLANNING_SQL),
]


def _applied_versions(recorded: set) -> set:
    """Every version up to and including the recorded head counts as applied.

    alembic_version holds a single row -- the head -- so before a second
    migration existed, "applied" was just {head}, and every migration *below*
    head looked unapplied and re-ran on each startup. That only went unnoticed
    because the baseline is all IF NOT EXISTS. MIGRATIONS is ordered, so the
    head's position defines the applied prefix."""
    order = [v for v, _ in MIGRATIONS]
    heads = [order.index(v) for v in recorded if v in order]
    if not heads:
        return set()
    return set(order[: max(heads) + 1])


def run_migrations() -> None:
    con = get_connection()
    try:
        con.execute(
            "CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) PRIMARY KEY)"
        )
        recorded = {r[0] for r in con.execute("SELECT version_num FROM alembic_version").fetchall()}
        applied = _applied_versions(recorded)
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
