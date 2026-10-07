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
    -- AMFI's second ISIN column. Only a scheme with a dividend option is issued one, so
    -- its presence is the structural evidence that option_type is IDCW -- which matters
    -- because AMFI's own Option field is hand-typed free text and sometimes says
    -- "Growth" for a scheme that has one (152965 is published exactly that way).
    isin_reinvestment TEXT,
    -- Where plan_type / option_type came from: 'amfi' (AMFI's Plan/Option column), 'isin'
    -- (option read off the reinvestment ISIN), 'name' (inferred from the scheme name),
    -- 'nav_match' (amfi_sync.resolve_plan_options). NULL = written before provenance was
    -- recorded. Without it a value AMFI stated and one an old parser guessed look identical.
    plan_source TEXT,
    option_source TEXT,
    -- SEBI's riskometer label (Low ... Very High) from AMFI's fund-performance feed, and the
    -- feed's NAV date it was read on. The fund's own official risk classification, so it
    -- outranks anything the app infers from NAV volatility.
    riskometer TEXT,
    riskometer_as_of DATE,
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
-- One row per *change* in a scheme's disclosed TER, not per calendar day. AMFI republishes
-- the same figures daily, so the dense form stored ~236k rows per month to say the same
-- thing 30 times over. `ter_date` is the first date these figures were seen and `valid_to`
-- the last, so a row asserts "this TER held from ter_date through valid_to".
--
-- valid_to is what keeps a wound-up fund honest: without it the final row of a scheme that
-- stopped publishing in 2019 would read as "and it still charges this today".
CREATE TABLE IF NOT EXISTS ter_history (
    scheme_code BIGINT,
    ter_date DATE,
    valid_to DATE,
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

# Collapses the dense daily TER grid into one row per change. AMFI republishes identical
# figures every calendar day, so ~82% of the rows restated the previous day's numbers.
#
# Re-running this must be a no-op, which is why the run's end is max(COALESCE(valid_to,
# ter_date)) rather than max(ter_date): on already-compacted data each group is a single
# row, and taking max(ter_date) would silently reset every valid_to back to its start and
# throw away the interval.
#
# A run breaks on any gap in the calendar, not only on a change in value. AMFI publishes
# every day of the month for a live scheme, so a missing day means the scheme genuinely had
# no disclosure -- it had not launched, was suspended, or the fetch lost pages. Bridging
# those days would invent disclosures that never happened: measured over the 6 months held
# at the time of writing, bridging silently added 27,433 days across 1,641 schemes and moved
# their duration-weighted mean TER by up to 0.85 percentage points. Breaking instead makes
# the compaction exactly lossless -- expanding the runs reproduces the daily grid row for
# row -- and costs almost nothing, since gaps are rare.
TER_CHANGE_POINTS_SQL = """
ALTER TABLE ter_history ADD COLUMN IF NOT EXISTS valid_to DATE;
UPDATE ter_history SET valid_to = ter_date WHERE valid_to IS NULL;

CREATE TEMP TABLE _ter_runs ON COMMIT DROP AS
WITH marked AS (
    SELECT scheme_code, ter_date, valid_to, base_expense_ratio_pct, brokerage_cost_pct,
           transaction_cost_pct, statutory_levies_pct, total_ter_pct, source_url,
           CASE WHEN (base_expense_ratio_pct, brokerage_cost_pct, transaction_cost_pct,
                      statutory_levies_pct, total_ter_pct)
                     IS DISTINCT FROM
                     LAG((base_expense_ratio_pct, brokerage_cost_pct, transaction_cost_pct,
                          statutory_levies_pct, total_ter_pct))
                       OVER (PARTITION BY scheme_code ORDER BY ter_date)
                  OR LAG(COALESCE(valid_to, ter_date))
                       OVER (PARTITION BY scheme_code ORDER BY ter_date) IS DISTINCT FROM ter_date - 1
                THEN 1 ELSE 0 END AS starts_run
    FROM ter_history
), grouped AS (
    SELECT *, SUM(starts_run) OVER (PARTITION BY scheme_code ORDER BY ter_date
                                    ROWS UNBOUNDED PRECEDING) AS run_id
    FROM marked
)
SELECT scheme_code,
       min(ter_date) AS ter_date,
       max(COALESCE(valid_to, ter_date)) AS valid_to,
       min(base_expense_ratio_pct) AS base_expense_ratio_pct,
       min(brokerage_cost_pct)     AS brokerage_cost_pct,
       min(transaction_cost_pct)   AS transaction_cost_pct,
       min(statutory_levies_pct)   AS statutory_levies_pct,
       min(total_ter_pct)          AS total_ter_pct,
       min(source_url)             AS source_url
FROM grouped
GROUP BY scheme_code, run_id;

DELETE FROM ter_history;
INSERT INTO ter_history (scheme_code, ter_date, valid_to, base_expense_ratio_pct,
                         brokerage_cost_pct, transaction_cost_pct, statutory_levies_pct,
                         total_ter_pct, source_url)
SELECT scheme_code, ter_date, valid_to, base_expense_ratio_pct, brokerage_cost_pct,
       transaction_cost_pct, statutory_levies_pct, total_ter_pct, source_url
FROM _ter_runs;

CREATE INDEX IF NOT EXISTS idx_ter_history_asof ON ter_history (scheme_code, ter_date DESC);
"""

# Adds the column to databases created before the baseline carried it. The next NAV sync
# fills it in; nothing back-fills it here, because the value only exists in AMFI's daily
# file and re-deriving it from what is already stored would defeat the point of keeping it.
SCHEME_REINVESTMENT_ISIN_SQL = """
ALTER TABLE schemes ADD COLUMN IF NOT EXISTS isin_reinvestment TEXT;
"""

# Provenance for plan_type / option_type (see the baseline's comment on the columns).
# Existing rows are deliberately left NULL rather than back-filled: which signal produced a
# stored value was never recorded, and guessing it now would manufacture exactly the false
# certainty these columns exist to expose. The next NAV sync stamps every scheme AMFI's
# file speaks for; what stays NULL afterwards is the genuinely unexplained remainder.
SCHEME_FIELD_PROVENANCE_SQL = """
ALTER TABLE schemes ADD COLUMN IF NOT EXISTS plan_source TEXT;
ALTER TABLE schemes ADD COLUMN IF NOT EXISTS option_source TEXT;
"""

# Filled by the nightly plan/option resolution, which already downloads the feed that
# carries it; nothing is back-filled here because the value only exists in that feed.
SCHEME_RISKOMETER_SQL = """
ALTER TABLE schemes ADD COLUMN IF NOT EXISTS riskometer TEXT;
ALTER TABLE schemes ADD COLUMN IF NOT EXISTS riskometer_as_of DATE;
"""

# One row per FUND (not per plan/option scheme) as AMFI's fund-performance feed last
# published it: its assets, its official SEBI benchmark, and the returns AMFI itself
# computes for the Direct plan, the Regular plan and that benchmark. The nightly plan/option
# resolution already downloads this feed and used to throw all of it away. Kept at fund
# level on purpose: AUM is reported once per fund, so summing it over the four plan/option
# schemes of a fund would count the same money four times. `scheme_codes` are our schemes
# matched to the fund; `fund_house` is read off them, since the feed does not name the AMC.
# Replaced wholesale on each successful fetch, so it is always one consistent day's picture.
AMFI_FUND_SNAPSHOT_SQL = """
CREATE TABLE IF NOT EXISTS amfi_fund_snapshot (
    fund_name TEXT NOT NULL,
    sub_category TEXT NOT NULL,
    category_id INTEGER,
    fund_house TEXT,
    scheme_codes BIGINT[] NOT NULL DEFAULT '{}',
    aum_cr DOUBLE PRECISION,
    benchmark TEXT,
    riskometer TEXT,
    return_1y_direct DOUBLE PRECISION,
    return_1y_regular DOUBLE PRECISION,
    return_1y_benchmark DOUBLE PRECISION,
    return_3y_direct DOUBLE PRECISION,
    return_3y_regular DOUBLE PRECISION,
    return_3y_benchmark DOUBLE PRECISION,
    return_5y_direct DOUBLE PRECISION,
    return_5y_regular DOUBLE PRECISION,
    return_5y_benchmark DOUBLE PRECISION,
    as_of DATE NOT NULL,
    PRIMARY KEY (fund_name, sub_category)
);
CREATE INDEX IF NOT EXISTS idx_amfi_fund_snapshot_codes ON amfi_fund_snapshot USING GIN (scheme_codes);
"""

# goals.amount_as_of: the day the target amount was stated "in today's money". Inflation runs
# from that day to the target date, so the target in rupees of that date stays fixed. Measured
# from each day the page is opened instead, the inflated target shrank as the date approached
# (a Rs 70 lakh goal 146 days out lost ~Rs 1.5 lakh of inflation by its last month). Existing
# goals take the day they were created.
#
# sip_mandate_pauses: when a mandate was paused and resumed. Instalments falling inside a
# pause were never debited, so they are never offered for the ledger; before this, resuming a
# mandate offered every month it had been paused. A mandate already paused is taken to have
# been paused since its last update.
GOAL_AS_OF_AND_SIP_PAUSES_SQL = """
ALTER TABLE goals ADD COLUMN IF NOT EXISTS amount_as_of DATE;
UPDATE goals SET amount_as_of = (created_at AT TIME ZONE 'Asia/Kolkata')::date WHERE amount_as_of IS NULL;
ALTER TABLE goals ALTER COLUMN amount_as_of SET DEFAULT CURRENT_DATE;
ALTER TABLE goals ALTER COLUMN amount_as_of SET NOT NULL;
CREATE TABLE IF NOT EXISTS sip_mandate_pauses (
    id BIGSERIAL PRIMARY KEY,
    mandate_id BIGINT NOT NULL REFERENCES sip_mandates(id) ON DELETE CASCADE,
    paused_from DATE NOT NULL,
    resumed_on DATE,
    CHECK (resumed_on IS NULL OR resumed_on >= paused_from)
);
CREATE INDEX IF NOT EXISTS idx_sip_mandate_pauses_mandate ON sip_mandate_pauses (mandate_id);
INSERT INTO sip_mandate_pauses (mandate_id, paused_from)
SELECT m.id, (m.updated_at AT TIME ZONE 'Asia/Kolkata')::date FROM sip_mandates m
WHERE NOT m.active AND NOT EXISTS (SELECT 1 FROM sip_mandate_pauses p WHERE p.mandate_id = m.id);
"""

MIGRATIONS = [
    ("0001_baseline", BASELINE_SQL),
    ("0002_holdings_core", HOLDINGS_CORE_SQL),
    ("0003_holdings_targets", HOLDINGS_TARGETS_SQL),
    ("0004_holdings_planning", HOLDINGS_PLANNING_SQL),
    ("0005_ter_change_points", TER_CHANGE_POINTS_SQL),
    ("0006_scheme_reinvestment_isin", SCHEME_REINVESTMENT_ISIN_SQL),
    ("0007_scheme_field_provenance", SCHEME_FIELD_PROVENANCE_SQL),
    ("0008_scheme_riskometer", SCHEME_RISKOMETER_SQL),
    ("0009_amfi_fund_snapshot", AMFI_FUND_SNAPSHOT_SQL),
    ("0010_goal_as_of_sip_pauses", GOAL_AS_OF_AND_SIP_PAUSES_SQL),
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
