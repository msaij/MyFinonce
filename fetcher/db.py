import datetime
import os
import re
import threading
import duckdb
import pandas as pd
import numpy as np
import streamlit as st
from typing import List, Optional, Tuple, Dict, Any
import costs_data

# TTL is a safety net only — bump_data_version() below proactively clears every
# st.cache_data cache on every write (sync, backfill, cost import), so a stale
# read past this TTL should never actually happen in normal operation.
_CACHE_TTL = 600

DB_PATH = os.getenv("DUCKDB_PATH", os.path.join(os.path.dirname(__file__), "data", "mutual_funds.duckdb"))

_CON = None
_LOCK = threading.Lock()

# Serializes every DB *write* path (daily sync, backfill chunks, cost import, summary-table
# rebuild) so the background sync daemon's scheduled/heartbeat runs can never race a manual
# "Sync Now" / "Recompute" click (or each other) on the same shared DuckDB connection — that
# race previously surfaced as intermittent "write-write conflict" / "Conflict on tuple
# deletion" errors when both fired at once. Reentrant so a writer that itself calls
# refresh_summary_table() (which also takes this lock) doesn't deadlock on itself.
WRITE_LOCK = threading.RLock()

_DATA_VERSION = 0
_DATA_VERSION_LOCK = threading.Lock()

def bump_data_version() -> int:
    """Marks the dataset as changed: bumps the version open UI sessions poll for
    (see date_picker.py), and clears every st.cache_data cache app-wide so the
    next query re-reads DuckDB instead of serving pre-sync results."""
    global _DATA_VERSION
    with _DATA_VERSION_LOCK:
        _DATA_VERSION += 1
        version = _DATA_VERSION
    try:
        st.cache_data.clear()
    except Exception:
        pass
    return version

def get_data_version() -> int:
    with _DATA_VERSION_LOCK:
        return _DATA_VERSION

def get_connection() -> duckdb.DuckDBPyConnection:
    """Returns a cursor on the shared singleton connection, transparently reconnecting if
    DuckDB has fatally invalidated it (a rare but observed crash from concurrent index
    mutation under load — DuckDB's own recovery path is "the database must be restarted",
    which without this check meant restarting the whole container by hand). A cheap SELECT 1
    health-check catches this before it can take down every page until a human intervenes."""
    global _CON
    with _LOCK:
        if _CON is not None:
            try:
                _CON.execute("SELECT 1")
            except Exception:
                try:
                    _CON.close()
                except Exception:
                    pass
                _CON = None
        if _CON is None:
            os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
            _CON = duckdb.connect(DB_PATH, read_only=False)
    return _CON.cursor()

def _execute_with_conflict_retry(con, sql, attempts=3, delay_seconds=1.0):
    """Retries a DDL statement once or twice on a transient DuckDB catalog write-write
    conflict — e.g. the background sync daemon's CREATE OR REPLACE racing a concurrent
    read from an open Streamlit session on the same table. A real (non-conflict) error
    still raises immediately."""
    import time
    last_err = None
    for attempt in range(attempts):
        try:
            con.execute(sql)
            return
        except duckdb.Error as e:
            if "write-write conflict" not in str(e).lower():
                raise
            last_err = e
            if attempt < attempts - 1:
                time.sleep(delay_seconds)
    raise last_err

# A fund occasionally resets its face value by a clean factor (e.g. a Liquid/Overnight
# fund rebasing from Rs 1000 to Rs 100 per unit, or an ETF splitting units to track its
# index price more directly) — a real, documented corporate-action-style event, not a
# data error. Left unadjusted, every return calculation spanning that date computes a
# fake ~-90% (or +900%) "return" from the raw NAV ratio. No genuine one-day fund return
# comes remotely close to these ratios, so any match is treated as a split.
_SPLIT_FACTORS = [2, 3, 4, 5, 10, 20, 25, 50, 100, 200, 500, 1000]
_SPLIT_CANDIDATES = sorted(set(_SPLIT_FACTORS) | {round(1.0 / f, 10) for f in _SPLIT_FACTORS})
# 5%, not 1%: the split date's NAV pair still carries that day's ordinary real price move
# layered on top of the clean split ratio (observed up to ~1.5% in practice), and every
# candidate is still >=90% away from 1.0 (no-split), so this stays nowhere near a
# plausible genuine one-day fund return.
_SPLIT_TOLERANCE = 0.05

def _is_clean_split_ratio(ratio: float) -> bool:
    if ratio is None or pd.isna(ratio) or ratio <= 0 or not np.isfinite(ratio):
        return False
    return any(abs(ratio - c) / c < _SPLIT_TOLERANCE for c in _SPLIT_CANDIDATES)

def normalize_nav_splits() -> int:
    """Serialized via WRITE_LOCK — see refresh_summary_table()."""
    with WRITE_LOCK:
        return _normalize_nav_splits_impl()

def _normalize_nav_splits_impl() -> int:
    """Detects unit-split/face-value-reset events in nav_history and back-adjusts every
    NAV before the split date by the split's own measured ratio, so the series is
    continuous for return calculations — the same "split-adjusted price" convention
    every stock/ETF data provider uses. Only ever multiplies real, already-published NAVs
    by a precisely measured factor; never invents a value. Idempotent: once adjusted, the
    boundary ratio settles near 1.0 and is never re-flagged on a later run."""
    con = get_connection()
    df = con.execute("SELECT scheme_code, nav_date, nav FROM nav_history ORDER BY scheme_code, nav_date").fetchdf()
    # A "segregated portfolio" (a side-pocket created when a debt scheme's underlying paper
    # defaults) settles with a final lump-sum recovery distribution that can coincidentally
    # land near a clean ratio — that's a one-time debt recovery, not a unit-price
    # redenomination, and back-adjusting it would fabricate a fake continuous history.
    # These are also already excluded from "current" returns by the is_active staleness
    # guard in summary_table; exclude them here too so raw nav_history charts stay honest.
    segregated_codes = set(
        con.execute("SELECT scheme_code FROM schemes WHERE scheme_name ILIKE '%segregat%'").fetchdf()["scheme_code"]
    )
    con.close()
    if df.empty:
        return 0

    df["nav"] = df["nav"].astype(float)
    df["ratio"] = df["nav"] / df.groupby("scheme_code")["nav"].shift(1)
    splits = df[df["ratio"].apply(_is_clean_split_ratio) & ~df["scheme_code"].isin(segregated_codes)][["scheme_code", "nav_date", "ratio"]]
    if splits.empty:
        return 0

    df["adj_factor"] = 1.0
    for scheme_code, group in splits.groupby("scheme_code"):
        scheme_mask = df["scheme_code"] == scheme_code
        for _, row in group.iterrows():
            df.loc[scheme_mask & (df["nav_date"] < row["nav_date"]), "adj_factor"] *= row["ratio"]

    changed = df[df["adj_factor"] != 1.0].copy()
    if changed.empty:
        return 0
    changed["nav"] = (changed["nav"] * changed["adj_factor"]).round(4)

    logger_msg_schemes = splits["scheme_code"].nunique()
    con = get_connection()
    try:
        staging = changed[["scheme_code", "nav_date", "nav"]]
        con.register("stg_nav_adjust", staging)
        con.execute("""
            UPDATE nav_history
            SET nav = s.nav
            FROM stg_nav_adjust s
            WHERE nav_history.scheme_code = s.scheme_code AND nav_history.nav_date = s.nav_date;
        """)
        try:
            con.unregister("stg_nav_adjust")
        except Exception:
            pass
    finally:
        con.close()
    print(f"NAV split normalization: adjusted {len(changed):,} historical NAV rows across {logger_msg_schemes} scheme(s).")
    return len(changed)

def refresh_summary_table():
    """Recomputes and materializes the summary table for instant UI rendering.
    Serialized via WRITE_LOCK so this can never race a concurrent sync/backfill/import write."""
    with WRITE_LOCK:
        _normalize_nav_splits_impl()
        _refresh_summary_table_impl()

def _refresh_summary_table_impl():
    con = get_connection()
    _summary_sql = """
        CREATE OR REPLACE TABLE summary_table AS
        WITH ranked_nav AS (
            SELECT 
                scheme_code,
                nav_date,
                nav,
                ROW_NUMBER() OVER (PARTITION BY scheme_code ORDER BY nav_date DESC) as rn
            FROM nav_history
        ),
        latest_nav AS (
            SELECT scheme_code, nav_date as latest_date, nav as latest_nav
            FROM ranked_nav
            WHERE rn = 1
        ),
        nav_1d AS (
            SELECT scheme_code, nav as nav_1d_ago
            FROM ranked_nav
            WHERE rn = 2
        ),
        nav_7d AS (
            SELECT scheme_code, nav as nav_7d_ago
            FROM (
                SELECT scheme_code, nav,
                       ROW_NUMBER() OVER (PARTITION BY scheme_code ORDER BY ABS(DATE_DIFF('day', nav_date, (SELECT latest_date FROM latest_nav l WHERE l.scheme_code = ranked_nav.scheme_code) - INTERVAL '7 days'))) as rn_7d
                FROM nav_history ranked_nav
                WHERE nav_date <= (SELECT latest_date FROM latest_nav l WHERE l.scheme_code = ranked_nav.scheme_code) - INTERVAL '5 days'
            ) t
            WHERE rn_7d = 1
        ),
        nav_30d AS (
            SELECT scheme_code, nav as nav_30d_ago
            FROM (
                SELECT scheme_code, nav,
                       ROW_NUMBER() OVER (PARTITION BY scheme_code ORDER BY ABS(DATE_DIFF('day', nav_date, (SELECT latest_date FROM latest_nav l WHERE l.scheme_code = ranked_nav.scheme_code) - INTERVAL '30 days'))) as rn_30d
                FROM nav_history ranked_nav
                WHERE nav_date <= (SELECT latest_date FROM latest_nav l WHERE l.scheme_code = ranked_nav.scheme_code) - INTERVAL '25 days'
            ) t
            WHERE rn_30d = 1
        ),
        nav_90d AS (
            SELECT scheme_code, nav as nav_90d_ago
            FROM (
                SELECT scheme_code, nav,
                       ROW_NUMBER() OVER (PARTITION BY scheme_code ORDER BY ABS(DATE_DIFF('day', nav_date, (SELECT latest_date FROM latest_nav l WHERE l.scheme_code = ranked_nav.scheme_code) - INTERVAL '90 days'))) as rn_90d
                FROM nav_history ranked_nav
                WHERE nav_date <= (SELECT latest_date FROM latest_nav l WHERE l.scheme_code = ranked_nav.scheme_code) - INTERVAL '75 days'
            ) t
            WHERE rn_90d = 1
        ),
        nav_1y AS (
            SELECT scheme_code, nav as nav_1y_ago
            FROM (
                SELECT scheme_code, nav,
                       ROW_NUMBER() OVER (PARTITION BY scheme_code ORDER BY ABS(DATE_DIFF('day', nav_date, (SELECT latest_date FROM latest_nav l WHERE l.scheme_code = ranked_nav.scheme_code) - INTERVAL '365 days'))) as rn_1y
                FROM nav_history ranked_nav
                WHERE nav_date <= (SELECT latest_date FROM latest_nav l WHERE l.scheme_code = ranked_nav.scheme_code) - INTERVAL '330 days'
            ) t
            WHERE rn_1y = 1
        ),
        stats_52w AS (
            SELECT
                scheme_code,
                MAX(nav) as high_52w,
                MIN(nav) as low_52w
            FROM nav_history
            GROUP BY scheme_code
        ),
        db_freshness AS (
            SELECT MAX(nav_date) as global_max_date FROM nav_history
        )
        SELECT
            s.scheme_code,
            s.scheme_name,
            s.fund_house,
            s.category,
            CASE
                WHEN s.category LIKE 'Equity Scheme%' THEN 'Equity'
                WHEN s.category LIKE 'Debt Scheme%' THEN 'Debt'
                -- Pre-2018-recategorization AMFI debt-fund naming ("Income/Debt Oriented
                -- Schemes - ..." and the bare legacy label "Income") — ~2,000 currently
                -- active schemes carry these labels but were falling into the 'Other /
                -- Index / ETF' catch-all bucket instead of 'Debt', silently excluding a
                -- large share of real debt funds from every Asset Class breakdown/filter.
                WHEN s.category LIKE 'Income/Debt Oriented Schemes%' THEN 'Debt'
                WHEN s.category = 'Income' THEN 'Debt'
                WHEN s.category LIKE 'Hybrid Scheme%' THEN 'Hybrid'
                WHEN s.category LIKE 'Solution Oriented%' THEN 'Solution Oriented'
                ELSE 'Other / Index / ETF'
            END AS broad_category,
            s.plan_type,
            s.option_type,
            s.isin,
            s.expense_ratio,
            s.ter_status,
            s.ter_source,
            s.ter_source_url,
            s.ter_as_of_date,
            s.ter_base_expense_ratio,
            s.ter_brokerage_cost_pct,
            s.ter_transaction_cost_pct,
            s.ter_statutory_levies_pct,
            s.exit_load_pct,
            s.exit_load_days,
            s.exit_load_description,
            s.exit_rule_json,
            s.exit_rule_status,
            s.exit_rule_source,
            s.exit_rule_source_url,
            s.exit_rule_as_of_date,
            s.lock_in_years,
            l.latest_date,
            l.latest_nav,
            (l.latest_date >= f.global_max_date - INTERVAL '30 days') as is_active,
            CASE WHEN l.latest_date >= f.global_max_date - INTERVAL '30 days'
                 THEN ROUND(((l.latest_nav - n1.nav_1d_ago) / NULLIF(n1.nav_1d_ago, 0) * 100.0), 4) END as change_1d_pct,
            CASE WHEN l.latest_date >= f.global_max_date - INTERVAL '30 days'
                 THEN ROUND(((l.latest_nav - n7.nav_7d_ago) / NULLIF(n7.nav_7d_ago, 0) * 100.0), 4) END as return_7d_pct,
            CASE WHEN l.latest_date >= f.global_max_date - INTERVAL '30 days'
                 THEN ROUND(((l.latest_nav - n30.nav_30d_ago) / NULLIF(n30.nav_30d_ago, 0) * 100.0), 4) END as return_30d_pct,
            CASE WHEN l.latest_date >= f.global_max_date - INTERVAL '30 days'
                 THEN ROUND(((l.latest_nav - n90.nav_90d_ago) / NULLIF(n90.nav_90d_ago, 0) * 100.0), 4) END as return_90d_pct,
            CASE WHEN l.latest_date >= f.global_max_date - INTERVAL '30 days'
                 THEN ROUND(((l.latest_nav - n1y.nav_1y_ago) / NULLIF(n1y.nav_1y_ago, 0) * 100.0), 4) END as return_1y_pct,
            st.high_52w,
            st.low_52w,
            ROUND(((l.latest_nav - st.high_52w) / NULLIF(st.high_52w, 0) * 100.0), 4) as dist_from_52w_high_pct
        FROM schemes s
        JOIN latest_nav l ON s.scheme_code = l.scheme_code
        CROSS JOIN db_freshness f
        LEFT JOIN nav_1d n1 ON s.scheme_code = n1.scheme_code
        LEFT JOIN nav_7d n7 ON s.scheme_code = n7.scheme_code
        LEFT JOIN nav_30d n30 ON s.scheme_code = n30.scheme_code
        LEFT JOIN nav_90d n90 ON s.scheme_code = n90.scheme_code
        LEFT JOIN nav_1y n1y ON s.scheme_code = n1y.scheme_code
        LEFT JOIN stats_52w st ON s.scheme_code = st.scheme_code;
    """
    try:
        _execute_with_conflict_retry(con, _summary_sql)
    finally:
        # Always close, even on an exhausted-retry failure — an unclosed cursor can leave a
        # transaction open on the shared connection and block every other reader/writer
        # behind it indefinitely.
        con.close()
    invalidate_database_stats_cache()
    bump_data_version()

def init_db():
    """Initializes tables, views, columns, and cost profiles if they do not exist."""
    con = get_connection()
    con.execute("""
        CREATE TABLE IF NOT EXISTS schemes (
            scheme_code BIGINT PRIMARY KEY,
            scheme_name VARCHAR,
            fund_house VARCHAR,
            category VARCHAR,
            plan_type VARCHAR,
            option_type VARCHAR,
            isin VARCHAR,
            expense_ratio DOUBLE,
            exit_load_pct DOUBLE,
            exit_load_days INTEGER,
            exit_load_description VARCHAR,
            lock_in_years INTEGER,
            ter_status VARCHAR,
            ter_source VARCHAR,
            ter_source_url VARCHAR,
            ter_as_of_date DATE,
            ter_base_expense_ratio DOUBLE,
            ter_brokerage_cost_pct DOUBLE,
            ter_transaction_cost_pct DOUBLE,
            ter_statutory_levies_pct DOUBLE,
            exit_rule_json VARCHAR,
            exit_rule_status VARCHAR,
            exit_rule_source VARCHAR,
            exit_rule_source_url VARCHAR,
            exit_rule_as_of_date DATE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS nav_history (
            scheme_code BIGINT,
            nav_date DATE,
            nav DECIMAL(14,4),
            PRIMARY KEY (scheme_code, nav_date)
        );
        -- Full dated history of official AMFI TER-portal disclosures (Regulation 66), one row
        -- per scheme per calendar day — the time-series counterpart to schemes' cached "latest"
        -- cost columns, the same relationship nav_history already has to summary_table.
        CREATE TABLE IF NOT EXISTS ter_history (
            scheme_code BIGINT,
            ter_date DATE,
            base_expense_ratio_pct DOUBLE,
            brokerage_cost_pct DOUBLE,
            transaction_cost_pct DOUBLE,
            statutory_levies_pct DOUBLE,
            total_ter_pct DOUBLE,
            source_url VARCHAR,
            PRIMARY KEY (scheme_code, ter_date)
        );
        CREATE INDEX IF NOT EXISTS idx_schemes_house ON schemes(fund_house);
        CREATE INDEX IF NOT EXISTS idx_schemes_cat ON schemes(category);
        DROP INDEX IF EXISTS idx_nav_code_date;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_schemes_code ON schemes(scheme_code);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_nav_code_date ON nav_history(scheme_code, nav_date);
        CREATE INDEX IF NOT EXISTS idx_ter_history_code ON ter_history(scheme_code);
    """)

    # Auto-migration for existing schemes tables lacking cost columns
    for col_name, col_type in [
        ("expense_ratio", "DOUBLE"),
        ("exit_load_pct", "DOUBLE"),
        ("exit_load_days", "INTEGER"),
        ("exit_load_description", "VARCHAR"),
        ("lock_in_years", "INTEGER"),
        ("ter_status", "VARCHAR"),
        ("ter_source", "VARCHAR"),
        ("ter_source_url", "VARCHAR"),
        ("ter_as_of_date", "DATE"),
        ("ter_base_expense_ratio", "DOUBLE"),
        ("ter_brokerage_cost_pct", "DOUBLE"),
        ("ter_transaction_cost_pct", "DOUBLE"),
        ("ter_statutory_levies_pct", "DOUBLE"),
        ("exit_rule_json", "VARCHAR"),
        ("exit_rule_status", "VARCHAR"),
        ("exit_rule_source", "VARCHAR"),
        ("exit_rule_source_url", "VARCHAR"),
        ("exit_rule_as_of_date", "DATE")
    ]:
        try:
            con.execute(f"ALTER TABLE schemes ADD COLUMN IF NOT EXISTS {col_name} {col_type};")
        except Exception:
            pass
            
    # Migrate legacy inferred values to explicit source-aware states.
    try:
        con.execute("CREATE TABLE IF NOT EXISTS sync_meta (key VARCHAR PRIMARY KEY, value VARCHAR);")
        costs_meta = con.execute("SELECT value FROM sync_meta WHERE key = 'cost_data_version';").fetchone()
        if not costs_meta or costs_meta[0] != costs_data.COST_DATA_VERSION:
            rows = con.execute("SELECT scheme_code, scheme_name, category, plan_type FROM schemes;").fetchall()
            if rows:
                cost_records = []
                for r in rows:
                    c_specs = costs_data.get_scheme_cost_specs(r[0], r[1], r[2], r[3])
                    cost_records.append({
                        "scheme_code": r[0],
                        "expense_ratio": c_specs["expense_ratio"],
                        "ter_status": c_specs["ter_status"],
                        "ter_source": c_specs["ter_source"],
                        "ter_source_url": c_specs["ter_source_url"],
                        "ter_as_of_date": c_specs["ter_as_of_date"],
                        "exit_load_pct": c_specs["exit_load_pct"],
                        "exit_load_days": c_specs["exit_load_days"],
                        "exit_load_description": c_specs["exit_load_description"],
                        "exit_rule_json": c_specs["exit_rule_json"],
                        "exit_rule_status": c_specs["exit_rule_status"],
                        "exit_rule_source": c_specs["exit_rule_source"],
                        "exit_rule_source_url": c_specs["exit_rule_source_url"],
                        "exit_rule_as_of_date": c_specs["exit_rule_as_of_date"],
                        "lock_in_years": c_specs["lock_in_years"]
                    })
                df_stg_costs = pd.DataFrame(cost_records)
                con.register("stg_costs", df_stg_costs)
                con.execute("""
                    UPDATE schemes
                    SET expense_ratio = c.expense_ratio,
                        ter_status = c.ter_status,
                        ter_source = c.ter_source,
                        ter_source_url = c.ter_source_url,
                        ter_as_of_date = c.ter_as_of_date,
                        exit_load_pct = c.exit_load_pct,
                        exit_load_days = c.exit_load_days,
                        exit_load_description = c.exit_load_description,
                        exit_rule_json = c.exit_rule_json,
                        exit_rule_status = c.exit_rule_status,
                        exit_rule_source = c.exit_rule_source,
                        exit_rule_source_url = c.exit_rule_source_url,
                        exit_rule_as_of_date = c.exit_rule_as_of_date,
                        lock_in_years = c.lock_in_years
                    FROM stg_costs c
                    WHERE schemes.scheme_code = c.scheme_code;
                """)
                try:
                    con.unregister("stg_costs")
                except Exception:
                    pass
                con.execute("INSERT OR REPLACE INTO sync_meta (key, value) VALUES ('cost_data_version', ?);", [costs_data.COST_DATA_VERSION])
                con.close()
                refresh_summary_table()
                con = get_connection()
    except Exception as e:
        print(f"Cost initialization notice: {e}")

    # Verify summary_table includes the source-aware cost fields.
    try:
        con.execute("SELECT broad_category, expense_ratio, ter_status, exit_rule_status FROM summary_table LIMIT 1;")
    except Exception:
        con.close()
        refresh_summary_table()
        con = get_connection()
    con.close()

@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def get_amcs() -> List[str]:
    con = get_connection()
    res = con.execute("SELECT DISTINCT fund_house FROM schemes WHERE fund_house IS NOT NULL AND fund_house != '' ORDER BY fund_house;").fetchall()
    con.close()
    return [r[0] for r in res]

@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def get_broad_categories() -> List[str]:
    con = get_connection()
    res = con.execute("SELECT DISTINCT broad_category FROM summary_table WHERE broad_category IS NOT NULL ORDER BY broad_category;").fetchall()
    con.close()
    return [r[0] for r in res]

@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def get_subcategories(broad_category: Optional[str] = None) -> List[str]:
    con = get_connection()
    if broad_category and broad_category != "All Categories":
        res = con.execute(
            "SELECT DISTINCT category FROM summary_table WHERE broad_category = ? AND category IS NOT NULL ORDER BY category;",
            [broad_category]
        ).fetchall()
    else:
        res = con.execute(
            "SELECT DISTINCT category FROM summary_table WHERE category IS NOT NULL ORDER BY category;"
        ).fetchall()
    con.close()
    return [r[0] for r in res]

def get_options_list() -> List[str]:
    return ["All Options", "Growth", "IDCW"]

def get_plans_list() -> List[str]:
    return ["All Plans", "Direct", "Regular"]

@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def get_market_overview_stats() -> Dict[str, Any]:
    con = get_connection()
    total_schemes = con.execute("SELECT count(*) FROM schemes;").fetchone()[0]
    total_amcs = con.execute("SELECT count(DISTINCT fund_house) FROM schemes WHERE fund_house IS NOT NULL;").fetchone()[0]
    total_nav_records = con.execute("SELECT count(*) FROM nav_history;").fetchone()[0]
    date_row = con.execute("SELECT min(nav_date), max(nav_date) FROM nav_history;").fetchone()
    
    # Asset class distribution with multi-horizon returns
    asset_dist = con.execute("""
        SELECT broad_category, count(*) as count,
               ROUND(AVG(change_1d_pct), 4) as avg_1d,
               ROUND(AVG(return_7d_pct), 4) as avg_7d,
               ROUND(AVG(return_30d_pct), 4) as avg_30d,
               ROUND(AVG(return_90d_pct), 4) as avg_90d,
               ROUND(AVG(return_1y_pct), 4) as avg_1y,
               ROUND(MEDIAN(return_30d_pct), 4) as med_30d,
               ROUND(MEDIAN(return_90d_pct), 4) as med_90d
        FROM summary_table
        GROUP BY broad_category
        ORDER BY count DESC;
    """).fetchdf()
    
    # Top 15 AMCs by scheme volume and average returns
    top_amcs = con.execute("""
        SELECT 
            s.fund_house, 
            count(*) as schemes_count,
            ROUND(AVG(st.return_30d_pct), 4) as avg_30d,
            ROUND(AVG(st.return_90d_pct), 4) as avg_90d,
            ROUND(AVG(st.return_1y_pct), 4) as avg_1y
        FROM schemes s
        LEFT JOIN summary_table st ON s.scheme_code = st.scheme_code
        WHERE s.fund_house IS NOT NULL AND s.fund_house != ''
        GROUP BY s.fund_house
        ORDER BY schemes_count DESC
        LIMIT 15;
    """).fetchdf()
    
    # Best performing category (30D)
    best_cat = con.execute("""
        SELECT category, ROUND(AVG(return_30d_pct), 4) as avg_30d
        FROM summary_table
        WHERE return_30d_pct IS NOT NULL
        GROUP BY category
        ORDER BY avg_30d DESC
        LIMIT 1;
    """).fetchone()
    
    con.close()
    return {
        "total_schemes": total_schemes,
        "total_amcs": total_amcs,
        "total_nav_records": total_nav_records,
        "min_date": date_row[0],
        "max_date": date_row[1],
        "asset_dist": asset_dist,
        "top_amcs": top_amcs,
        "best_cat": {"name": best_cat[0], "return_pct": best_cat[1]} if best_cat else None
    }

@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def get_category_performance_matrix(broad_category: str = "All") -> pd.DataFrame:
    con = get_connection()
    where_sql = ""
    params = []
    if broad_category and broad_category != "All":
        where_sql = "AND broad_category = ?"
        params = [broad_category]
    df = con.execute(f"""
        SELECT 
            broad_category AS "Asset Class",
            category AS "Category",
            count(*) AS "Schemes",
            ROUND(AVG(expense_ratio), 4) AS "Avg TER %",
            ROUND(AVG(exit_load_pct), 4) AS "Avg Exit %",
            ROUND(AVG(change_1d_pct), 4) AS "Avg 1D %",
            ROUND(AVG(return_7d_pct), 4) AS "Avg 7D %",
            ROUND(AVG(return_30d_pct), 4) AS "Avg 30D %",
            ROUND(AVG(return_90d_pct), 4) AS "Avg 90D %",
            ROUND(AVG(return_1y_pct), 4) AS "Avg 1Y %",
            ROUND(AVG(dist_from_52w_high_pct), 4) AS "52W High Gap %",
            ROUND(MAX(return_30d_pct), 4) AS "Top Fund (30D) %",
            ROUND(MIN(return_30d_pct), 4) AS "Bottom Fund (30D) %"
        FROM summary_table
        WHERE category IS NOT NULL {where_sql}
        GROUP BY broad_category, category
        ORDER BY "Avg 30D %" DESC NULLS LAST;
    """, params).fetchdf()
    con.close()
    return df

@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def get_macro_asset_class_trend(start_date: datetime.date, end_date: datetime.date, plan_type: str = "All Plans") -> pd.DataFrame:
    """
    Computes an indexed base-100 time series for the major asset classes (Equity, Debt, Hybrid)
    over the chosen date range for institutional macro trajectory visualization.
    """
    con = get_connection()
    plan_clause = "AND s.plan_type = ?" if plan_type and plan_type != "All Plans" else ""
    plan_params = [plan_type] if plan_clause else []
    
    sql = f"""
        WITH daily_navs AS (
            SELECT 
                nh.nav_date,
                s.broad_category,
                AVG(nh.nav) as mean_nav
            FROM nav_history nh
            JOIN summary_table s ON nh.scheme_code = s.scheme_code
            WHERE nh.nav_date >= ? AND nh.nav_date <= ?
              AND s.broad_category IN ('Equity', 'Debt', 'Hybrid')
              {plan_clause}
            GROUP BY nh.nav_date, s.broad_category
        ),
        base_navs AS (
            SELECT broad_category, mean_nav as base_nav
            FROM (
                SELECT broad_category, mean_nav,
                       ROW_NUMBER() OVER (PARTITION BY broad_category ORDER BY nav_date ASC) as rn
                FROM daily_navs
            ) WHERE rn = 1
        )
        SELECT 
            d.nav_date,
            d.broad_category as "Asset Class",
            ROUND((d.mean_nav / NULLIF(b.base_nav, 0)) * 100.0, 2) as "Indexed Performance"
        FROM daily_navs d
        JOIN base_navs b ON d.broad_category = b.broad_category
        ORDER BY d.nav_date ASC;
    """
    params = [start_date, end_date] + plan_params
    try:
        df = con.execute(sql, params).fetchdf()
    except Exception:
        df = pd.DataFrame(columns=["nav_date", "Asset Class", "Indexed Performance"])
    con.close()
    return df

@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def get_kpis(amc="All Fund Houses", broad_cat="All Categories", sub_cat="All Sub-Categories", plan_type="All Plans", option_type="All Options", search_term="", start_date=None, end_date=None, scheme_code=None, max_expense_ratio=None) -> Dict[str, Any]:
    con = get_connection()
    where_sql, params = _build_screener_where(amc, broad_cat, sub_cat, plan_type, option_type, search_term, scheme_code=scheme_code, max_expense_ratio=max_expense_ratio)
    
    sql_overview = f"""
        SELECT 
            count(*) as total_schemes,
            MAX(latest_date) as latest_date
        FROM summary_table s
        {where_sql};
    """
    row = con.execute(sql_overview, params).fetchone()
    total_schemes = row[0] if row else 0
    latest_date = row[1] if row else None
    
    top_performer = None
    lag_performer = None
    advancers = 0
    decliners = 0
    unchanged = 0
    median_return = 0.0
    avg_return = 0.0
    best_cat = None

    if start_date and end_date:
        sql_period_kpi = f"""
            WITH p_start AS (
                SELECT scheme_code, nav as start_nav
                FROM (
                    SELECT scheme_code, nav,
                           ROW_NUMBER() OVER (PARTITION BY scheme_code ORDER BY nav_date ASC) as rn
                    FROM nav_history
                    WHERE nav_date >= ? AND nav_date <= ?
                ) WHERE rn = 1
            ),
            p_end AS (
                SELECT scheme_code, nav as end_nav
                FROM (
                    SELECT scheme_code, nav,
                           ROW_NUMBER() OVER (PARTITION BY scheme_code ORDER BY nav_date DESC) as rn
                    FROM nav_history
                    WHERE nav_date >= ? AND nav_date <= ?
                ) WHERE rn = 1
            ),
            period_calc AS (
                SELECT s.scheme_name, s.broad_category, s.category,
                       ROUND(((pe.end_nav - ps.start_nav) / NULLIF(ps.start_nav, 0) * 100.0), 4) as period_return_pct
                FROM summary_table s
                JOIN p_start ps ON s.scheme_code = ps.scheme_code
                JOIN p_end pe ON s.scheme_code = pe.scheme_code
                {where_sql}
            )
            SELECT 
                ARG_MAX(scheme_name, period_return_pct) as top_name,
                MAX(period_return_pct) as top_return,
                ARG_MIN(scheme_name, period_return_pct) as lag_name,
                MIN(period_return_pct) as lag_return,
                COUNT(CASE WHEN period_return_pct > 0 THEN 1 END) as advancers,
                COUNT(CASE WHEN period_return_pct < 0 THEN 1 END) as decliners,
                COUNT(CASE WHEN period_return_pct = 0 THEN 1 END) as unchanged,
                ROUND(MEDIAN(period_return_pct), 4) as median_return,
                ROUND(AVG(period_return_pct), 4) as avg_return
            FROM period_calc 
            WHERE period_return_pct IS NOT NULL;
        """
        all_p = [start_date, end_date, start_date, end_date] + params
        kpi_row = con.execute(sql_period_kpi, all_p).fetchone()
        if kpi_row and kpi_row[0] is not None:
            top_performer = {"name": kpi_row[0], "return_pct": kpi_row[1]}
            lag_performer = {"name": kpi_row[2], "return_pct": kpi_row[3]}
            advancers = kpi_row[4] or 0
            decliners = kpi_row[5] or 0
            unchanged = kpi_row[6] or 0
            median_return = kpi_row[7] or 0.0
            avg_return = kpi_row[8] or 0.0
            
        # Dynamically determine best performing category for this period
        sql_best_cat = f"""
            WITH p_start AS (
                SELECT scheme_code, nav as start_nav
                FROM (
                    SELECT scheme_code, nav,
                           ROW_NUMBER() OVER (PARTITION BY scheme_code ORDER BY nav_date ASC) as rn
                    FROM nav_history
                    WHERE nav_date >= ? AND nav_date <= ?
                ) WHERE rn = 1
            ),
            p_end AS (
                SELECT scheme_code, nav as end_nav
                FROM (
                    SELECT scheme_code, nav,
                           ROW_NUMBER() OVER (PARTITION BY scheme_code ORDER BY nav_date DESC) as rn
                    FROM nav_history
                    WHERE nav_date >= ? AND nav_date <= ?
                ) WHERE rn = 1
            ),
            period_calc AS (
                SELECT s.category,
                       ROUND(((pe.end_nav - ps.start_nav) / NULLIF(ps.start_nav, 0) * 100.0), 4) as period_return_pct
                FROM summary_table s
                JOIN p_start ps ON s.scheme_code = ps.scheme_code
                JOIN p_end pe ON s.scheme_code = pe.scheme_code
                {where_sql}
            )
            SELECT category, ROUND(AVG(period_return_pct), 4) as avg_cat_ret
            FROM period_calc
            WHERE category IS NOT NULL AND period_return_pct IS NOT NULL
            GROUP BY category
            ORDER BY avg_cat_ret DESC
            LIMIT 1;
        """
        bcat_row = con.execute(sql_best_cat, all_p).fetchone()
        if bcat_row:
            best_cat = {"name": bcat_row[0], "return_pct": bcat_row[1]}
    else:
        sql_summary_kpi = f"""
            SELECT 
                ARG_MAX(scheme_name, return_30d_pct) as top_name,
                MAX(return_30d_pct) as top_return,
                ARG_MIN(scheme_name, return_30d_pct) as lag_name,
                MIN(return_30d_pct) as lag_return,
                COUNT(CASE WHEN return_30d_pct > 0 THEN 1 END) as advancers,
                COUNT(CASE WHEN return_30d_pct < 0 THEN 1 END) as decliners,
                COUNT(CASE WHEN return_30d_pct = 0 THEN 1 END) as unchanged,
                ROUND(MEDIAN(return_30d_pct), 4) as median_return,
                ROUND(AVG(return_30d_pct), 4) as avg_return
            FROM summary_table s
            {where_sql} {'AND' if where_sql else 'WHERE'} s.return_30d_pct IS NOT NULL;
        """
        kpi_row = con.execute(sql_summary_kpi, params).fetchone()
        if kpi_row and kpi_row[0] is not None:
            top_performer = {"name": kpi_row[0], "return_pct": kpi_row[1]}
            lag_performer = {"name": kpi_row[2], "return_pct": kpi_row[3]}
            advancers = kpi_row[4] or 0
            decliners = kpi_row[5] or 0
            unchanged = kpi_row[6] or 0
            median_return = kpi_row[7] or 0.0
            avg_return = kpi_row[8] or 0.0
            
        sql_best_cat = f"""
            SELECT s.category, ROUND(AVG(s.return_30d_pct), 4) as avg_cat_ret
            FROM summary_table s
            {where_sql} {'AND' if where_sql else 'WHERE'} s.category IS NOT NULL AND s.return_30d_pct IS NOT NULL
            GROUP BY s.category
            ORDER BY avg_cat_ret DESC
            LIMIT 1;
        """
        bcat_row = con.execute(sql_best_cat, params).fetchone()
        if bcat_row:
            best_cat = {"name": bcat_row[0], "return_pct": bcat_row[1]}
    
    con.close()
    return {
        "total_schemes": total_schemes,
        "latest_date": latest_date,
        "top_performer": top_performer,
        "lag_performer": lag_performer,
        "advancers": advancers,
        "decliners": decliners,
        "unchanged": unchanged,
        "median_return": median_return,
        "avg_return": avg_return,
        "best_cat": best_cat
    }

def _build_screener_where(amc="All Fund Houses", broad_cat="All Categories", sub_cat="All Sub-Categories", plan_type="All Plans", option_type="All Options", search_term="", min_return_30d=None, scheme_code=None, max_expense_ratio=None) -> Tuple[str, List[Any]]:
    clauses = []
    params = []
    
    if scheme_code is not None:
        try:
            sc_int = int(scheme_code)
            if sc_int > 0:
                clauses.append("s.scheme_code = ?")
                params.append(sc_int)
        except (ValueError, TypeError):
            pass

    if amc and amc != "All Fund Houses":
        clauses.append("s.fund_house = ?")
        params.append(amc)
        
    if broad_cat and broad_cat != "All Categories":
        clauses.append("s.broad_category = ?")
        params.append(broad_cat)
        
    if sub_cat and sub_cat != "All Sub-Categories":
        clauses.append("s.category = ?")
        params.append(sub_cat)
        
    if plan_type and plan_type != "All Plans":
        clauses.append("s.plan_type = ?")
        params.append(plan_type)
        
    if option_type and option_type != "All Options":
        clauses.append("s.option_type = ?")
        params.append(option_type)
        
    if search_term and search_term.strip():
        # Advanced multi-token all-words matching: extracts alphanumeric tokens so words match in any order and punctuation is safely ignored
        tokens = [t for t in re.findall(r'[a-zA-Z0-9]+', search_term.lower()) if t]
        for token in tokens:
            pattern = f"%{token}%"
            clauses.append(
                "("
                "COALESCE(LOWER(s.scheme_name), '') LIKE ? "
                "OR CAST(s.scheme_code AS VARCHAR) LIKE ? "
                "OR COALESCE(LOWER(s.fund_house), '') LIKE ? "
                "OR COALESCE(LOWER(s.category), '') LIKE ? "
                "OR COALESCE(LOWER(s.plan_type), '') LIKE ? "
                "OR COALESCE(LOWER(s.option_type), '') LIKE ?"
                ")"
            )
            params.extend([pattern, pattern, pattern, pattern, pattern, pattern])
        
    if min_return_30d is not None:
        clauses.append("s.return_30d_pct >= ?")
        params.append(min_return_30d)

    if max_expense_ratio is not None and max_expense_ratio > 0:
        clauses.append("s.expense_ratio <= ?")
        params.append(max_expense_ratio)
        
    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where_sql, params

@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def get_screener_dataframe(
    amc="All Fund Houses",
    broad_cat="All Categories",
    sub_cat="All Sub-Categories",
    plan_type="All Plans",
    option_type="All Options",
    search_term="",
    sort_by="return_30d_pct",
    ascending=False,
    limit=1000,
    start_date=None,
    end_date=None,
    scheme_code=None,
    max_expense_ratio=None
) -> pd.DataFrame:
    con = get_connection()
    where_sql, params = _build_screener_where(amc, broad_cat, sub_cat, plan_type, option_type, search_term, scheme_code=scheme_code, max_expense_ratio=max_expense_ratio)
    
    order_dir = "ASC" if ascending else "DESC"
    valid_sorts = {
        "period_return_pct": "period_return_pct",
        "return_30d_pct": "return_30d_pct",
        "return_7d_pct": "return_7d_pct",
        "return_90d_pct": "return_90d_pct",
        "return_1y_pct": "return_1y_pct",
        "change_1d_pct": "change_1d_pct",
        "latest_nav": "latest_nav",
        "scheme_name": "scheme_name",
        "dist_from_52w_high_pct": "dist_from_52w_high_pct",
        "expense_ratio": "expense_ratio",
        "exit_load_pct": "exit_load_pct"
    }
    sort_col = valid_sorts.get(sort_by, "return_30d_pct")
    limit_clause = f"LIMIT {int(limit)}" if limit else ""
    
    if start_date and end_date:
        sql = f"""
            WITH p_start AS (
                SELECT scheme_code, nav as start_nav
                FROM (
                    SELECT scheme_code, nav,
                           ROW_NUMBER() OVER (PARTITION BY scheme_code ORDER BY nav_date ASC) as rn
                    FROM nav_history
                    WHERE nav_date >= ? AND nav_date <= ?
                ) WHERE rn = 1
            ),
            p_end AS (
                SELECT scheme_code, nav as end_nav
                FROM (
                    SELECT scheme_code, nav,
                           ROW_NUMBER() OVER (PARTITION BY scheme_code ORDER BY nav_date DESC) as rn
                    FROM nav_history
                    WHERE nav_date >= ? AND nav_date <= ?
                ) WHERE rn = 1
            )
            SELECT 
                s.scheme_code,
                s.scheme_name,
                s.fund_house,
                s.category,
                s.broad_category,
                s.plan_type,
                s.option_type,
                s.expense_ratio,
                s.ter_status,
                s.ter_source,
                s.ter_as_of_date,
                s.exit_load_pct,
                s.exit_load_days,
                s.exit_load_description,
                s.exit_rule_status,
                s.exit_rule_source,
                s.exit_rule_as_of_date,
                s.lock_in_years,
                COALESCE(pe.end_nav, s.latest_nav) as latest_nav,
                s.latest_date,
                s.change_1d_pct,
                s.return_7d_pct,
                s.return_30d_pct,
                s.return_90d_pct,
                s.return_1y_pct,
                ROUND(((COALESCE(pe.end_nav, s.latest_nav) - ps.start_nav) / NULLIF(ps.start_nav, 0) * 100.0), 4) as period_return_pct,
                s.high_52w,
                s.low_52w,
                s.dist_from_52w_high_pct,
                s.isin
            FROM summary_table s
            LEFT JOIN p_start ps ON s.scheme_code = ps.scheme_code
            LEFT JOIN p_end pe ON s.scheme_code = pe.scheme_code
            {where_sql}
            ORDER BY {sort_col} {order_dir} NULLS LAST
            {limit_clause};
        """
        all_params = [start_date, end_date, start_date, end_date] + params
        df = con.execute(sql, all_params).fetchdf()
    else:
        sql = f"""
            SELECT 
                scheme_code,
                scheme_name,
                fund_house,
                category,
                broad_category,
                plan_type,
                option_type,
                expense_ratio,
                ter_status,
                ter_source,
                ter_as_of_date,
                exit_load_pct,
                exit_load_days,
                exit_load_description,
                exit_rule_status,
                exit_rule_source,
                exit_rule_as_of_date,
                lock_in_years,
                latest_nav,
                latest_date,
                change_1d_pct,
                return_7d_pct,
                return_30d_pct,
                return_90d_pct,
                return_1y_pct,
                return_30d_pct as period_return_pct,
                high_52w,
                low_52w,
                dist_from_52w_high_pct,
                isin
            FROM summary_table s
            {where_sql}
            ORDER BY {sort_col} {order_dir} NULLS LAST
            {limit_clause};
        """
        df = con.execute(sql, params).fetchdf()
    con.close()
    return df

def format_scheme_display_name(name: str, plan: str = "", option: str = "", code: Any = "") -> str:
    name = (name or "").strip()
    plan = (plan or "").strip()
    option = (option or "").strip()
    code_str = str(code).strip() if code else ""
    
    parts = []
    if plan and plan.lower() not in name.lower() and plan.lower() != "all plans":
        parts.append(plan)
    if option and option.lower() not in name.lower() and option.lower() != "all options":
        parts.append(option)
        
    spec = " - ".join(parts)
    if spec and code_str:
        return f"{name} ({spec}) [{code_str}]"
    elif spec:
        return f"{name} ({spec})"
    elif code_str:
        return f"{name} [{code_str}]"
    return name

@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def get_schemes_for_dropdown(
    amc="All Fund Houses",
    broad_cat="All Categories",
    sub_cat="All Sub-Categories",
    plan_type="All Plans",
    option_type="All Options",
    search_term="",
    limit=None
) -> List[Dict[str, Any]]:
    """
    Returns filtered schemes as a list of dicts for selectbox dropdowns,
    including scheme_code, scheme_name, and standardized display_label with AMFI code.
    """
    con = get_connection()
    where_sql, params = _build_screener_where(amc, broad_cat, sub_cat, plan_type, option_type, search_term)
    limit_clause = f"LIMIT {int(limit)}" if limit else ""
    sql = f"""
        SELECT s.scheme_code, s.scheme_name, s.plan_type, s.option_type, s.fund_house
        FROM summary_table s
        {where_sql}
        ORDER BY s.scheme_name ASC
        {limit_clause};
    """
    rows = con.execute(sql, params).fetchall()
    con.close()
    return [
        {
            "scheme_code": r[0],
            "scheme_name": r[1],
            "plan_type": r[2],
            "option_type": r[3],
            "fund_house": r[4],
            "display_label": format_scheme_display_name(r[1], r[2], r[3], r[0])
        }
        for r in rows
    ]

@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def get_nav_history_dataframe(scheme_codes: List[int], start_date=None, end_date=None) -> pd.DataFrame:
    if not scheme_codes:
        return pd.DataFrame()
    con = get_connection()
    placeholders = ", ".join(["?"] * len(scheme_codes))
    params: List[Any] = list(scheme_codes)
    
    date_clauses = []
    if start_date:
        date_clauses.append("n.nav_date >= ?")
        params.append(start_date)
    if end_date:
        date_clauses.append("n.nav_date <= ?")
        params.append(end_date)
        
    date_sql = ("AND " + " AND ".join(date_clauses)) if date_clauses else ""
    
    sql = f"""
        SELECT 
            n.scheme_code,
            s.scheme_name as raw_scheme_name,
            s.plan_type,
            s.option_type,
            n.nav_date,
            n.nav
        FROM nav_history n
        JOIN schemes s ON n.scheme_code = s.scheme_code
        WHERE n.scheme_code IN ({placeholders})
        {date_sql}
        ORDER BY n.nav_date ASC;
    """
    df = con.execute(sql, params).fetchdf()
    con.close()
    if not df.empty:
        df["base_scheme_name"] = df["raw_scheme_name"]
        df["scheme_name"] = df.apply(
            lambda r: format_scheme_display_name(r["raw_scheme_name"], r.get("plan_type"), r.get("option_type"), r["scheme_code"]),
            axis=1
        )
    return df

@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def get_gainers_losers(
    period_col="return_30d_pct",
    top_n=5,
    broad_cat="All Categories",
    sub_cat="All Sub-Categories",
    plan_type="All Plans",
    start_date=None,
    end_date=None
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    con = get_connection()
    where_sql, params = _build_screener_where("All Fund Houses", broad_cat, sub_cat, plan_type, "All Options", "")
    
    if start_date and end_date:
        sql_gainers = f"""
            WITH p_start AS (
                SELECT scheme_code, nav as start_nav
                FROM (
                    SELECT scheme_code, nav,
                           ROW_NUMBER() OVER (PARTITION BY scheme_code ORDER BY nav_date ASC) as rn
                    FROM nav_history
                    WHERE nav_date >= ? AND nav_date <= ?
                ) WHERE rn = 1
            ),
            p_end AS (
                SELECT scheme_code, nav as end_nav
                FROM (
                    SELECT scheme_code, nav,
                           ROW_NUMBER() OVER (PARTITION BY scheme_code ORDER BY nav_date DESC) as rn
                    FROM nav_history
                    WHERE nav_date >= ? AND nav_date <= ?
                ) WHERE rn = 1
            ),
            calc AS (
                SELECT s.scheme_code, s.scheme_name, s.plan_type, s.option_type, s.fund_house, s.category,
                       ROUND(((pe.end_nav - ps.start_nav) / NULLIF(ps.start_nav, 0) * 100.0), 4) as return_pct
                FROM summary_table s
                JOIN p_start ps ON s.scheme_code = ps.scheme_code
                JOIN p_end pe ON s.scheme_code = pe.scheme_code
                {where_sql}
            )
            SELECT scheme_code, scheme_name, plan_type, option_type, fund_house, category, return_pct
            FROM calc
            WHERE return_pct IS NOT NULL
            ORDER BY return_pct DESC
            LIMIT {top_n};
        """
        sql_losers = f"""
            WITH p_start AS (
                SELECT scheme_code, nav as start_nav
                FROM (
                    SELECT scheme_code, nav,
                           ROW_NUMBER() OVER (PARTITION BY scheme_code ORDER BY nav_date ASC) as rn
                    FROM nav_history
                    WHERE nav_date >= ? AND nav_date <= ?
                ) WHERE rn = 1
            ),
            p_end AS (
                SELECT scheme_code, nav as end_nav
                FROM (
                    SELECT scheme_code, nav,
                           ROW_NUMBER() OVER (PARTITION BY scheme_code ORDER BY nav_date DESC) as rn
                    FROM nav_history
                    WHERE nav_date >= ? AND nav_date <= ?
                ) WHERE rn = 1
            ),
            calc AS (
                SELECT s.scheme_code, s.scheme_name, s.plan_type, s.option_type, s.fund_house, s.category,
                       ROUND(((pe.end_nav - ps.start_nav) / NULLIF(ps.start_nav, 0) * 100.0), 4) as return_pct
                FROM summary_table s
                JOIN p_start ps ON s.scheme_code = ps.scheme_code
                JOIN p_end pe ON s.scheme_code = pe.scheme_code
                {where_sql}
            )
            SELECT scheme_code, scheme_name, plan_type, option_type, fund_house, category, return_pct
            FROM calc
            WHERE return_pct IS NOT NULL
            ORDER BY return_pct ASC
            LIMIT {top_n};
        """
        all_p = [start_date, end_date, start_date, end_date] + params
        df_gainers = con.execute(sql_gainers, all_p).fetchdf()
        df_losers = con.execute(sql_losers, all_p).fetchdf()
    else:
        valid_cols = ["change_1d_pct", "return_7d_pct", "return_30d_pct", "return_90d_pct", "return_1y_pct"]
        if period_col not in valid_cols:
            period_col = "return_30d_pct"
        filter_cond = f"{where_sql} {'AND' if where_sql else 'WHERE'} s.{period_col} IS NOT NULL"
        
        sql_gainers = f"""
            SELECT s.scheme_code, s.scheme_name, s.plan_type, s.option_type, s.fund_house, s.category, s.{period_col} as return_pct
            FROM summary_table s
            {filter_cond}
            ORDER BY s.{period_col} DESC
            LIMIT {top_n};
        """
        df_gainers = con.execute(sql_gainers, params).fetchdf()
        
        sql_losers = f"""
            SELECT s.scheme_code, s.scheme_name, s.plan_type, s.option_type, s.fund_house, s.category, s.{period_col} as return_pct
            FROM summary_table s
            {filter_cond}
            ORDER BY s.{period_col} ASC
            LIMIT {top_n};
        """
        df_losers = con.execute(sql_losers, params).fetchdf()
        
    con.close()
    if not df_gainers.empty:
        df_gainers["scheme_name"] = df_gainers.apply(
            lambda r: format_scheme_display_name(r["scheme_name"], r.get("plan_type"), r.get("option_type"), r["scheme_code"]),
            axis=1
        )
    if not df_losers.empty:
        df_losers["scheme_name"] = df_losers.apply(
            lambda r: format_scheme_display_name(r["scheme_name"], r.get("plan_type"), r.get("option_type"), r["scheme_code"]),
            axis=1
        )
    return df_gainers, df_losers

@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def get_advanced_leaders_dataframe(
    broad_cat="All Categories",
    sub_cat="All Sub-Categories",
    plan_type="All Plans",
    option_type="All Options",
    start_date=None,
    end_date=None
) -> pd.DataFrame:
    """
    Computes comprehensive relative alpha, peer category medians, quartiles,
    annualized volatility, win rate, and drawdown metrics across all matching funds
    over the active date window for institutional leadership analysis.
    """
    con = get_connection()
    where_sql, params = _build_screener_where("All Fund Houses", broad_cat, sub_cat, plan_type, option_type, "")
    
    if start_date and end_date:
        sql = f"""
            WITH p_start AS (
                SELECT scheme_code, nav as start_nav
                FROM (
                    SELECT scheme_code, nav,
                           ROW_NUMBER() OVER (PARTITION BY scheme_code ORDER BY nav_date ASC) as rn
                    FROM nav_history
                    WHERE nav_date >= ? AND nav_date <= ?
                ) WHERE rn = 1
            ),
            p_end AS (
                SELECT scheme_code, nav as end_nav
                FROM (
                    SELECT scheme_code, nav,
                           ROW_NUMBER() OVER (PARTITION BY scheme_code ORDER BY nav_date DESC) as rn
                    FROM nav_history
                    WHERE nav_date >= ? AND nav_date <= ?
                ) WHERE rn = 1
            ),
            daily_returns AS (
                SELECT
                    scheme_code,
                    nav_date,
                    (nav - LAG(nav, 1) OVER (PARTITION BY scheme_code ORDER BY nav_date ASC)) / NULLIF(LAG(nav, 1) OVER (PARTITION BY scheme_code ORDER BY nav_date ASC), 0) as daily_ret
                FROM nav_history
                WHERE nav_date >= ? AND nav_date <= ?
            ),
            vol_calc AS (
                SELECT
                    scheme_code,
                    ROUND(STDDEV(daily_ret) * SQRT(252.0) * 100.0, 4) as annualized_vol_pct,
                    COUNT(CASE WHEN daily_ret > 0 THEN 1 END) as up_days,
                    COUNT(daily_ret) as trading_days
                FROM daily_returns
                WHERE daily_ret IS NOT NULL
                GROUP BY scheme_code
            ),
            scheme_perf AS (
                SELECT
                    s.scheme_code,
                    s.scheme_name,
                    s.fund_house,
                    s.category,
                    s.broad_category,
                    s.plan_type,
                    s.option_type,
                    s.expense_ratio,
                    s.exit_load_pct,
                    s.exit_load_days,
                    s.exit_load_description,
                    s.lock_in_years,
                    s.latest_nav,
                    s.dist_from_52w_high_pct,
                    s.high_52w,
                    s.low_52w,
                    ROUND(((pe.end_nav - ps.start_nav) / NULLIF(ps.start_nav, 0) * 100.0), 4) as period_return_pct,
                    v.annualized_vol_pct as annualized_vol_pct,
                    COALESCE(v.trading_days, 0) as n_trading_days,
                    ROUND((COALESCE(v.up_days, 0) * 100.0 / NULLIF(v.trading_days, 0)), 2) as win_rate_pct
                FROM summary_table s
                JOIN p_start ps ON s.scheme_code = ps.scheme_code
                JOIN p_end pe ON s.scheme_code = pe.scheme_code
                LEFT JOIN vol_calc v ON s.scheme_code = v.scheme_code
                {where_sql}
            )
            SELECT *
            FROM scheme_perf
            WHERE period_return_pct IS NOT NULL;
        """
        all_p = [start_date, end_date, start_date, end_date, start_date, end_date] + params
        df = con.execute(sql, all_p).fetchdf()

        # True peer-category median: scoped only by broad/sub-category (which is what "category"
        # means) and NOT by the Plan/Option filters above, so toggling e.g. "Direct only" can't
        # silently change the peer baseline that alpha is measured against.
        where_sql_cat, params_cat = _build_screener_where("All Fund Houses", broad_cat, sub_cat, "All Plans", "All Options", "")
        cat_median_sql = f"""
            WITH p_start AS (
                SELECT scheme_code, nav as start_nav
                FROM (
                    SELECT scheme_code, nav,
                           ROW_NUMBER() OVER (PARTITION BY scheme_code ORDER BY nav_date ASC) as rn
                    FROM nav_history
                    WHERE nav_date >= ? AND nav_date <= ?
                ) WHERE rn = 1
            ),
            p_end AS (
                SELECT scheme_code, nav as end_nav
                FROM (
                    SELECT scheme_code, nav,
                           ROW_NUMBER() OVER (PARTITION BY scheme_code ORDER BY nav_date DESC) as rn
                    FROM nav_history
                    WHERE nav_date >= ? AND nav_date <= ?
                ) WHERE rn = 1
            )
            SELECT s.category,
                   MEDIAN(ROUND(((pe.end_nav - ps.start_nav) / NULLIF(ps.start_nav, 0) * 100.0), 4)) as true_cat_median
            FROM summary_table s
            JOIN p_start ps ON s.scheme_code = ps.scheme_code
            JOIN p_end pe ON s.scheme_code = pe.scheme_code
            {where_sql_cat}
            GROUP BY s.category;
        """
        cat_median_params = [start_date, end_date, start_date, end_date] + params_cat
        df_cat_median = con.execute(cat_median_sql, cat_median_params).fetchdf()
        df_cat_median = pd.DataFrame(columns=["category", "true_cat_median"])
    else:
        filter_cond = f"{where_sql} {'AND' if where_sql else 'WHERE'} s.return_30d_pct IS NOT NULL"
        sql = f"""
            SELECT
                s.scheme_code,
                s.scheme_name,
                s.fund_house,
                s.category,
                s.broad_category,
                s.plan_type,
                s.option_type,
                s.expense_ratio,
                s.exit_load_pct,
                s.exit_load_days,
                s.exit_load_description,
                s.lock_in_years,
                s.latest_nav,
                s.dist_from_52w_high_pct,
                s.high_52w,
                s.low_52w,
                s.return_30d_pct as period_return_pct,
                CAST(NULL AS DOUBLE) as annualized_vol_pct,
                0 as n_trading_days,
                50.0 as win_rate_pct
            FROM summary_table s
            {filter_cond};
        """
        df = con.execute(sql, params).fetchdf()
        df_cat_median = pd.DataFrame(columns=["category", "true_cat_median"])

    con.close()
    if not df.empty:
        # Standardized display label
        df["display_name"] = df.apply(
            lambda r: format_scheme_display_name(r["scheme_name"], r.get("plan_type"), r.get("option_type"), r["scheme_code"]),
            axis=1
        )
        # Peer Category Median Return: the true (Plan/Option-independent) median where available,
        # falling back to the in-view median for the no-date-range branch or any category it missed.
        if not df_cat_median.empty:
            df = df.merge(df_cat_median, on="category", how="left")
            df["cat_median_return"] = df["true_cat_median"].round(4)
            in_view_median = df.groupby("category")["period_return_pct"].transform("median").round(4)
            df["cat_median_return"] = df["cat_median_return"].fillna(in_view_median)
            df = df.drop(columns=["true_cat_median"])
        else:
            df["cat_median_return"] = df.groupby("category")["period_return_pct"].transform("median").round(4)

        # Category Alpha (Peer Outperformance)
        df["cat_alpha_pct"] = (df["period_return_pct"] - df["cat_median_return"]).round(4)
        
        # Quartile ranking within category (1 = Q1 top 25%, 4 = Q4 bottom 25%)
        def _calc_q(s):
            n = len(s)
            if n < 4:
                return s.rank(ascending=False, method="min").astype(int).clip(upper=4)
            try:
                ranks = s.rank(ascending=False, method="first")
                return pd.qcut(ranks, q=4, labels=[1, 2, 3, 4]).astype(int)
            except Exception:
                return pd.Series(1, index=s.index)

        df["quartile"] = df.groupby("category")["period_return_pct"].transform(_calc_q)
        
        # Return to Risk Ratio
        df["return_to_risk"] = np.where(
            df["annualized_vol_pct"] > 0.01,
            (df["period_return_pct"] / df["annualized_vol_pct"]).round(4),
            0.0
        )
    return df

@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def get_scheme_profile(scheme_code: int) -> Tuple[Optional[Dict[str, Any]], pd.DataFrame]:
    con = get_connection()
    summary = con.execute("SELECT * FROM summary_table WHERE scheme_code = ?", [scheme_code]).fetchdf()
    if summary.empty:
        summary = con.execute("SELECT * FROM schemes WHERE scheme_code = ?", [scheme_code]).fetchdf()
    history = con.execute("SELECT nav_date, nav FROM nav_history WHERE scheme_code = ? ORDER BY nav_date DESC", [scheme_code]).fetchdf()
    con.close()
    
    profile = summary.to_dict(orient="records")[0] if not summary.empty else None
    return profile, history


def import_official_cost_records(records: pd.DataFrame) -> Dict[str, Any]:
    """Serialized via WRITE_LOCK — see refresh_summary_table()."""
    with WRITE_LOCK:
        return _import_official_cost_records_impl(records)

def _import_official_cost_records_impl(records: pd.DataFrame) -> Dict[str, Any]:
    """Import dated, scheme-code-keyed TER and exit-load records from official sources.

    Required columns are ``scheme_code`` and, for each populated cost type, its
    value, ``*_as_of_date``, and ``*_source_url``. Exit rules use the JSON
    schema accepted by ``costs_data.parse_exit_rule``. Rows that cannot prove
    their source and effective date are rejected rather than downgraded to a
    guessed value.
    """
    if "scheme_code" not in records.columns:
        return {"imported": 0, "rejected": len(records), "errors": ["Missing required column: scheme_code."]}

    accepted = []
    errors = []
    for row_number, (_, row) in enumerate(records.iterrows(), start=2):
        try:
            scheme_code = int(row["scheme_code"])
        except (TypeError, ValueError):
            errors.append(f"Row {row_number}: invalid scheme_code.")
            continue

        ter_value = row.get("expense_ratio")
        ter_present = ter_value is not None and not pd.isna(ter_value) and str(ter_value).strip() != ""
        exit_json = row.get("exit_rule_json")
        exit_present = exit_json is not None and not pd.isna(exit_json) and str(exit_json).strip() != ""
        ter_status = None
        exit_status = None

        if ter_present:
            try:
                ter_value = float(ter_value)
                ter_date = str(row.get("ter_as_of_date") or "").strip()
                ter_url = str(row.get("ter_source_url") or "").strip()
                if ter_value < 0 or not ter_date or not ter_url.startswith(("https://", "http://")):
                    raise ValueError
                pd.Timestamp(ter_date)
                ter_status = costs_data.STATUS_OFFICIAL
            except (TypeError, ValueError):
                errors.append(f"Row {row_number}: TER requires a non-negative value, as-of date, and source URL.")
                continue
        else:
            ter_value = None
            ter_date = None
            ter_url = None

        if exit_present:
            exit_date = str(row.get("exit_rule_as_of_date") or "").strip()
            exit_url = str(row.get("exit_rule_source_url") or "").strip()
            if not costs_data.parse_exit_rule(str(exit_json)) or not exit_date or not exit_url.startswith(("https://", "http://")):
                errors.append(f"Row {row_number}: exit rule requires valid JSON, as-of date, and source URL.")
                continue
            try:
                pd.Timestamp(exit_date)
            except ValueError:
                errors.append(f"Row {row_number}: invalid exit-rule as-of date.")
                continue
            exit_status = costs_data.STATUS_OFFICIAL
        else:
            exit_json = None
            exit_date = None
            exit_url = None

        if not ter_status and not exit_status:
            errors.append(f"Row {row_number}: no dated official TER or exit rule supplied.")
            continue

        accepted.append({
            "scheme_code": scheme_code,
            "expense_ratio": ter_value,
            "ter_status": ter_status,
            "ter_source": str(row.get("ter_source") or "Official import") if ter_status else None,
            "ter_source_url": ter_url,
            "ter_as_of_date": ter_date,
            "exit_rule_json": str(exit_json) if exit_status else None,
            "exit_rule_status": exit_status,
            "exit_rule_source": str(row.get("exit_rule_source") or "Official SID/KIM import") if exit_status else None,
            "exit_rule_source_url": exit_url,
            "exit_rule_as_of_date": exit_date,
            "exit_load_description": str(row.get("exit_load_description") or "Source-supplied tiered rule") if exit_status else None,
            "lock_in_years": row.get("lock_in_years") if exit_status else None,
        })

    if not accepted:
        return {"imported": 0, "rejected": len(records), "errors": errors}

    con = get_connection()
    try:
        staging = pd.DataFrame(accepted)
        con.register("stg_official_costs", staging)
        updated = con.execute("""
            UPDATE schemes
            SET expense_ratio = CASE WHEN c.ter_status = 'official' THEN c.expense_ratio ELSE schemes.expense_ratio END,
                ter_status = CASE WHEN c.ter_status = 'official' THEN c.ter_status ELSE schemes.ter_status END,
                ter_source = CASE WHEN c.ter_status = 'official' THEN c.ter_source ELSE schemes.ter_source END,
                ter_source_url = CASE WHEN c.ter_status = 'official' THEN c.ter_source_url ELSE schemes.ter_source_url END,
                ter_as_of_date = CASE WHEN c.ter_status = 'official' THEN c.ter_as_of_date::DATE ELSE schemes.ter_as_of_date END,
                exit_rule_json = CASE WHEN c.exit_rule_status = 'official' THEN c.exit_rule_json ELSE schemes.exit_rule_json END,
                exit_rule_status = CASE WHEN c.exit_rule_status = 'official' THEN c.exit_rule_status ELSE schemes.exit_rule_status END,
                exit_rule_source = CASE WHEN c.exit_rule_status = 'official' THEN c.exit_rule_source ELSE schemes.exit_rule_source END,
                exit_rule_source_url = CASE WHEN c.exit_rule_status = 'official' THEN c.exit_rule_source_url ELSE schemes.exit_rule_source_url END,
                exit_rule_as_of_date = CASE WHEN c.exit_rule_status = 'official' THEN c.exit_rule_as_of_date::DATE ELSE schemes.exit_rule_as_of_date END,
                exit_load_description = CASE WHEN c.exit_rule_status = 'official' THEN c.exit_load_description ELSE schemes.exit_load_description END,
                lock_in_years = CASE WHEN c.exit_rule_status = 'official' THEN c.lock_in_years ELSE schemes.lock_in_years END
            FROM stg_official_costs c
            WHERE schemes.scheme_code = c.scheme_code
            RETURNING schemes.scheme_code;
        """).fetchall()
        con.unregister("stg_official_costs")
    finally:
        con.close()
    refresh_summary_table()
    return {"imported": len(updated), "rejected": len(records) - len(accepted) + (len(accepted) - len(updated)), "errors": errors}

_STATS_CACHE = None
_STATS_CACHE_TIME = 0.0
_STATS_CACHE_TTL = 120.0  # 2 minutes TTL

def invalidate_database_stats_cache():
    """Flushes cached database telemetry stats."""
    global _STATS_CACHE, _STATS_CACHE_TIME
    _STATS_CACHE = None
    _STATS_CACHE_TIME = 0.0

def get_database_stats(force_refresh: bool = False) -> Dict[str, Any]:
    global _STATS_CACHE, _STATS_CACHE_TIME
    import time
    now = time.time()
    if not force_refresh and _STATS_CACHE is not None and (now - _STATS_CACHE_TIME) < _STATS_CACHE_TTL:
        return dict(_STATS_CACHE)

    con = get_connection()
    schemes_count = con.execute("SELECT count(*) FROM schemes;").fetchone()[0]
    nav_count = con.execute("SELECT count(*) FROM nav_history;").fetchone()[0]
    amc_count = con.execute("SELECT count(DISTINCT fund_house) FROM schemes WHERE fund_house IS NOT NULL;").fetchone()[0]
    cat_count = con.execute("SELECT count(DISTINCT category) FROM schemes WHERE category IS NOT NULL;").fetchone()[0]
    min_date = con.execute("SELECT min(nav_date) FROM nav_history;").fetchone()[0]
    max_date = con.execute("SELECT max(nav_date) FROM nav_history;").fetchone()[0]
    con.close()
    
    file_size_mb = 0.0
    if os.path.exists(DB_PATH):
        file_size_mb = round(os.path.getsize(DB_PATH) / (1024 * 1024), 2)
        
    res = {
        "schemes_count": schemes_count,
        "nav_count": nav_count,
        "amc_count": amc_count,
        "category_count": cat_count,
        "min_date": min_date,
        "max_date": max_date,
        "file_size_mb": file_size_mb,
        "db_path": DB_PATH
    }
    _STATS_CACHE = res
    _STATS_CACHE_TIME = now
    return dict(res)

@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def get_cost_data_coverage() -> Dict[str, Any]:
    """Breaks down how many schemes have an 'official' (dated, source-linked) vs.
    'legacy_unverified' (bundled CSV, name-matched) vs. 'unknown' TER / exit-load record.
    TER can reach 'official' two ways — a manual dated CSV import, or the automated daily
    sync against AMFI's own TER-disclosure portal (see amfi_sync.sync_official_ter) — the
    ter_auto_synced figure below counts schemes carrying the latter's source tag specifically.
    Exit-load has no automated feed at all; only the manual official import can set it."""
    con = get_connection()
    ter_rows = con.execute("""
        SELECT COALESCE(ter_status, 'unknown') as status, count(*)
        FROM schemes GROUP BY 1;
    """).fetchall()
    exit_rows = con.execute("""
        SELECT COALESCE(exit_rule_status, 'unknown') as status, count(*)
        FROM schemes GROUP BY 1;
    """).fetchall()
    ter_auto_synced = con.execute("""
        SELECT count(*) FROM schemes
        WHERE ter_status = 'official' AND ter_source LIKE 'AMFI Total Expense Ratio Disclosure%';
    """).fetchone()[0]
    total = con.execute("SELECT count(*) FROM schemes;").fetchone()[0]
    con.close()

    ter_counts = {status: cnt for status, cnt in ter_rows}
    exit_counts = {status: cnt for status, cnt in exit_rows}
    return {
        "total_schemes": total,
        "ter_official": ter_counts.get("official", 0),
        "ter_auto_synced": ter_auto_synced,
        "ter_legacy": ter_counts.get("legacy_unverified", 0),
        "ter_unknown": ter_counts.get("unknown", 0),
        "exit_official": exit_counts.get("official", 0),
        "exit_unknown": total - exit_counts.get("official", 0),
    }


def get_scheme_identity_map() -> pd.DataFrame:
    """Raw (scheme_code, scheme_name, plan_type) for every scheme — used only for
    server-side name-matching against external sources (see amfi_sync.sync_official_ter),
    never for UI display, so it deliberately reads the base table rather than the cached,
    st.cache_data-wrapped summary_table view."""
    con = get_connection()
    df = con.execute("SELECT scheme_code, scheme_name, plan_type FROM schemes;").fetchdf()
    con.close()
    return df


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def get_scheme_ter_history(scheme_code: int) -> pd.DataFrame:
    """Full dated Regulation 66 TER disclosure history for one scheme, oldest first —
    only populated once amfi_sync.sync_official_ter has matched that scheme at least once."""
    con = get_connection()
    df = con.execute(
        """
        SELECT ter_date, base_expense_ratio_pct, brokerage_cost_pct, transaction_cost_pct,
               statutory_levies_pct, total_ter_pct, source_url
        FROM ter_history
        WHERE scheme_code = ?
        ORDER BY ter_date ASC;
        """,
        [scheme_code],
    ).fetchdf()
    con.close()
    return df


def upsert_ter_history(records: pd.DataFrame) -> int:
    """Serialized via WRITE_LOCK — see refresh_summary_table()."""
    with WRITE_LOCK:
        return _upsert_ter_history_impl(records)


def _upsert_ter_history_impl(records: pd.DataFrame) -> int:
    if records.empty:
        return 0
    con = get_connection()
    try:
        con.register("stg_ter_history", records)
        con.execute("""
            DELETE FROM ter_history
            USING stg_ter_history s
            WHERE ter_history.scheme_code = s.scheme_code AND ter_history.ter_date = s.ter_date;
        """)
        con.execute("""
            INSERT INTO ter_history (
                scheme_code, ter_date, base_expense_ratio_pct, brokerage_cost_pct,
                transaction_cost_pct, statutory_levies_pct, total_ter_pct, source_url
            )
            SELECT scheme_code, ter_date, base_expense_ratio_pct, brokerage_cost_pct,
                   transaction_cost_pct, statutory_levies_pct, total_ter_pct, source_url
            FROM stg_ter_history;
        """)
        try:
            con.unregister("stg_ter_history")
        except Exception:
            pass
    finally:
        con.close()
    return len(records)


def apply_latest_official_ter(records: pd.DataFrame) -> Dict[str, Any]:
    """Serialized via WRITE_LOCK — see refresh_summary_table()."""
    with WRITE_LOCK:
        return _apply_latest_official_ter_impl(records)


def _apply_latest_official_ter_impl(records: pd.DataFrame) -> Dict[str, Any]:
    """Promotes each matched scheme's latest dated AMFI TER-portal row to the schemes
    table's cached 'current' cost fields — the automated counterpart to
    import_official_cost_records(), extended with the BER/Brokerage/Transaction/Statutory
    breakdown this source provides. Never moves ter_as_of_date backwards: if a scheme
    already carries a more recent official as-of date (from either sync path), this row
    is skipped for that scheme rather than regressing it."""
    if records.empty:
        return {"updated": 0}
    con = get_connection()
    try:
        staging = records.copy()
        staging["ter_status"] = costs_data.STATUS_OFFICIAL
        con.register("stg_latest_ter", staging)
        updated = con.execute("""
            UPDATE schemes
            SET expense_ratio = c.total_ter_pct,
                ter_base_expense_ratio = c.base_expense_ratio_pct,
                ter_brokerage_cost_pct = c.brokerage_cost_pct,
                ter_transaction_cost_pct = c.transaction_cost_pct,
                ter_statutory_levies_pct = c.statutory_levies_pct,
                ter_status = c.ter_status,
                ter_source = c.ter_source,
                ter_source_url = c.source_url,
                ter_as_of_date = c.ter_date::DATE
            FROM stg_latest_ter c
            WHERE schemes.scheme_code = c.scheme_code
              AND (schemes.ter_as_of_date IS NULL OR schemes.ter_as_of_date <= c.ter_date::DATE)
            RETURNING schemes.scheme_code;
        """).fetchall()
        try:
            con.unregister("stg_latest_ter")
        except Exception:
            pass
    finally:
        con.close()
    refresh_summary_table()
    return {"updated": len(updated)}
