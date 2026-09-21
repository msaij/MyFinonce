"""PostgreSQL query functions for the FastAPI backend.

Originally ported ~verbatim from fetcher/db.py (the Streamlit app's DuckDB-
based DB-access module), then to SQLite, then to PostgreSQL on 2026-09-12 --
see app.db.connection's module docstring for why each move happened. Same
overall logic throughout, same public-wrapper-takes-WRITE_LOCK /
private-_impl-does-the-work structure; only the SQL strings and a few
dialect-driven mechanics changed. Connection/lock/retry/data-version machinery
(get_connection, WRITE_LOCK, _execute_with_conflict_retry, bump_data_version,
get_data_version) lives in app.db.connection -- see that module. Every
Streamlit cache_data decorator (600s TTL, no spinner) became ``@cached(ttl=600)``
from app.core.cache.

Dialect notes, SQLite -> PostgreSQL (the reverse of most of what the previous
port had to work around, since Postgres has the features DuckDB did and SQLite
did not):
- Placeholders are ``%s``, not ``?``. And a LITERAL ``%`` in SQL (``ILIKE 'x%'``)
  must be doubled to ``%%`` *if* that statement is executed with parameters --
  psycopg only scans for placeholders when params are supplied. Every such query
  in this file currently runs without params; check this if you add one.
- ``MEDIAN(x)`` -> ``percentile_cont(0.5) WITHIN GROUP (ORDER BY x)``, and
  ``STDDEV(x)`` -> ``stddev_samp(x)``. Both were hand-written Python aggregates
  registered per SQLite connection; they are native here, and far faster for it
  (no Python callback per row).
- ``ROUND(x, n)`` -> ``ROUND((x)::numeric, n)::double precision``. Postgres has
  ``round(numeric, int)`` and ``round(double precision)`` but NOT
  ``round(double precision, int)``, so the two-argument form over a computed
  double fails outright; the cast back keeps the column a float rather than a
  Decimal for pandas downstream.
- A subquery in FROM must have an alias (``) sub WHERE ...``). SQLite allowed it
  without one.
- ``LIKE`` is case-SENSITIVE here; SQLite's was ASCII case-insensitive. Literal
  patterns that relied on that became ``ILIKE``.
- ``(b - a)`` -> ``(b - a)``. Subtracting two Postgres
  DATEs yields an integer day count directly.
- ``date(x, '-N days')`` -> ``(x - N)``, same reason.
- ``INSERT OR REPLACE`` -> ``INSERT ... ON CONFLICT (key) DO UPDATE``.
- ``UPDATE t SET c = (SELECT ...) WHERE EXISTS (SELECT ...)`` ->
  ``UPDATE t SET c = s.c FROM s WHERE ...``. The correlated-subquery form was a
  SQLite workaround; UPDATE...FROM evaluates the join once instead of per row.
- Type names: ``TEXT``->``TEXT``, ``DOUBLE PRECISION``->``DOUBLE PRECISION``.
- ``ALTER TABLE ... ADD COLUMN IF NOT EXISTS`` is supported, so the old
  try/except-around-a-duplicate-column-error migration is gone.
- DDL is transactional, which matters for refresh_summary_table(): DROP + CREATE
  inside one transaction means concurrent readers keep seeing the old table
  until commit, instead of briefly seeing no table at all.
"""

import datetime
import re
from typing import List, Optional, Tuple, Dict, Any

import pandas as pd
import numpy as np
import psycopg

from app import costs_data
from app.db.connection import (
    get_connection,
    WRITE_LOCK,
    _execute_with_conflict_retry,
    bump_data_version,
    get_data_version,
    DATABASE_URL,
    fetchdf as _fetchdf,
    pyval as _pyval,
    write_staging_table as _write_staging_table,
)
from app.core.cache import cached

# A fund occasionally resets its face value by a clean factor (e.g. a Liquid/Overnight
# fund rebasing from Rs 1000 to Rs 100 per unit, or an ETF splitting units to track its
# index price more directly) â€” a real, documented corporate-action-style event, not a
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

def _redacted_dsn() -> str:
    """The database DSN with any password removed, for display. get_database_stats()
    surfaces this to the browser via /api/meta/status, and the connection string now
    carries credentials where the old SQLite file path did not."""
    return re.sub(r"://([^:/@]+):[^@]*@", r"://\1:***@", DATABASE_URL)


def _is_clean_split_ratio(ratio: float) -> bool:
    if ratio is None or pd.isna(ratio) or ratio <= 0 or not np.isfinite(ratio):
        return False
    return any(abs(ratio - c) / c < _SPLIT_TOLERANCE for c in _SPLIT_CANDIDATES)

def _configure_parallel_planner(con, num_workers: int = 2) -> None:
    """Configures PostgreSQL session planner settings for high-concurrency parallel lateral joins."""
    con.execute(f"SET max_parallel_workers_per_gather = {int(num_workers)};")
    con.execute("SET parallel_setup_cost = 10;")
    con.execute("SET min_parallel_table_scan_size = 0;")
    con.execute("SET parallel_tuple_cost = 0.0001;")

def normalize_nav_splits() -> int:
    """Serialized via WRITE_LOCK â€” see refresh_summary_table()."""
    with WRITE_LOCK:
        return _normalize_nav_splits_impl()

def _normalize_nav_splits_impl() -> int:
    """Detects unit-split/face-value-reset events in nav_history and back-adjusts every
    NAV before the split date by the split's own measured ratio, so the series is
    continuous for return calculations â€” the same "split-adjusted price" convention
    every stock/ETF data provider uses. Only ever multiplies real, already-published NAVs
    by a precisely measured factor; never invents a value. Idempotent: once adjusted, the
    boundary ratio settles near 1.0 and is never re-flagged on a later run."""
    con = get_connection()
    df = _fetchdf(con.execute("SELECT scheme_code, nav_date, nav FROM nav_history ORDER BY scheme_code, nav_date"))
    # A "segregated portfolio" (a side-pocket created when a debt scheme's underlying paper
    # defaults) settles with a final lump-sum recovery distribution that can coincidentally
    # land near a clean ratio â€” that's a one-time debt recovery, not a unit-price
    # redenomination, and back-adjusting it would fabricate a fake continuous history.
    # These are also already excluded from "current" returns by the is_active staleness
    # guard in summary_table; exclude them here too so raw nav_history charts stay honest.
    # ILIKE, not LIKE: SQLite's LIKE is ASCII case-insensitive by default and this
    # query relied on that, but PostgreSQL's LIKE is case-SENSITIVE. Left as LIKE
    # after the port, this would have quietly stopped matching "Segregated
    # Portfolio ..." scheme names -- which fails in the dangerous direction, by
    # back-adjusting debt-recovery distributions as if they were unit splits and
    # fabricating continuous history. Every other LIKE in this file was audited
    # for the same reason.
    segregated_codes = set(
        _fetchdf(con.execute("SELECT scheme_code FROM schemes WHERE scheme_name ILIKE '%segregat%'"))["scheme_code"]
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
        # One explicit transaction: pooled connections are autocommit, so without
        # this the staging load and the UPDATE that consumes it would be separate
        # transactions, and a failure between them would leave the staging table
        # populated and the adjustment half-applied.
        con.begin()
        staging = changed[["scheme_code", "nav_date", "nav"]]
        # like_table/key_columns are what make this join an index seek rather than a
        # full scan of the staging table per nav_history row -- see
        # write_staging_table()'s docstring for the type-affinity reason.
        _write_staging_table(con, "stg_nav_adjust", staging,
                             like_table="nav_history",
                             key_columns=["scheme_code", "nav_date"])
        con.execute("""
            UPDATE nav_history
            SET nav = s.nav
            FROM stg_nav_adjust s
            WHERE s.scheme_code = nav_history.scheme_code
              AND s.nav_date = nav_history.nav_date;
        """)
        con.execute("DROP TABLE IF EXISTS stg_nav_adjust")
        con.commit()
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass
        raise
    finally:
        con.close()
    print(f"NAV split normalization: adjusted {len(changed):,} historical NAV rows across {logger_msg_schemes} scheme(s).")
    return len(changed)

def refresh_summary_table():
    """Recomputes and materializes the summary table for instant UI rendering.
    Serialized via WRITE_LOCK so this can never race a concurrent sync/backfill/import write."""
    with WRITE_LOCK:
        _refresh_summary_table_impl()

def _refresh_summary_table_impl():
    con = get_connection()
    # One explicit transaction around DROP+CREATE+INSERT, and here that is not
    # just about write batching -- PostgreSQL DDL is transactional, so wrapping
    # the DROP and the CREATE together means concurrent readers keep seeing the
    # OLD summary_table right up to the commit and then see the new one. Under
    # SQLite each statement committed on its own, so between the DROP and the end
    # of the rebuild every read endpoint querying summary_table saw no table at
    # all. With the UI polling several endpoints every 5s and this running after
    # each sync and each backfill chunk, that window got hit regularly.
    con.begin()
    con.execute("DROP TABLE IF EXISTS summary_table")
    # Explicit schema, not `CREATE TABLE ... AS SELECT`: CTAS derives column types
    # from the query's output expressions, so a computed column's type depends on
    # what the planner inferred that day. Declaring the schema up front and then
    # INSERT INTO ... SELECT pins the types the readers below expect, which keeps
    # DATE columns coming back as datetime.date and the float columns as floats
    # rather than occasionally numeric/Decimal.
    con.execute("""
        CREATE TABLE summary_table (
            scheme_code BIGINT,
            scheme_name TEXT,
            fund_house TEXT,
            category TEXT,
            broad_category TEXT,
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
            latest_date DATE,
            latest_nav DOUBLE PRECISION,
            -- BOOLEAN, not the INTEGER this was under SQLite: a comparison there
            -- evaluated to 0/1, so the column held an int. In PostgreSQL
            -- `a >= b` is a genuine boolean and inserting it into an integer
            -- column is a hard DatatypeMismatch, not a silent coercion. Storing
            -- the real type is better than casting to int at both ends --
            -- consumers now say `WHERE is_active` instead of `= 1`.
            is_active BOOLEAN,
            change_1d_pct DOUBLE PRECISION,
            return_7d_pct DOUBLE PRECISION,
            return_30d_pct DOUBLE PRECISION,
            return_90d_pct DOUBLE PRECISION,
            return_1y_pct DOUBLE PRECISION,
            return_3y_pct DOUBLE PRECISION,
            return_5y_pct DOUBLE PRECISION,
            return_10y_pct DOUBLE PRECISION,
            return_1y DOUBLE PRECISION,
            return_3y DOUBLE PRECISION,
            return_5y DOUBLE PRECISION,
            return_10y DOUBLE PRECISION,
            high_52w DOUBLE PRECISION,
            low_52w DOUBLE PRECISION,
            dist_from_52w_high_pct DOUBLE PRECISION
        )
    """)
    _summary_sql = """
        INSERT INTO summary_table (
            scheme_code, scheme_name, fund_house, category, broad_category, plan_type,
            option_type, isin, expense_ratio, ter_status, ter_source, ter_source_url,
            ter_as_of_date, ter_base_expense_ratio, ter_brokerage_cost_pct,
            ter_transaction_cost_pct, ter_statutory_levies_pct,
            latest_date, latest_nav, is_active, change_1d_pct, return_7d_pct,
            return_30d_pct, return_90d_pct, return_1y_pct, return_3y_pct, return_5y_pct, return_10y_pct,
            return_1y, return_3y, return_5y, return_10y,
            high_52w, low_52w, dist_from_52w_high_pct
        )
        WITH db_freshness AS (
            SELECT MAX(nav_date) as global_max_date FROM nav_history
        ),
        latest_nav AS (
            SELECT s.scheme_code, l.latest_date, l.latest_nav
            FROM schemes s
            CROSS JOIN LATERAL (
                SELECT nav_date as latest_date, nav as latest_nav
                FROM nav_history
                WHERE scheme_code = s.scheme_code
                ORDER BY nav_date DESC
                LIMIT 1
            ) l
        ),
        nav_1d AS (
            SELECT l.scheme_code, n1.nav_1d_ago
            FROM latest_nav l, db_freshness f
            CROSS JOIN LATERAL (
                SELECT nav as nav_1d_ago
                FROM nav_history
                WHERE scheme_code = l.scheme_code AND nav_date < l.latest_date
                ORDER BY nav_date DESC
                LIMIT 1
            ) n1
            WHERE l.latest_date >= (f.global_max_date - 30)
        ),
        nav_7d AS (
            SELECT l.scheme_code, n7.nav as nav_7d_ago
            FROM latest_nav l, db_freshness f
            CROSS JOIN LATERAL (
                SELECT nav
                FROM nav_history
                WHERE scheme_code = l.scheme_code
                  AND nav_date <= (l.latest_date - 5)
                  AND nav_date >= (l.latest_date - 14)
                ORDER BY ABS(nav_date - (l.latest_date - 7))
                LIMIT 1
            ) n7
            WHERE l.latest_date >= (f.global_max_date - 30)
        ),
        nav_30d AS (
            SELECT l.scheme_code, n30.nav as nav_30d_ago
            FROM latest_nav l, db_freshness f
            CROSS JOIN LATERAL (
                SELECT nav
                FROM nav_history
                WHERE scheme_code = l.scheme_code
                  AND nav_date <= (l.latest_date - 25)
                  AND nav_date >= (l.latest_date - 45)
                ORDER BY ABS(nav_date - (l.latest_date - 30))
                LIMIT 1
            ) n30
            WHERE l.latest_date >= (f.global_max_date - 30)
        ),
        nav_90d AS (
            SELECT l.scheme_code, n90.nav as nav_90d_ago
            FROM latest_nav l, db_freshness f
            CROSS JOIN LATERAL (
                SELECT nav
                FROM nav_history
                WHERE scheme_code = l.scheme_code
                  AND nav_date <= (l.latest_date - 75)
                  AND nav_date >= (l.latest_date - 120)
                ORDER BY ABS(nav_date - (l.latest_date - 90))
                LIMIT 1
            ) n90
            WHERE l.latest_date >= (f.global_max_date - 30)
        ),
        nav_1y AS (
            SELECT l.scheme_code, n1y.nav as nav_1y_ago
            FROM latest_nav l, db_freshness f
            CROSS JOIN LATERAL (
                SELECT nav
                FROM nav_history
                WHERE scheme_code = l.scheme_code
                  AND nav_date <= (l.latest_date - 330)
                  AND nav_date >= (l.latest_date - 420)
                ORDER BY ABS(nav_date - (l.latest_date - 365))
                LIMIT 1
            ) n1y
            WHERE l.latest_date >= (f.global_max_date - 30)
        ),
        nav_3y AS (
            SELECT l.scheme_code, n3y.nav as nav_3y_ago
            FROM latest_nav l, db_freshness f
            CROSS JOIN LATERAL (
                SELECT nav
                FROM nav_history
                WHERE scheme_code = l.scheme_code
                  AND nav_date <= (l.latest_date - 1000)
                  AND nav_date >= (l.latest_date - 1200)
                ORDER BY ABS(nav_date - (l.latest_date - 1095))
                LIMIT 1
            ) n3y
            WHERE l.latest_date >= (f.global_max_date - 30)
        ),
        nav_5y AS (
            SELECT l.scheme_code, n5y.nav as nav_5y_ago
            FROM latest_nav l, db_freshness f
            CROSS JOIN LATERAL (
                SELECT nav
                FROM nav_history
                WHERE scheme_code = l.scheme_code
                  AND nav_date <= (l.latest_date - 1700)
                  AND nav_date >= (l.latest_date - 1950)
                ORDER BY ABS(nav_date - (l.latest_date - 1826))
                LIMIT 1
            ) n5y
            WHERE l.latest_date >= (f.global_max_date - 30)
        ),
        nav_10y AS (
            SELECT l.scheme_code, n10y.nav as nav_10y_ago
            FROM latest_nav l, db_freshness f
            CROSS JOIN LATERAL (
                SELECT nav
                FROM nav_history
                WHERE scheme_code = l.scheme_code
                  AND nav_date <= (l.latest_date - 3500)
                  AND nav_date >= (l.latest_date - 3800)
                ORDER BY ABS(nav_date - (l.latest_date - 3652))
                LIMIT 1
            ) n10y
            WHERE l.latest_date >= (f.global_max_date - 30)
        ),
        stats_52w AS (
            SELECT
                nh.scheme_code,
                MAX(nh.nav) as high_52w,
                MIN(nh.nav) as low_52w
            FROM nav_history nh, db_freshness f
            WHERE nh.nav_date >= (f.global_max_date - 365)
            GROUP BY nh.scheme_code
        )
        SELECT
            s.scheme_code,
            s.scheme_name,
            s.fund_house,
            s.category,
            CASE
                WHEN s.category ILIKE 'Equity Scheme%' THEN 'Equity'
                WHEN s.category ILIKE 'Debt Scheme%' THEN 'Debt'
                -- Pre-2018-recategorization AMFI debt-fund naming ("Income/Debt Oriented
                -- Schemes - ..." and the bare legacy label "Income") — ~2,000 currently
                -- active schemes carry these labels but were falling into the 'Other /
                -- Index / ETF' catch-all bucket instead of 'Debt', silently excluding a
                -- large share of real debt funds from every Asset Class breakdown/filter.
                WHEN s.category ILIKE 'Income/Debt Oriented Schemes%' THEN 'Debt'
                WHEN s.category = 'Income' THEN 'Debt'
                WHEN s.category ILIKE 'Hybrid Scheme%' THEN 'Hybrid'
                WHEN s.category ILIKE 'Solution Oriented%' THEN 'Solution Oriented'
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
            l.latest_date,
            l.latest_nav,
            (l.latest_date >= (f.global_max_date - 30)) as is_active,
            CASE WHEN l.latest_date >= (f.global_max_date - 30)
                 THEN ROUND((((l.latest_nav - n1.nav_1d_ago) / NULLIF(n1.nav_1d_ago, 0) * 100.0))::numeric, 4)::double precision END as change_1d_pct,
            CASE WHEN l.latest_date >= (f.global_max_date - 30)
                 THEN ROUND((((l.latest_nav - n7.nav_7d_ago) / NULLIF(n7.nav_7d_ago, 0) * 100.0))::numeric, 4)::double precision END as return_7d_pct,
            CASE WHEN l.latest_date >= (f.global_max_date - 30)
                 THEN ROUND((((l.latest_nav - n30.nav_30d_ago) / NULLIF(n30.nav_30d_ago, 0) * 100.0))::numeric, 4)::double precision END as return_30d_pct,
            CASE WHEN l.latest_date >= (f.global_max_date - 30)
                 THEN ROUND((((l.latest_nav - n90.nav_90d_ago) / NULLIF(n90.nav_90d_ago, 0) * 100.0))::numeric, 4)::double precision END as return_90d_pct,
            CASE WHEN l.latest_date >= (f.global_max_date - 30)
                 THEN ROUND((((l.latest_nav - n1y.nav_1y_ago) / NULLIF(n1y.nav_1y_ago, 0) * 100.0))::numeric, 4)::double precision END as return_1y_pct,
            CASE WHEN l.latest_date >= (f.global_max_date - 30)
                 THEN ROUND((((l.latest_nav - n3y.nav_3y_ago) / NULLIF(n3y.nav_3y_ago, 0) * 100.0))::numeric, 4)::double precision END as return_3y_pct,
            CASE WHEN l.latest_date >= (f.global_max_date - 30)
                 THEN ROUND((((l.latest_nav - n5y.nav_5y_ago) / NULLIF(n5y.nav_5y_ago, 0) * 100.0))::numeric, 4)::double precision END as return_5y_pct,
            CASE WHEN l.latest_date >= (f.global_max_date - 30)
                 THEN ROUND((((l.latest_nav - n10y.nav_10y_ago) / NULLIF(n10y.nav_10y_ago, 0) * 100.0))::numeric, 4)::double precision END as return_10y_pct,
            CASE WHEN l.latest_date >= (f.global_max_date - 30)
                 THEN ROUND((((l.latest_nav - n1y.nav_1y_ago) / NULLIF(n1y.nav_1y_ago, 0) * 100.0))::numeric, 4)::double precision END as return_1y,
            CASE WHEN l.latest_date >= (f.global_max_date - 30)
                 THEN ROUND((((l.latest_nav - n3y.nav_3y_ago) / NULLIF(n3y.nav_3y_ago, 0) * 100.0))::numeric, 4)::double precision END as return_3y,
            CASE WHEN l.latest_date >= (f.global_max_date - 30)
                 THEN ROUND((((l.latest_nav - n5y.nav_5y_ago) / NULLIF(n5y.nav_5y_ago, 0) * 100.0))::numeric, 4)::double precision END as return_5y,
            CASE WHEN l.latest_date >= (f.global_max_date - 30)
                 THEN ROUND((((l.latest_nav - n10y.nav_10y_ago) / NULLIF(n10y.nav_10y_ago, 0) * 100.0))::numeric, 4)::double precision END as return_10y,
            st.high_52w,
            st.low_52w,
            ROUND((((l.latest_nav - st.high_52w) / NULLIF(st.high_52w, 0) * 100.0))::numeric, 4)::double precision as dist_from_52w_high_pct
        FROM schemes s
        JOIN latest_nav l ON s.scheme_code = l.scheme_code
        CROSS JOIN db_freshness f
        LEFT JOIN nav_1d n1 ON s.scheme_code = n1.scheme_code
        LEFT JOIN nav_7d n7 ON s.scheme_code = n7.scheme_code
        LEFT JOIN nav_30d n30 ON s.scheme_code = n30.scheme_code
        LEFT JOIN nav_90d n90 ON s.scheme_code = n90.scheme_code
        LEFT JOIN nav_1y n1y ON s.scheme_code = n1y.scheme_code
        LEFT JOIN nav_3y n3y ON s.scheme_code = n3y.scheme_code
        LEFT JOIN nav_5y n5y ON s.scheme_code = n5y.scheme_code
        LEFT JOIN nav_10y n10y ON s.scheme_code = n10y.scheme_code
        LEFT JOIN stats_52w st ON s.scheme_code = st.scheme_code;
    """
    try:
        _execute_with_conflict_retry(con, _summary_sql)
        con.execute("CREATE INDEX IF NOT EXISTS idx_summary_scheme_code ON summary_table(scheme_code);")
        con.execute("CREATE INDEX IF NOT EXISTS idx_summary_broad_cat ON summary_table(broad_category);")
        con.execute("CREATE INDEX IF NOT EXISTS idx_summary_cat ON summary_table(category);")
        con.execute("CREATE INDEX IF NOT EXISTS idx_summary_active ON summary_table(is_active);")
        con.execute("CREATE INDEX IF NOT EXISTS idx_summary_latest_date ON summary_table(latest_date);")
        con.execute("CREATE INDEX IF NOT EXISTS idx_summary_cat_active ON summary_table(category, is_active);")
        con.execute("CREATE INDEX IF NOT EXISTS idx_summary_active_cat ON summary_table(is_active, category);")
        con.execute("CREATE INDEX IF NOT EXISTS idx_summary_perf_cov ON summary_table(category, is_active) INCLUDE (scheme_code, scheme_name, return_1y_pct, return_3y_pct, return_5y_pct, return_10y_pct);")
        con.execute("ALTER TABLE summary_table SET (parallel_workers = 4);")
        try:
            con.execute("CREATE EXTENSION IF NOT EXISTS pg_prewarm;")
            con.execute("SELECT pg_prewarm('summary_table');")
        except Exception:
            pass
        con.commit()
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass
        raise
    finally:
        # Always close, even on an exhausted-retry failure â€” an unclosed cursor can leave a
        # transaction open on the shared connection and block every other reader/writer
        # behind it indefinitely. (The ROLLBACK above already ends the transaction on
        # failure; this close() is the same belt-and-suspenders guard as before.)
        con.close()
    invalidate_database_stats_cache()
    bump_data_version()

def init_db():
    """Run durable-table migrations, then refresh the summary cache if missing."""
    from app.db.migrate import run_migrations
    run_migrations()
    con = get_connection()

    try:
        con.execute("""
            CREATE INDEX IF NOT EXISTS idx_summary_scheme_code ON summary_table(scheme_code);
            CREATE INDEX IF NOT EXISTS idx_summary_broad_cat ON summary_table(broad_category);
            CREATE INDEX IF NOT EXISTS idx_summary_cat ON summary_table(category);
            CREATE INDEX IF NOT EXISTS idx_summary_active ON summary_table(is_active);
            CREATE INDEX IF NOT EXISTS idx_summary_latest_date ON summary_table(latest_date);
            CREATE INDEX IF NOT EXISTS idx_summary_cat_active ON summary_table(category, is_active);
            CREATE INDEX IF NOT EXISTS idx_summary_active_cat ON summary_table(is_active, category);
            CREATE INDEX IF NOT EXISTS idx_summary_perf_cov ON summary_table(category, is_active) INCLUDE (scheme_code, scheme_name, return_1y_pct, return_3y_pct, return_5y_pct, return_10y_pct);
            ALTER TABLE summary_table SET (parallel_workers = 4);
        """)
        con.execute("CREATE EXTENSION IF NOT EXISTS pg_prewarm;")
        con.execute("SELECT pg_prewarm('idx_nav_history_cov');")
        con.execute("SELECT pg_prewarm('summary_table');")
    except Exception:
        pass

    # Migrate legacy inferred values to explicit source-aware states.
    try:
        con.execute("CREATE TABLE IF NOT EXISTS sync_meta (key TEXT PRIMARY KEY, value TEXT);")
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
                    })
                df_stg_costs = pd.DataFrame(cost_records)
                # One explicit transaction, with a nested try/except (rather than
                # letting the outer one below catch it) so a failure rolls back
                # cleanly instead of leaving this connection sitting on an open
                # transaction that the rest of this function keeps reusing.
                con.begin()
                try:
                    _write_staging_table(con, "stg_costs", df_stg_costs,
                                         like_table="schemes", key_columns=["scheme_code"])
                    # UPDATE...FROM, not the five correlated subqueries this used to
                    # run: that form was a SQLite-portability workaround, and it
                    # re-evaluated a subquery per column per row. The join here is
                    # planned once.
                    con.execute("""
                        UPDATE schemes
                        SET expense_ratio = c.expense_ratio,
                            ter_status = c.ter_status,
                            ter_source = c.ter_source,
                            ter_source_url = c.ter_source_url,
                            ter_as_of_date = c.ter_as_of_date
                        FROM stg_costs c
                        WHERE c.scheme_code = schemes.scheme_code
                          AND schemes.ter_status IS DISTINCT FROM 'official';
                    """)
                    con.execute("DROP TABLE IF EXISTS stg_costs")
                    con.execute(
                        """INSERT INTO sync_meta (key, value) VALUES ('cost_data_version', %s)
                           ON CONFLICT (key) DO UPDATE SET value = excluded.value;""",
                        (costs_data.COST_DATA_VERSION,),
                    )
                    con.commit()
                except Exception:
                    con.rollback()
                    raise
                con.close()
                refresh_summary_table()
                con = get_connection()
    except Exception as e:
        print(f"Cost initialization notice: {e}")

    cache_missing = False
    try:
        con.execute("SELECT broad_category, expense_ratio, ter_status FROM summary_table LIMIT 1;")
    except Exception:
        cache_missing = True
    con.close()
    if cache_missing:
        refresh_summary_table()

@cached(ttl=600)
def get_amcs() -> List[str]:
    con = get_connection()
    res = con.execute("SELECT DISTINCT fund_house FROM schemes WHERE fund_house IS NOT NULL AND fund_house != '' ORDER BY fund_house;").fetchall()
    con.close()
    return [r[0] for r in res]

@cached(ttl=600)
def get_broad_categories() -> List[str]:
    con = get_connection()
    res = con.execute("SELECT DISTINCT broad_category FROM summary_table WHERE broad_category IS NOT NULL ORDER BY broad_category;").fetchall()
    con.close()
    return [r[0] for r in res]

@cached(ttl=600)
def get_subcategories(broad_category: Optional[str] = None) -> List[str]:
    con = get_connection()
    if broad_category and broad_category != "All Categories":
        res = con.execute(
            "SELECT DISTINCT category FROM summary_table WHERE broad_category = %s AND category IS NOT NULL ORDER BY category;",
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

@cached(ttl=600)
def get_market_overview_stats() -> Dict[str, Any]:
    con = get_connection()
    total_schemes = con.execute("SELECT count(*) FROM schemes;").fetchone()[0]
    total_amcs = con.execute("SELECT count(DISTINCT fund_house) FROM schemes WHERE fund_house IS NOT NULL;").fetchone()[0]
    total_nav_records = con.execute("SELECT count(*) FROM nav_history;").fetchone()[0]
    date_row = con.execute("SELECT min(nav_date), max(nav_date) FROM nav_history;").fetchone()

    # Asset class distribution with multi-horizon returns
    asset_dist = _fetchdf(con.execute("""
        SELECT broad_category, count(*) as count,
               ROUND((AVG(change_1d_pct))::numeric, 4)::double precision as avg_1d,
               ROUND((AVG(return_7d_pct))::numeric, 4)::double precision as avg_7d,
               ROUND((AVG(return_30d_pct))::numeric, 4)::double precision as avg_30d,
               ROUND((AVG(return_90d_pct))::numeric, 4)::double precision as avg_90d,
               ROUND((AVG(return_1y_pct))::numeric, 4)::double precision as avg_1y,
               ROUND((percentile_cont(0.5) WITHIN GROUP (ORDER BY return_30d_pct))::numeric, 4)::double precision as med_30d,
               ROUND((percentile_cont(0.5) WITHIN GROUP (ORDER BY return_90d_pct))::numeric, 4)::double precision as med_90d
        FROM summary_table
        GROUP BY broad_category
        ORDER BY count DESC;
    """))

    # Top 15 AMCs by scheme volume and average returns
    top_amcs = _fetchdf(con.execute("""
        SELECT
            s.fund_house,
            count(*) as schemes_count,
            ROUND((AVG(st.return_30d_pct))::numeric, 4)::double precision as avg_30d,
            ROUND((AVG(st.return_90d_pct))::numeric, 4)::double precision as avg_90d,
            ROUND((AVG(st.return_1y_pct))::numeric, 4)::double precision as avg_1y
        FROM schemes s
        LEFT JOIN summary_table st ON s.scheme_code = st.scheme_code
        WHERE s.fund_house IS NOT NULL AND s.fund_house != ''
        GROUP BY s.fund_house
        ORDER BY schemes_count DESC
        LIMIT 15;
    """))

    # Best performing category (30D)
    best_cat = con.execute("""
        SELECT category, ROUND((AVG(return_30d_pct))::numeric, 4)::double precision as avg_30d
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

@cached(ttl=600)
def get_category_performance_matrix(broad_category: str = "All") -> pd.DataFrame:
    con = get_connection()
    where_sql = ""
    params = []
    if broad_category and broad_category != "All":
        where_sql = "AND broad_category = %s"
        params = [broad_category]
    df = _fetchdf(con.execute(f"""
        SELECT
            broad_category AS "Asset Class",
            category AS "Category",
            count(*) AS "Schemes",
            ROUND((AVG(expense_ratio))::numeric, 4)::double precision AS avg_ter,
            ROUND((AVG(change_1d_pct))::numeric, 4)::double precision AS avg_1d,
            ROUND((AVG(return_7d_pct))::numeric, 4)::double precision AS avg_7d,
            ROUND((AVG(return_30d_pct))::numeric, 4)::double precision AS avg_30d,
            ROUND((AVG(return_90d_pct))::numeric, 4)::double precision AS avg_90d,
            ROUND((AVG(return_1y_pct))::numeric, 4)::double precision AS avg_1y,
            ROUND((AVG(dist_from_52w_high_pct))::numeric, 4)::double precision AS dist_52w_high,
            ROUND((MAX(return_30d_pct))::numeric, 4)::double precision AS top_fund_30d,
            ROUND((MIN(return_30d_pct))::numeric, 4)::double precision AS bottom_fund_30d
        FROM summary_table
        WHERE category IS NOT NULL {where_sql}
        GROUP BY broad_category, category
        ORDER BY avg_30d DESC NULLS LAST;
    """, params if params else None))
    con.close()
    if not df.empty:
        df = df.rename(columns={
            "avg_ter": "Avg TER %",
            "avg_1d": "Avg 1D %",
            "avg_7d": "Avg 7D %",
            "avg_30d": "Avg 30D %",
            "avg_90d": "Avg 90D %",
            "avg_1y": "Avg 1Y %",
            "dist_52w_high": "52W High Gap %",
            "top_fund_30d": "Top Fund (30D) %",
            "bottom_fund_30d": "Bottom Fund (30D) %",
        })
    else:
        df = pd.DataFrame(columns=[
            "Asset Class", "Category", "Schemes", "Avg TER %", "Avg 1D %",
            "Avg 7D %", "Avg 30D %", "Avg 90D %", "Avg 1Y %", "52W High Gap %",
            "Top Fund (30D) %", "Bottom Fund (30D) %"
        ])
    return df

@cached(ttl=600)
def get_macro_asset_class_trend(start_date: datetime.date, end_date: datetime.date, plan_type: str = "All Plans", option_type: str = "All Options") -> pd.DataFrame:
    """
    Computes an indexed base-100 time series for the major asset classes (Equity, Debt, Hybrid)
    over the chosen date range for institutional macro trajectory visualization.
    """
    start_date, end_date = _normalize_date_range(start_date, end_date)
    if not start_date or not end_date:
        return pd.DataFrame(columns=["nav_date", "Asset Class", "Indexed Performance"])
    con = get_connection()
    plan_clause = "AND s.plan_type = %s" if plan_type and plan_type != "All Plans" else ""
    opt_clause = "AND s.option_type = %s" if option_type and option_type != "All Options" else ""
    extra_params = []
    if plan_clause:
        extra_params.append(plan_type)
    if opt_clause:
        extra_params.append(option_type)

    sql = f"""
        WITH matching_schemes AS (
            SELECT s.scheme_code, s.broad_category
            FROM summary_table s
            WHERE s.broad_category IN ('Equity', 'Debt', 'Hybrid')
              {plan_clause}
              {opt_clause}
        ),
        daily_navs AS (
            SELECT
                nh.nav_date,
                ms.broad_category,
                AVG(nh.nav) as mean_nav
            FROM nav_history nh
            JOIN matching_schemes ms ON nh.scheme_code = ms.scheme_code
            WHERE nh.nav_date >= %s AND nh.nav_date <= %s
            GROUP BY nh.nav_date, ms.broad_category
        ),
        base_navs AS (
            SELECT broad_category, mean_nav as base_nav
            FROM (
                SELECT broad_category, mean_nav,
                       ROW_NUMBER() OVER (PARTITION BY broad_category ORDER BY nav_date ASC) as rn
                FROM daily_navs
            ) sub WHERE rn = 1
        )
        SELECT
            d.nav_date,
            d.broad_category as "Asset Class",
            ROUND(((d.mean_nav / NULLIF(b.base_nav, 0)) * 100.0)::numeric, 2)::double precision as "Indexed Performance"
        FROM daily_navs d
        JOIN base_navs b ON d.broad_category = b.broad_category
        ORDER BY d.nav_date ASC;
    """
    params = extra_params + [start_date, end_date]
    try:
        df = _fetchdf(con.execute(sql, params))
    except Exception:
        df = pd.DataFrame(columns=["nav_date", "Asset Class", "Indexed Performance"])
    finally:
        con.close()
    return df

def _parse_date(val: Any) -> Optional[datetime.date]:
    if val is None or val == "":
        return None
    if isinstance(val, datetime.date) and not isinstance(val, datetime.datetime):
        return val
    if isinstance(val, datetime.datetime):
        return val.date()
    if hasattr(val, "date") and callable(val.date):
        return val.date()
    if isinstance(val, str):
        val = val.strip()
        if not val:
            return None
        try:
            return datetime.date.fromisoformat(val[:10])
        except (ValueError, TypeError):
            pass
        try:
            return pd.to_datetime(val).date()
        except Exception:
            return None
    try:
        return pd.to_datetime(val).date()
    except Exception:
        return None

def _normalize_date_range(start_date: Any, end_date: Any) -> Tuple[Optional[datetime.date], Optional[datetime.date]]:
    """Normalizes start_date and end_date to datetime.date objects, auto-swapping if both are present and start > end."""
    sd = _parse_date(start_date)
    ed = _parse_date(end_date)
    if sd is not None and ed is not None and sd > ed:
        sd, ed = ed, sd
    return sd, ed

@cached(ttl=600)
def get_kpis(amc="All Fund Houses", broad_cat="All Categories", sub_cat="All Sub-Categories", plan_type="All Plans", option_type="All Options", search_term="", start_date=None, end_date=None, scheme_code=None, max_expense_ratio=None, official_ter_only=False) -> Dict[str, Any]:
    con = get_connection()
    start_date, end_date = _normalize_date_range(start_date, end_date)
    where_sql, params = _build_screener_where(amc, broad_cat, sub_cat, plan_type, option_type, search_term, scheme_code=scheme_code, max_expense_ratio=max_expense_ratio, official_ter_only=official_ter_only)

    try:
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
            _configure_parallel_planner(con)
            extra_cond = "AND s.latest_date >= %s" if where_sql else "WHERE s.latest_date >= %s"
            where_sql_active = f"{where_sql} {extra_cond}"
            sql_combined = f"""
                WITH period_calc AS (
                    SELECT s.scheme_name, s.broad_category, s.category,
                           CASE
                               WHEN ps.nav IS NULL OR ps.nav <= 0 THEN NULL
                               ELSE ROUND((((COALESCE(pe.nav, s.latest_nav) - ps.nav) / NULLIF(ps.nav, 0) * 100.0))::numeric, 4)::double precision
                           END as period_return_pct
                    FROM summary_table s
                    LEFT JOIN LATERAL (
                        SELECT nav, nav_date FROM nav_history
                        WHERE scheme_code = s.scheme_code AND nav_date >= %s AND nav_date <= %s
                        ORDER BY nav_date ASC
                        LIMIT 1
                    ) ps ON TRUE
                    LEFT JOIN LATERAL (
                        SELECT nav, nav_date FROM nav_history
                        WHERE scheme_code = s.scheme_code AND nav_date >= %s AND nav_date <= %s
                        ORDER BY nav_date DESC
                        LIMIT 1
                    ) pe ON TRUE
                    {where_sql_active}
                ),
                kpi_stats AS (
                    SELECT
                        (SELECT scheme_name FROM period_calc WHERE period_return_pct IS NOT NULL ORDER BY period_return_pct DESC LIMIT 1) as top_name,
                        MAX(period_return_pct) as top_return,
                        (SELECT scheme_name FROM period_calc WHERE period_return_pct IS NOT NULL ORDER BY period_return_pct ASC LIMIT 1) as lag_name,
                        MIN(period_return_pct) as lag_return,
                        COUNT(CASE WHEN period_return_pct > 0 THEN 1 END) as advancers,
                        COUNT(CASE WHEN period_return_pct < 0 THEN 1 END) as decliners,
                        COUNT(CASE WHEN period_return_pct = 0 THEN 1 END) as unchanged,
                        ROUND((percentile_cont(0.5) WITHIN GROUP (ORDER BY period_return_pct))::numeric, 4)::double precision as median_return,
                        ROUND((AVG(period_return_pct))::numeric, 4)::double precision as avg_return
                    FROM period_calc
                    WHERE period_return_pct IS NOT NULL
                ),
                best_cat AS (
                    SELECT category, ROUND((AVG(period_return_pct))::numeric, 4)::double precision as avg_cat_ret
                    FROM period_calc
                    WHERE category IS NOT NULL AND period_return_pct IS NOT NULL
                    GROUP BY category
                    ORDER BY avg_cat_ret DESC
                    LIMIT 1
                )
                SELECT k.*, b.category as best_cat_name, b.avg_cat_ret as best_cat_return
                FROM kpi_stats k
                LEFT JOIN best_cat b ON TRUE;
            """
            all_p = [start_date, end_date, start_date, end_date] + params + [start_date]
            kpi_row = con.execute(sql_combined, all_p).fetchone()
            if kpi_row and kpi_row[0] is not None:
                top_performer = {"name": kpi_row[0], "return_pct": kpi_row[1]}
                lag_performer = {"name": kpi_row[2], "return_pct": kpi_row[3]}
                advancers = kpi_row[4] or 0
                decliners = kpi_row[5] or 0
                unchanged = kpi_row[6] or 0
                median_return = kpi_row[7] or 0.0
                avg_return = kpi_row[8] or 0.0
            if kpi_row and kpi_row[9] is not None:
                best_cat = {"name": kpi_row[9], "return_pct": kpi_row[10]}
        else:
            sql_summary_kpi = f"""
                WITH filtered AS MATERIALIZED (
                    SELECT s.scheme_name, s.return_30d_pct
                    FROM summary_table s
                    {where_sql} {'AND' if where_sql else 'WHERE'} s.return_30d_pct IS NOT NULL
                )
                SELECT
                    (SELECT scheme_name FROM filtered ORDER BY return_30d_pct DESC LIMIT 1) as top_name,
                    MAX(return_30d_pct) as top_return,
                    (SELECT scheme_name FROM filtered ORDER BY return_30d_pct ASC LIMIT 1) as lag_name,
                    MIN(return_30d_pct) as lag_return,
                    COUNT(CASE WHEN return_30d_pct > 0 THEN 1 END) as advancers,
                    COUNT(CASE WHEN return_30d_pct < 0 THEN 1 END) as decliners,
                    COUNT(CASE WHEN return_30d_pct = 0 THEN 1 END) as unchanged,
                    ROUND((percentile_cont(0.5) WITHIN GROUP (ORDER BY return_30d_pct))::numeric, 4)::double precision as median_return,
                    ROUND((AVG(return_30d_pct))::numeric, 4)::double precision as avg_return
                FROM filtered;
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
                SELECT s.category, ROUND((AVG(s.return_30d_pct))::numeric, 4)::double precision as avg_cat_ret
                FROM summary_table s
                {where_sql} {'AND' if where_sql else 'WHERE'} s.category IS NOT NULL AND s.return_30d_pct IS NOT NULL
                GROUP BY s.category
                ORDER BY avg_cat_ret DESC
                LIMIT 1;
            """
            bcat_row = con.execute(sql_best_cat, params).fetchone()
            if bcat_row:
                best_cat = {"name": bcat_row[0], "return_pct": bcat_row[1]}
    finally:
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

def _build_screener_where(amc="All Fund Houses", broad_cat="All Categories", sub_cat="All Sub-Categories", plan_type="All Plans", option_type="All Options", search_term="", min_return_30d=None, scheme_code=None, max_expense_ratio=None, official_ter_only=False) -> Tuple[str, List[Any]]:
    clauses = []
    params = []

    if scheme_code is not None:
        try:
            sc_int = int(scheme_code)
            if sc_int > 0:
                clauses.append("s.scheme_code = %s")
                params.append(sc_int)
        except (ValueError, TypeError):
            pass

    if amc and amc != "All Fund Houses":
        clauses.append("s.fund_house = %s")
        params.append(amc)

    if broad_cat and broad_cat != "All Categories":
        clauses.append("s.broad_category = %s")
        params.append(broad_cat)

    if sub_cat and sub_cat != "All Sub-Categories":
        clauses.append("s.category = %s")
        params.append(sub_cat)

    if plan_type and plan_type != "All Plans":
        clauses.append("s.plan_type = %s")
        params.append(plan_type)

    if option_type and option_type != "All Options":
        clauses.append("s.option_type = %s")
        params.append(option_type)

    if search_term and search_term.strip():
        # Advanced multi-token all-words matching: extracts alphanumeric tokens so words match in any order and punctuation is safely ignored
        tokens = [t for t in re.findall(r'[a-zA-Z0-9]+', search_term.lower()) if t]
        for token in tokens:
            pattern = f"%{token}%"
            clauses.append(
                "("
                "COALESCE(LOWER(s.scheme_name), '') LIKE %s "
                "OR CAST(s.scheme_code AS TEXT) LIKE %s "
                "OR COALESCE(LOWER(s.fund_house), '') LIKE %s "
                "OR COALESCE(LOWER(s.category), '') LIKE %s "
                "OR COALESCE(LOWER(s.plan_type), '') LIKE %s "
                "OR COALESCE(LOWER(s.option_type), '') LIKE %s"
                ")"
            )
            params.extend([pattern, pattern, pattern, pattern, pattern, pattern])

    if min_return_30d is not None:
        clauses.append("s.return_30d_pct >= %s")
        params.append(min_return_30d)

    if max_expense_ratio is not None and max_expense_ratio > 0:
        clauses.append("COALESCE(s.ter_base_expense_ratio, s.expense_ratio) <= %s")
        params.append(max_expense_ratio)

    if official_ter_only:
        clauses.append("s.ter_status = 'official'")

    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where_sql, params

@cached(ttl=600)
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
    max_expense_ratio=None,
    official_ter_only=False,
) -> pd.DataFrame:
    start_date, end_date = _normalize_date_range(start_date, end_date)
    where_sql, params = _build_screener_where(amc, broad_cat, sub_cat, plan_type, option_type, search_term, scheme_code=scheme_code, max_expense_ratio=max_expense_ratio, official_ter_only=official_ter_only)

    col_map = {
        "scheme_code": "scheme_code",
        "scheme_name": "scheme_name",
        "fund_house": "fund_house",
        "category": "category",
        "plan_type": "plan_type",
        "option_type": "option_type",
        "expense_ratio": "COALESCE(s.ter_base_expense_ratio, s.expense_ratio)",
        "ter_base_expense_ratio": "COALESCE(s.ter_base_expense_ratio, s.expense_ratio)",
        "ter_status": "ter_status",
        "latest_nav": "latest_nav",
        "latest_date": "latest_date",
        "change_1d_pct": "change_1d_pct",
        "return_7d_pct": "return_7d_pct",
        "return_30d_pct": "return_30d_pct",
        "return_90d_pct": "return_90d_pct",
        "return_1y_pct": "return_1y_pct",
        "return_3y_pct": "return_3y_pct",
        "return_5y_pct": "return_5y_pct",
        "return_10y_pct": "return_10y_pct",
        "dist_from_52w_high_pct": "dist_from_52w_high_pct",
        "period_return_pct": "period_return_pct",
    }
    sort_col = col_map.get(sort_by, "return_30d_pct")
    order_dir = "ASC" if ascending else "DESC"
    limit_clause = f"LIMIT {limit}" if limit else ""

    con = get_connection()
    try:
        if start_date and end_date:
            _configure_parallel_planner(con)
            extra_cond = "AND s.latest_date >= %s" if where_sql else "WHERE s.latest_date >= %s"
            where_sql_active = f"{where_sql} {extra_cond}"
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
                    s.ter_base_expense_ratio,
                    s.ter_brokerage_cost_pct,
                    s.ter_transaction_cost_pct,
                    s.ter_statutory_levies_pct,
                    s.ter_status,
                    s.ter_source,
                    s.ter_as_of_date,
                    COALESCE(pe.nav, s.latest_nav) as latest_nav,
                    s.latest_date,
                    s.change_1d_pct,
                    s.return_7d_pct,
                    s.return_30d_pct,
                    s.return_90d_pct,
                    s.return_1y_pct,
                    s.return_3y_pct,
                    s.return_5y_pct,
                    s.return_10y_pct,
                    CASE
                        WHEN ps.nav IS NULL OR ps.nav <= 0 THEN NULL
                        ELSE ROUND((((COALESCE(pe.nav, s.latest_nav) - ps.nav) / NULLIF(ps.nav, 0) * 100.0))::numeric, 4)::double precision
                    END as period_return_pct,
                    s.high_52w,
                    s.low_52w,
                    s.dist_from_52w_high_pct,
                    s.isin
                FROM summary_table s
                LEFT JOIN LATERAL (
                    SELECT nav, nav_date FROM nav_history
                    WHERE scheme_code = s.scheme_code AND nav_date >= %s AND nav_date <= %s
                    ORDER BY nav_date ASC
                    LIMIT 1
                ) ps ON TRUE
                LEFT JOIN LATERAL (
                    SELECT nav, nav_date FROM nav_history
                    WHERE scheme_code = s.scheme_code AND nav_date >= %s AND nav_date <= %s
                    ORDER BY nav_date DESC
                    LIMIT 1
                ) pe ON TRUE
                {where_sql_active}
                ORDER BY {sort_col} {order_dir} NULLS LAST
                {limit_clause};
            """
            all_params = [start_date, end_date, start_date, end_date] + params + [start_date]
            df = _fetchdf(con.execute(sql, all_params))
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
                    ter_base_expense_ratio,
                    ter_brokerage_cost_pct,
                    ter_transaction_cost_pct,
                    ter_statutory_levies_pct,
                    ter_status,
                    ter_source,
                    ter_as_of_date,
                    latest_nav,
                    latest_date,
                    change_1d_pct,
                    return_7d_pct,
                    return_30d_pct,
                    return_90d_pct,
                    return_1y_pct,
                    return_3y_pct,
                    return_5y_pct,
                    return_10y_pct,
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
            df = _fetchdf(con.execute(sql, params))
    finally:
        con.close()
    return df

get_screener_data = get_screener_dataframe

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

@cached(ttl=600)
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
        SELECT s.scheme_code, s.scheme_name, s.plan_type, s.option_type, s.fund_house, s.category
        FROM summary_table s
        {where_sql}
        ORDER BY s.scheme_name ASC
        {limit_clause};
    """
    try:
        rows = con.execute(sql, params).fetchall()
    except Exception:
        rows = []
    if not rows:
        # Fallback to schemes base table if summary_table has not been populated yet
        sql_fallback = f"""
            SELECT s.scheme_code, s.scheme_name, s.plan_type, s.option_type, s.fund_house, s.category
            FROM schemes s
            {where_sql}
            ORDER BY s.scheme_name ASC
            {limit_clause};
        """
        try:
            rows = con.execute(sql_fallback, params).fetchall()
        except Exception:
            rows = []
    con.close()
    return [
        {
            "scheme_code": r[0],
            "scheme_name": r[1],
            "plan_type": r[2],
            "option_type": r[3],
            "fund_house": r[4],
            "category": r[5] if len(r) > 5 else None,
            "display_label": format_scheme_display_name(r[1], r[2], r[3], r[0])
        }
        for r in rows
    ]

@cached(ttl=600)
def get_nav_history_dataframe(scheme_codes: List[int], start_date=None, end_date=None) -> pd.DataFrame:
    if not scheme_codes:
        return pd.DataFrame()
    start_date, end_date = _normalize_date_range(start_date, end_date)
    con = get_connection()
    placeholders = ", ".join(["%s"] * len(scheme_codes))
    params: List[Any] = list(scheme_codes)

    date_clauses = []
    if start_date:
        date_clauses.append("n.nav_date >= %s")
        params.append(start_date)
    if end_date:
        date_clauses.append("n.nav_date <= %s")
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
    df = _fetchdf(con.execute(sql, params))
    con.close()
    if not df.empty:
        df["base_scheme_name"] = df["raw_scheme_name"]
        df["scheme_name"] = df.apply(
            lambda r: format_scheme_display_name(r["raw_scheme_name"], r.get("plan_type"), r.get("option_type"), r["scheme_code"]),
            axis=1
        )
    return df

@cached(ttl=600)
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
    start_date, end_date = _normalize_date_range(start_date, end_date)
    where_sql, params = _build_screener_where("All Fund Houses", broad_cat, sub_cat, plan_type, "All Options", "")

    if start_date and end_date:
        _configure_parallel_planner(con)
        extra_cond = "AND s.latest_date >= %s" if where_sql else "WHERE s.latest_date >= %s"
        where_sql_active = f"{where_sql} {extra_cond}"
        sql_gainers = f"""
            WITH calc AS (
                SELECT s.scheme_code, s.scheme_name, s.plan_type, s.option_type, s.fund_house, s.category,
                       CASE
                           WHEN ps.nav IS NULL OR ps.nav <= 0 THEN NULL
                           ELSE ROUND((((COALESCE(pe.nav, s.latest_nav) - ps.nav) / NULLIF(ps.nav, 0) * 100.0))::numeric, 4)::double precision
                       END as return_pct
                FROM summary_table s
                LEFT JOIN LATERAL (
                    SELECT nav, nav_date FROM nav_history
                    WHERE scheme_code = s.scheme_code AND nav_date >= %s AND nav_date <= %s
                    ORDER BY nav_date ASC
                    LIMIT 1
                ) ps ON TRUE
                LEFT JOIN LATERAL (
                    SELECT nav, nav_date FROM nav_history
                    WHERE scheme_code = s.scheme_code AND nav_date >= %s AND nav_date <= %s
                    ORDER BY nav_date DESC
                    LIMIT 1
                ) pe ON TRUE
                {where_sql_active}
            )
            SELECT scheme_code, scheme_name, plan_type, option_type, fund_house, category, return_pct
            FROM calc
            WHERE return_pct IS NOT NULL
            ORDER BY return_pct DESC
            LIMIT {top_n};
        """
        sql_losers = f"""
            WITH calc AS (
                SELECT s.scheme_code, s.scheme_name, s.plan_type, s.option_type, s.fund_house, s.category,
                       CASE
                           WHEN ps.nav IS NULL OR ps.nav <= 0 THEN NULL
                           ELSE ROUND((((COALESCE(pe.nav, s.latest_nav) - ps.nav) / NULLIF(ps.nav, 0) * 100.0))::numeric, 4)::double precision
                       END as return_pct
                FROM summary_table s
                LEFT JOIN LATERAL (
                    SELECT nav, nav_date FROM nav_history
                    WHERE scheme_code = s.scheme_code AND nav_date >= %s AND nav_date <= %s
                    ORDER BY nav_date ASC
                    LIMIT 1
                ) ps ON TRUE
                LEFT JOIN LATERAL (
                    SELECT nav, nav_date FROM nav_history
                    WHERE scheme_code = s.scheme_code AND nav_date >= %s AND nav_date <= %s
                    ORDER BY nav_date DESC
                    LIMIT 1
                ) pe ON TRUE
                {where_sql_active}
            )
            SELECT scheme_code, scheme_name, plan_type, option_type, fund_house, category, return_pct
            FROM calc
            WHERE return_pct IS NOT NULL
            ORDER BY return_pct ASC
            LIMIT {top_n};
        """
        all_p = [start_date, end_date, start_date, end_date] + params + [start_date]
        df_gainers = _fetchdf(con.execute(sql_gainers, all_p))
        df_losers = _fetchdf(con.execute(sql_losers, all_p))
    else:
        valid_cols = [
            "change_1d_pct", "return_7d_pct", "return_30d_pct", "return_90d_pct",
            "return_1y_pct", "return_3y_pct", "return_5y_pct", "return_10y_pct"
        ]
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
        df_gainers = _fetchdf(con.execute(sql_gainers, params))

        sql_losers = f"""
            SELECT s.scheme_code, s.scheme_name, s.plan_type, s.option_type, s.fund_house, s.category, s.{period_col} as return_pct
            FROM summary_table s
            {filter_cond}
            ORDER BY s.{period_col} ASC
            LIMIT {top_n};
        """
        df_losers = _fetchdf(con.execute(sql_losers, params))

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

@cached(ttl=600)
def get_advanced_leaders_dataframe(
    broad_cat="All Categories",
    sub_cat="All Sub-Categories",
    plan_type="All Plans",
    option_type="All Options",
    start_date=None,
    end_date=None,
    vol_lookback="1Y"
) -> pd.DataFrame:
    """
    Computes comprehensive relative alpha, peer category medians, quartiles,
    annualized volatility, win rate, and drawdown metrics across all matching funds
    over the active date window for institutional leadership analysis.
    Supports configurable volatility lookback windows (1Y, 6M, 3Y, Full Window).
    """
    con = get_connection()
    start_date, end_date = _normalize_date_range(start_date, end_date)
    where_sql, params = _build_screener_where("All Fund Houses", broad_cat, sub_cat, plan_type, option_type, "")

    try:
        if start_date and end_date:
            _configure_parallel_planner(con)
            extra_cond = "AND s.latest_date >= %s" if where_sql else "WHERE s.latest_date >= %s"
            where_sql_active = f"{where_sql} {extra_cond}"
            if vol_lookback in ("6M", "6m", "180d", "180"):
                vol_start = max(start_date, end_date - datetime.timedelta(days=180))
            elif vol_lookback in ("3Y", "3y", "1095d", "1095"):
                vol_start = max(start_date, end_date - datetime.timedelta(days=1095))
            elif vol_lookback in ("all", "full", "Full Window", "Full"):
                vol_start = start_date
            else:  # default "1Y"
                vol_start = max(start_date, end_date - datetime.timedelta(days=365))
            sql = f"""
                WITH matching_schemes AS MATERIALIZED (
                    SELECT s.scheme_code
                    FROM summary_table s
                    {where_sql_active}
                ),
                daily_returns AS (
                    SELECT
                        nh.scheme_code,
                        nh.nav_date,
                        (nh.nav - LAG(nh.nav, 1) OVER (PARTITION BY nh.scheme_code ORDER BY nh.nav_date ASC)) / NULLIF(LAG(nh.nav, 1) OVER (PARTITION BY nh.scheme_code ORDER BY nh.nav_date ASC), 0) as daily_ret
                    FROM nav_history nh
                    JOIN matching_schemes ms ON nh.scheme_code = ms.scheme_code
                    WHERE nh.nav_date >= %s AND nh.nav_date <= %s
                ),
                vol_calc AS (
                    SELECT
                        scheme_code,
                        ROUND((stddev_samp(daily_ret) * SQRT(252.0) * 100.0)::numeric, 4)::double precision as annualized_vol_pct,
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
                        s.latest_nav,
                        s.dist_from_52w_high_pct,
                        s.high_52w,
                        s.low_52w,
                        CASE
                            WHEN ps.nav IS NULL OR ps.nav <= 0 THEN NULL
                            ELSE ROUND((((COALESCE(pe.nav, s.latest_nav) - ps.nav) / NULLIF(ps.nav, 0) * 100.0))::numeric, 4)::double precision
                        END as period_return_pct,
                        v.annualized_vol_pct as annualized_vol_pct,
                        COALESCE(v.trading_days, 0) as n_trading_days,
                        ROUND(((COALESCE(v.up_days, 0) * 100.0 / NULLIF(v.trading_days, 0)))::numeric, 2)::double precision as win_rate_pct
                    FROM summary_table s
                    LEFT JOIN LATERAL (
                        SELECT nav, nav_date FROM nav_history
                        WHERE scheme_code = s.scheme_code AND nav_date >= %s AND nav_date <= %s
                        ORDER BY nav_date ASC
                        LIMIT 1
                    ) ps ON TRUE
                    LEFT JOIN LATERAL (
                        SELECT nav, nav_date FROM nav_history
                        WHERE scheme_code = s.scheme_code AND nav_date >= %s AND nav_date <= %s
                        ORDER BY nav_date DESC
                        LIMIT 1
                    ) pe ON TRUE
                    LEFT JOIN vol_calc v ON s.scheme_code = v.scheme_code
                    {where_sql_active}
                )
                SELECT *
                FROM scheme_perf
                WHERE period_return_pct IS NOT NULL;
            """
            all_p = params + [start_date] + [vol_start, end_date, start_date, end_date, start_date, end_date] + params + [start_date]
            df = _fetchdf(con.execute(sql, all_p))

            # True peer-category median: scoped only by broad/sub-category (which is what "category"
            # means) and NOT by the Plan/Option filters above.
            # When looking across all plans and options, the in-view df already contains the full population,
            # so we can compute the true category median directly in pandas with zero additional DB round-trips.
            if (not plan_type or plan_type == "All Plans") and (not option_type or option_type == "All Options") and not df.empty:
                df_cat_median = (
                    df.dropna(subset=["category", "period_return_pct"])
                    .groupby("category", as_index=False)["period_return_pct"]
                    .median()
                    .rename(columns={"period_return_pct": "true_cat_median"})
                )
            else:
                where_sql_cat, params_cat = _build_screener_where("All Fund Houses", broad_cat, sub_cat, "All Plans", "All Options", "")
                extra_cond_cat = "AND s.latest_date >= %s" if where_sql_cat else "WHERE s.latest_date >= %s"
                where_sql_cat_active = f"{where_sql_cat} {extra_cond_cat}"
                cat_median_sql = f"""
                    WITH period_calc AS (
                        SELECT s.category,
                               CASE
                                   WHEN ps.nav IS NULL OR ps.nav <= 0 THEN NULL
                                   ELSE ROUND((((COALESCE(pe.nav, s.latest_nav) - ps.nav) / NULLIF(ps.nav, 0) * 100.0))::numeric, 4)::double precision
                               END as period_return_pct
                        FROM summary_table s
                        LEFT JOIN LATERAL (
                            SELECT nav, nav_date FROM nav_history
                            WHERE scheme_code = s.scheme_code AND nav_date >= %s AND nav_date <= %s
                            ORDER BY nav_date ASC
                            LIMIT 1
                        ) ps ON TRUE
                        LEFT JOIN LATERAL (
                            SELECT nav, nav_date FROM nav_history
                            WHERE scheme_code = s.scheme_code AND nav_date >= %s AND nav_date <= %s
                            ORDER BY nav_date DESC
                            LIMIT 1
                        ) pe ON TRUE
                        {where_sql_cat_active}
                    )
                    SELECT s.category,
                           percentile_cont(0.5) WITHIN GROUP (ORDER BY period_return_pct) as true_cat_median
                    FROM period_calc s
                    WHERE s.category IS NOT NULL AND s.period_return_pct IS NOT NULL
                    GROUP BY s.category;
                """
                cat_median_params = [start_date, end_date, start_date, end_date] + params_cat + [start_date]
                try:
                    df_cat_median = _fetchdf(con.execute(cat_median_sql, cat_median_params))
                except Exception:
                    df_cat_median = pd.DataFrame(columns=["category", "true_cat_median"])
        else:
            filter_cond = f"{where_sql} {'AND' if where_sql else 'WHERE'} s.latest_nav IS NOT NULL"
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
                    s.latest_nav,
                    s.dist_from_52w_high_pct,
                    s.high_52w,
                    s.low_52w,
                    COALESCE(s.return_30d_pct, s.return_7d_pct, s.change_1d_pct, 0.0) as period_return_pct,
                    CAST(NULL AS DOUBLE PRECISION) as annualized_vol_pct,
                    0 as n_trading_days,
                    50.0 as win_rate_pct
                FROM summary_table s
                {filter_cond};
            """
            df = _fetchdf(con.execute(sql, params))
            df_cat_median = pd.DataFrame(columns=["category", "true_cat_median"])
    finally:
        con.close()
    if not df.empty:
        # Standardized display label (fast zip comprehension avoids 15,000 pd.Series allocations)
        names = df["scheme_name"].tolist()
        plans = df["plan_type"].fillna("").tolist() if "plan_type" in df else [""] * len(df)
        options = df["option_type"].fillna("").tolist() if "option_type" in df else [""] * len(df)
        codes = df["scheme_code"].tolist()
        df["display_name"] = [
            format_scheme_display_name(n, p, o, c) for n, p, o, c in zip(names, plans, options, codes)
        ]
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

get_leaders_laggards = get_advanced_leaders_dataframe

@cached(ttl=600)
def get_scheme_profile(scheme_code: int) -> Tuple[Optional[Dict[str, Any]], pd.DataFrame]:
    con = get_connection()
    summary = _fetchdf(con.execute("SELECT * FROM summary_table WHERE scheme_code = %s", [scheme_code]))
    if summary.empty:
        summary = _fetchdf(con.execute("SELECT * FROM schemes WHERE scheme_code = %s", [scheme_code]))
    history = _fetchdf(con.execute("SELECT nav_date, nav FROM nav_history WHERE scheme_code = %s ORDER BY nav_date DESC", [scheme_code]))
    con.close()

    profile = summary.to_dict(orient="records")[0] if not summary.empty else None
    return profile, history


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
    try:
        schemes_count = con.execute("SELECT count(*) FROM schemes;").fetchone()[0]
        nav_count = con.execute("SELECT count(*) FROM nav_history;").fetchone()[0]
        amc_count = con.execute("SELECT count(DISTINCT fund_house) FROM schemes WHERE fund_house IS NOT NULL;").fetchone()[0]
        cat_count = con.execute("SELECT count(DISTINCT category) FROM schemes WHERE category IS NOT NULL;").fetchone()[0]
        min_date = con.execute("SELECT min(nav_date) FROM nav_history;").fetchone()[0]
        max_date = con.execute("SELECT max(nav_date) FROM nav_history;").fetchone()[0]
        try:
            ter_count = con.execute("SELECT count(*) FROM ter_history;").fetchone()[0]
        except Exception:
            ter_count = 0
        try:
            ter_official_schemes = con.execute("SELECT count(*) FROM schemes WHERE ter_status = 'official';").fetchone()[0]
        except Exception:
            ter_official_schemes = 0
        # There is no database *file* to stat any more -- the cluster lives inside
        # the postgres container's own volume, and this process cannot see it. Ask
        # the server instead. pg_database_size() reports the on-disk size of this
        # database including its indexes and TOAST, which is the closest analogue
        # of what the old os.path.getsize(DB_PATH) meant.
        file_size_mb = round(
            con.execute("SELECT pg_database_size(current_database()) / 1048576.0;").fetchone()[0], 2
        )
    finally:
        con.close()

    res = {
        "schemes_count": schemes_count,
        "nav_count": nav_count,
        "amc_count": amc_count,
        "category_count": cat_count,
        "ter_count": ter_count,
        "ter_official_schemes": ter_official_schemes,
        "min_date": min_date,
        "max_date": max_date,
        "file_size_mb": file_size_mb,
        # Kept under the same key the UI and /api/meta/status already read, but it
        # is now a DSN, not a path -- with the password stripped, since this value
        # is served to the browser.
        "db_path": _redacted_dsn(),
    }
    _STATS_CACHE = res
    _STATS_CACHE_TIME = now
    return dict(res)

@cached(ttl=600)
def get_cost_data_coverage() -> Dict[str, Any]:
    """Breaks down how many schemes have an 'official' (dated, source-linked) vs.
    'legacy_unverified' (bundled CSV, name-matched) vs. 'unknown' TER record.
    Official TER comes from the AMFI TER-disclosure portal sync
    (see amfi_sync.sync_official_ter). ter_auto_synced counts schemes whose
    ter_source tag matches that portal specifically."""
    con = get_connection()
    ter_rows = con.execute("""
        SELECT COALESCE(ter_status, 'unknown') as status, count(*)
        FROM schemes GROUP BY 1;
    """).fetchall()
    ter_auto_synced = con.execute("""
        SELECT count(*) FROM schemes
        WHERE ter_status = 'official' AND ter_source ILIKE 'AMFI Total Expense Ratio Disclosure%';
    """).fetchone()[0]
    total = con.execute("SELECT count(*) FROM schemes;").fetchone()[0]
    con.close()

    ter_counts = {status: cnt for status, cnt in ter_rows}
    return {
        "total_schemes": total,
        "ter_official": ter_counts.get("official", 0),
        "ter_auto_synced": ter_auto_synced,
        "ter_legacy": ter_counts.get("legacy_unverified", 0),
        "ter_unknown": ter_counts.get("unknown", 0),
    }


def get_scheme_identity_map() -> pd.DataFrame:
    """Raw (scheme_code, scheme_name, plan_type) for every scheme â€” used only for
    server-side name-matching against external sources (see amfi_sync.sync_official_ter),
    never for UI display, so it deliberately reads the base table rather than the cached,
    @cached-wrapped summary_table view."""
    con = get_connection()
    df = _fetchdf(con.execute("SELECT scheme_code, scheme_name, plan_type FROM schemes;"))
    con.close()
    return df


@cached(ttl=600)
def get_scheme_ter_history(scheme_code: int) -> pd.DataFrame:
    """Full dated Regulation 66 TER disclosure history for one scheme, oldest first â€”
    only populated once amfi_sync.sync_official_ter has matched that scheme at least once."""
    con = get_connection()
    df = _fetchdf(con.execute(
        """
        SELECT ter_date, base_expense_ratio_pct, brokerage_cost_pct, transaction_cost_pct,
               statutory_levies_pct, total_ter_pct, source_url
        FROM ter_history
        WHERE scheme_code = %s
        ORDER BY ter_date ASC;
        """,
        [scheme_code],
    ))
    con.close()
    return df


def upsert_ter_history(records: pd.DataFrame) -> int:
    """Serialized via WRITE_LOCK â€” see refresh_summary_table()."""
    with WRITE_LOCK:
        return _upsert_ter_history_impl(records)


def _upsert_ter_history_impl(records: pd.DataFrame) -> int:
    if records.empty:
        return 0
    con = get_connection()
    try:
        con.begin()
        _write_staging_table(con, "stg_ter_history", records,
                             like_table="ter_history",
                             key_columns=["scheme_code", "ter_date"])
        con.execute("""
            DELETE FROM ter_history
            WHERE EXISTS (
                SELECT 1 FROM stg_ter_history s
                WHERE ter_history.scheme_code = s.scheme_code AND ter_history.ter_date = s.ter_date
            );
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
        con.execute("DROP TABLE IF EXISTS stg_ter_history")
        con.commit()
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass
        raise
    finally:
        con.close()
    bump_data_version()
    invalidate_database_stats_cache()
    return len(records)


def apply_latest_official_ter(records: pd.DataFrame) -> Dict[str, Any]:
    """Serialized via WRITE_LOCK — see refresh_summary_table()."""
    with WRITE_LOCK:
        return _apply_latest_official_ter_impl(records)


def _apply_latest_official_ter_impl(records: pd.DataFrame) -> Dict[str, Any]:
    """Promotes each matched scheme's latest dated AMFI TER-portal row to the schemes
    table's cached 'current' cost fields, including the BER/Brokerage/Transaction/Statutory
    breakdown this source provides. Never moves ter_as_of_date backwards: if a scheme
    already carries a more recent official as-of date, this row is skipped for that
    scheme rather than regressing it."""
    if records.empty:
        return {"updated": 0}
    con = get_connection()
    try:
        con.begin()
        staging = records.copy()
        staging["ter_status"] = costs_data.STATUS_OFFICIAL
        # like_table="schemes" types the scheme_code join key; the TER breakdown columns
        # have no counterpart there and stay untyped, which is harmless -- they are only
        # projected into the UPDATE's SET list, never joined on.
        _write_staging_table(con, "stg_latest_ter", staging,
                             like_table="schemes", key_columns=["scheme_code"])
        updated = con.execute("""
            UPDATE schemes
            SET expense_ratio = c.total_ter_pct::double precision,
                ter_base_expense_ratio = c.base_expense_ratio_pct::double precision,
                ter_brokerage_cost_pct = c.brokerage_cost_pct::double precision,
                ter_transaction_cost_pct = c.transaction_cost_pct::double precision,
                ter_statutory_levies_pct = c.statutory_levies_pct::double precision,
                ter_status = c.ter_status,
                ter_source = c.ter_source,
                ter_source_url = c.source_url,
                ter_as_of_date = c.ter_date::date
            FROM stg_latest_ter c
            WHERE c.scheme_code = schemes.scheme_code
              AND (schemes.ter_as_of_date IS NULL OR schemes.ter_as_of_date <= c.ter_date::date)
            RETURNING schemes.scheme_code;
        """).fetchall()
        con.execute("DROP TABLE IF EXISTS stg_latest_ter")
        con.commit()
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass
        raise
    finally:
        con.close()
    refresh_summary_table()
    return {"updated": len(updated)}


def get_sync_meta_values(keys: List[str]) -> Dict[str, str]:
    if not keys:
        return {}
    con = get_connection()
    try:
        placeholders = ",".join(["%s"] * len(keys))
        rows = con.execute(
            f"SELECT key, value FROM sync_meta WHERE key IN ({placeholders})",
            list(keys),
        ).fetchall()
        return {r[0]: r[1] for r in rows}
    except Exception:
        return {}
    finally:
        con.close()


def set_sync_meta_value(key: str, value: str) -> None:
    con = get_connection()
    try:
        con.execute(
            """INSERT INTO sync_meta (key, value) VALUES (%s, %s)
               ON CONFLICT (key) DO UPDATE SET value = excluded.value""",
            (key, value),
        )
    finally:
        con.close()


def get_scheme_profile_only(scheme_code: int) -> Optional[Dict[str, Any]]:
    """summary_table identity row only — no unbounded nav_history."""
    con = get_connection()
    try:
        summary = _fetchdf(con.execute("SELECT * FROM summary_table WHERE scheme_code = %s", [scheme_code]))
        if summary.empty:
            summary = _fetchdf(con.execute("SELECT * FROM schemes WHERE scheme_code = %s", [scheme_code]))
        return summary.to_dict(orient="records")[0] if not summary.empty else None
    finally:
        con.close()


def get_data_quality() -> Dict[str, Any]:
    from app.factor_model import DEFAULT_FACTOR_PROXIES, get_factor_proxies

    con = get_connection()
    try:
        schemes_count = con.execute("SELECT count(*) FROM schemes").fetchone()[0]
        nav_count = con.execute("SELECT count(*) FROM nav_history").fetchone()[0]
        ter_official = con.execute("SELECT count(*) FROM schemes WHERE ter_status = 'official'").fetchone()[0]
        ter_unknown = con.execute(
            "SELECT count(*) FROM schemes WHERE ter_status IS NULL OR ter_status = 'unknown'"
        ).fetchone()[0]
        ter_legacy = con.execute(
            "SELECT count(*) FROM schemes WHERE ter_status = 'legacy_unverified'"
        ).fetchone()[0]
        by_amc = con.execute("""
            SELECT COALESCE(fund_house, '(unknown)') AS fund_house,
                   count(*) FILTER (WHERE ter_status = 'official') AS official,
                   count(*) AS total
            FROM schemes GROUP BY 1 ORDER BY total DESC LIMIT 40
        """).fetchall()
        by_cat = con.execute("""
            SELECT COALESCE(
                CASE
                    WHEN category ILIKE '%equity%' THEN 'Equity'
                    WHEN category ILIKE '%debt%' OR category ILIKE '%income%' THEN 'Debt'
                    WHEN category ILIKE '%hybrid%' THEN 'Hybrid'
                    WHEN category ILIKE '%gold%' THEN 'Gold'
                    ELSE 'Other'
                END, 'Other') AS broad_category,
                   count(*) FILTER (WHERE ter_status = 'official') AS official,
                   count(*) AS total
            FROM schemes GROUP BY 1 ORDER BY total DESC
        """).fetchall()
        proxies = get_factor_proxies()
        market_last = con.execute(
            "SELECT max(nav_date) FROM nav_history WHERE scheme_code = %s",
            (proxies.get("factor_proxy_market", DEFAULT_FACTOR_PROXIES["factor_proxy_market"]),),
        ).fetchone()[0]
        mom_last = con.execute(
            "SELECT max(nav_date) FROM nav_history WHERE scheme_code = %s",
            (proxies.get("factor_proxy_momentum", DEFAULT_FACTOR_PROXIES["factor_proxy_momentum"]),),
        ).fetchone()[0]
        max_date = con.execute("SELECT max(nav_date) FROM nav_history").fetchone()[0]
        nav_max_date_lag_days = None
        nav_gap_rate = None
        window_end = datetime.date.today()
        window_start = window_end - datetime.timedelta(days=90)
        expected_weekdays = sum(1 for i in range(91) if (window_start + datetime.timedelta(days=i)).weekday() < 5)
        actual_sessions = con.execute(
            "SELECT count(DISTINCT nav_date) FROM nav_history WHERE nav_date >= %s AND nav_date <= %s",
            (window_start, window_end),
        ).fetchone()[0]
        nav_gap_rate = None
        if expected_weekdays:
            nav_gap_rate = round(max(0.0, 1.0 - float(actual_sessions) / float(expected_weekdays)), 4)
        nav_max_date_lag_days = (window_end - max_date).days if max_date is not None else None
        summary_built = None
        try:
            summary_built = con.execute(
                "SELECT value FROM sync_meta WHERE key = 'summary_table_built_at'"
            ).fetchone()
            summary_built = summary_built[0] if summary_built else None
        except Exception:
            summary_built = None
    finally:
        con.close()

    def _cov_rows(rows, key_name):
        out = []
        for r in rows:
            total = int(r[2] or 0)
            official = int(r[1] or 0)
            out.append({
                key_name: r[0],
                "official": official,
                "total": total,
                "coverage": round(official / total, 4) if total else 0.0,
            })
        return out

    return {
        "schemes_count": int(schemes_count),
        "nav_count": int(nav_count),
        "ter_official_schemes": int(ter_official),
        "ter_unknown_schemes": int(ter_unknown),
        "ter_legacy_schemes": int(ter_legacy),
        "ter_official_coverage_ratio": round(ter_official / schemes_count, 4) if schemes_count else 0.0,
        "nav_gap_rate": nav_gap_rate,
        "nav_max_date_lag_days": nav_max_date_lag_days,
        "factor_market_last_nav": str(market_last) if market_last else None,
        "factor_momentum_last_nav": str(mom_last) if mom_last else None,
        "summary_table_built_at": summary_built,
        "ter_official_by_amc": _cov_rows(by_amc, "fund_house"),
        "ter_official_by_broad_category": _cov_rows(by_cat, "broad_category"),
        "factor_proxy_codes": proxies,
    }
