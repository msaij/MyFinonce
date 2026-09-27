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

def find_nav_split_events(con, since: Optional[datetime.date] = None) -> List[Dict[str, Any]]:
    """Unit-split / face-value-reset events still unadjusted in nav_history.

    Detection runs in the database rather than pandas: the old implementation pulled every
    NAV row into a DataFrame to compute one ratio column, which at 27M rows is a few GB and
    got the container OOM-killed -- so the normalizer could not be run at all on real data.
    Postgres computes the same lag() ratio over an index scan and returns only the handful
    of rows that match.

    A "segregated portfolio" (a side-pocket created when a debt scheme's paper defaults)
    settles with a lump-sum recovery distribution that can land near a clean ratio -- a
    one-time payout, not a redenomination. Back-adjusting one would fabricate continuous
    history, so those schemes are excluded by name."""
    candidates = sorted({float(f) for f in _SPLIT_CANDIDATES})
    rows = con.execute(
        """
        WITH ratios AS (
            SELECT n.scheme_code, n.nav_date,
                   n.nav / NULLIF(lag(n.nav) OVER (PARTITION BY n.scheme_code ORDER BY n.nav_date), 0) AS ratio
            FROM nav_history n
            WHERE (%s::date IS NULL OR n.nav_date >= %s::date)
        )
        SELECT r.scheme_code, r.nav_date, r.ratio
        FROM ratios r
        JOIN schemes s ON s.scheme_code = r.scheme_code
        WHERE r.ratio IS NOT NULL
          AND s.scheme_name NOT ILIKE '%%segregat%%'
          AND EXISTS (
              SELECT 1 FROM unnest(%s::double precision[]) AS c(v)
              WHERE abs(r.ratio - c.v) / c.v < %s
          )
        ORDER BY r.scheme_code, r.nav_date
        """,
        (since, since, candidates, _SPLIT_TOLERANCE),
    ).fetchall()
    return [{"scheme_code": int(r[0]), "nav_date": r[1], "ratio": float(r[2])} for r in rows]


def _normalize_nav_splits_impl() -> int:
    """Detects unit-split/face-value-reset events in nav_history and back-adjusts every
    NAV before the split date by the split's own measured ratio, so the series is
    continuous for return calculations -- the same "split-adjusted price" convention
    every stock/ETF data provider uses. Only ever multiplies real, already-published NAVs
    by a precisely measured factor; never invents a value. Idempotent: once adjusted, the
    boundary ratio settles near 1.0 and is never re-flagged on a later run.

    Returns the number of NAV rows rewritten."""
    con = get_connection()
    try:
        events = find_nav_split_events(con)
        if not events:
            return 0
        scheme_codes = sorted({e["scheme_code"] for e in events})
        # One row per (scheme, split date, ratio); every NAV strictly before a split date
        # is multiplied by the product of the ratios of all later splits for that scheme.
        # exp(sum(ln(ratio))) is that product -- every ratio here is positive by
        # construction, so the log is always defined.
        con.begin()
        con.execute("DROP TABLE IF EXISTS stg_nav_splits")
        con.execute("CREATE TEMP TABLE stg_nav_splits (scheme_code INTEGER, split_date DATE, ratio DOUBLE PRECISION)")
        con.executemany(
            "INSERT INTO stg_nav_splits (scheme_code, split_date, ratio) VALUES (%s, %s, %s)",
            [(e["scheme_code"], e["nav_date"], e["ratio"]) for e in events],
        )
        changed = con.execute(
            """
            UPDATE nav_history n
            SET nav = round((n.nav * f.factor)::numeric, 4)
            FROM (
                SELECT h.scheme_code, h.nav_date, exp(sum(ln(e.ratio))) AS factor
                FROM nav_history h
                JOIN stg_nav_splits e
                  ON e.scheme_code = h.scheme_code AND e.split_date > h.nav_date
                WHERE h.scheme_code = ANY(%s)
                GROUP BY h.scheme_code, h.nav_date
            ) f
            WHERE f.scheme_code = n.scheme_code AND f.nav_date = n.nav_date
            """,
            (scheme_codes,),
        ).rowcount
        con.execute("DROP TABLE IF EXISTS stg_nav_splits")
        con.commit()
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass
        raise
    finally:
        con.close()
    print(f"NAV split normalization: adjusted {changed:,} historical NAV rows "
          f"across {len({e['scheme_code'] for e in events})} scheme(s).")
    invalidate_database_stats_cache()
    return changed


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
            -- The NAV the 1-day figures are measured against, kept rather than
            -- discarded once change_1d_pct is derived from it. Holdings needs the
            -- rupee move of a position, units x (latest_nav - nav_1d_ago); rebuilding
            -- yesterday's NAV from a percentage rounded to 4 dp loses paise per fund.
            nav_1d_ago DOUBLE PRECISION,
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
            latest_date, latest_nav, nav_1d_ago, is_active, change_1d_pct, return_7d_pct,
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
        -- Each trailing return starts from the fund's NAV as of N days ago: the last NAV on or
        -- before that date, within a few days for weekends and holidays -- AMFI's own convention.
        -- The nearest NAV on EITHER side used to be taken from a wide band (330..420 days for
        -- 1Y), so a fund 342 days old reported its since-launch return as a "1-year" return
        -- (52 live schemes) and was ranked beside real one-year records; ties had no order.
        nav_7d AS (
            SELECT l.scheme_code, n7.nav as nav_7d_ago
            FROM latest_nav l, db_freshness f
            CROSS JOIN LATERAL (
                SELECT nav
                FROM nav_history
                WHERE scheme_code = l.scheme_code
                  AND nav_date <= (l.latest_date - 7)
                  AND nav_date >= (l.latest_date - 11)
                ORDER BY nav_date DESC
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
                  AND nav_date <= (l.latest_date - 30)
                  AND nav_date >= (l.latest_date - 37)
                ORDER BY nav_date DESC
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
                  AND nav_date <= (l.latest_date - 90)
                  AND nav_date >= (l.latest_date - 100)
                ORDER BY nav_date DESC
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
                  AND nav_date <= (l.latest_date - 365)
                  AND nav_date >= (l.latest_date - 375)
                ORDER BY nav_date DESC
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
                  AND nav_date <= (l.latest_date - 1095)
                  AND nav_date >= (l.latest_date - 1110)
                ORDER BY nav_date DESC
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
                  AND nav_date <= (l.latest_date - 1826)
                  AND nav_date >= (l.latest_date - 1841)
                ORDER BY nav_date DESC
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
                  AND nav_date <= (l.latest_date - 3652)
                  AND nav_date >= (l.latest_date - 3672)
                ORDER BY nav_date DESC
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
            -- Gated exactly like change_1d_pct below: a scheme too stale to get a
            -- 1-day percentage must not get a 1-day NAV either.
            CASE WHEN l.latest_date >= (f.global_max_date - 30)
                 THEN n1.nav_1d_ago END as nav_1d_ago,
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
    # Stamp when this cache was last rebuilt. get_data_quality() has always read this
    # key, but nothing ever wrote it, so it reported "never built" however often the
    # rebuild ran. Deliberately after the commit and on its own connection: inside the
    # rebuild transaction, any failure here (a database predating sync_meta, say) would
    # abort the whole rebuild, and a missing timestamp must never cost a good rebuild.
    try:
        set_sync_meta_value("summary_table_built_at", datetime.datetime.now().isoformat(timespec="seconds"))
    except Exception as e:
        print(f"Could not stamp summary_table_built_at: {e}")
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
    # Every other panel on this filter counts only active schemes; without this a wound-up
    # asset class could still offer a filter pill that leads to an empty table.
    res = con.execute("SELECT DISTINCT broad_category FROM summary_table WHERE is_active AND broad_category IS NOT NULL ORDER BY broad_category;").fetchall()
    con.close()
    return [r[0] for r in res]

@cached(ttl=600)
def get_subcategories(broad_category: Optional[str] = None) -> List[str]:
    con = get_connection()
    # Same is_active gap as get_broad_categories -- without it a wound-up category could
    # still offer a filter pill that leads to an empty table.
    if broad_category and broad_category != "All Categories":
        res = con.execute(
            "SELECT DISTINCT category FROM summary_table WHERE is_active AND broad_category = %s AND category IS NOT NULL ORDER BY category;",
            [broad_category]
        ).fetchall()
    else:
        res = con.execute(
            "SELECT DISTINCT category FROM summary_table WHERE is_active AND category IS NOT NULL ORDER BY category;"
        ).fetchall()
    con.close()
    return [r[0] for r in res]

#: Offered whenever some active scheme has no plan (or no option) AMFI ever stated, so
#: those schemes stay reachable. Without it they answer to no filter at all: "Direct" and
#: "Regular" between them returned 8,741 of 8,864 schemes, and nothing named the other 123.
UNSPECIFIED_FILTER = "Unspecified"

#: Legacy spelling of "unknown" written by the parser before it started recording an
#: absent value as NULL. Inactive schemes keep it forever, since nothing re-syncs them.
_UNKNOWN_OPTION_VALUES = ("Other",)


def plan_option_clauses(plan_type: str, option_type: str, alias: str = "s") -> Tuple[str, List[Any]]:
    """SQL for the global Plan/Option picker, as `AND ...` fragments plus their params.

    One implementation because four call sites need identical semantics -- the screener,
    the macro trend, the market-pulse aggregates and the category matrix. "Unspecified"
    needs its own predicate: NULL never satisfies `= 'Unspecified'`, and schemes that no
    longer re-sync still carry the older "Other" spelling of the same idea."""
    sql, params = "", []
    for column, value, head in ((f"{alias}.plan_type", plan_type, "All Plans"),
                                (f"{alias}.option_type", option_type, "All Options")):
        if not value or value == head:
            continue
        if value == UNSPECIFIED_FILTER:
            sql += f" AND ({column} IS NULL OR {column} = ANY(%s))"
            params.append(list(_UNKNOWN_OPTION_VALUES))
        else:
            sql += f" AND {column} = %s"
            params.append(value)
    return sql, params


def _filter_values(column: str, head: str) -> List[str]:
    """The values this column actually holds among active schemes, so a dropdown can never
    drift from the data (these were two hardcoded lists, one here and one in the browser)."""
    con = get_connection()
    try:
        rows = con.execute(
            f"SELECT DISTINCT {column} FROM summary_table WHERE is_active AND {column} IS NOT NULL"
        ).fetchall()
        has_unknown = con.execute(
            f"SELECT 1 FROM summary_table WHERE is_active AND ({column} IS NULL OR {column} = ANY(%s)) LIMIT 1",
            (list(_UNKNOWN_OPTION_VALUES),),
        ).fetchone() is not None
    except Exception:
        return [head]
    finally:
        con.close()
    values = sorted({str(r[0]).strip() for r in rows if r[0] and str(r[0]).strip() not in _UNKNOWN_OPTION_VALUES})
    return [head] + values + ([UNSPECIFIED_FILTER] if has_unknown else [])


@cached(ttl=600)
def get_options_list() -> List[str]:
    return _filter_values("option_type", "All Options")


@cached(ttl=600)
def get_plans_list() -> List[str]:
    return _filter_values("plan_type", "All Plans")

#: A house with one or two ranked schemes would otherwise top a performance ranking on noise --
#: one lucky fund posting +40% in a year says nothing about the AMC.
MIN_AMC_SCHEMES_FOR_RANKING = 5
#: As MIN_PEERS_FOR_ALPHA below: a peer group smaller than this has no meaningful median.
_MIN_PEERS = 5
_HORIZONS = {"1d": "change_1d_pct", "7d": "return_7d_pct", "30d": "return_30d_pct",
             "90d": "return_90d_pct", "1y": "return_1y_pct"}

# Feed asset-class ids -> the SEBI broad label classification.classify() reads, for a fund
# in the AUM snapshot that no scheme of ours matched (so there is no category to read).
_FEED_BROAD = {1: ("Equity", "Equity Scheme"), 2: ("Debt", "Debt Scheme"), 3: ("Hybrid", "Hybrid Scheme"),
               4: ("Solution Oriented", "Solution Oriented Scheme"), 6: ("Solution Oriented", "Solution Oriented Scheme")}


@cached(ttl=600)
def _pulse_universe(plan_type: str = "All Plans", option_type: str = "All Options") -> pd.DataFrame:
    """Every live scheme with its trailing returns, asset class, SEBI category and IDCW flag."""
    from app.classification import classify, is_idcw, sebi_category

    filt, filt_params = plan_option_clauses(plan_type, option_type, alias="s")
    con = get_connection()
    try:
        df = _fetchdf(con.execute(f"""
            SELECT s.scheme_code, s.scheme_name, s.fund_house, s.category, s.broad_category,
                   s.plan_type, s.option_type, s.expense_ratio, s.dist_from_52w_high_pct,
                   s.change_1d_pct, s.return_7d_pct, s.return_30d_pct, s.return_90d_pct, s.return_1y_pct
            FROM summary_table s
            WHERE s.is_active{filt}
        """, filt_params or None))
    finally:
        con.close()
    if df.empty:
        return df.assign(asset_class=[], peer_category=[], is_idcw=[], display_name=[])
    names = df["scheme_name"].tolist()
    df["asset_class"] = [classify(c, b, n) for c, b, n in zip(df["category"], df["broad_category"], names)]
    df["peer_category"] = [sebi_category(c) or "Uncategorised" for c in df["category"]]
    df["is_idcw"] = [is_idcw(o, n) for o, n in zip(df["option_type"], names)]
    df["display_name"] = [format_scheme_display_name(n, p, o, c) for n, p, o, c in
                          zip(names, df["plan_type"].tolist(), df["option_type"].tolist(), df["scheme_code"].tolist())]
    return df


def _returns_pool(uni: pd.DataFrame, option_type: str) -> pd.DataFrame:
    """The schemes whose NAV return means what it says. An IDCW plan's NAV falls by every
    payout, so averaging it in reported Debt's trailing year at 2.55% when its Growth plans'
    median was 5.41%. Only when the page is filtered to IDCW are they the pool."""
    if uni.empty:
        return uni
    want_idcw = str(option_type or "").strip().upper() == "IDCW"
    return uni[uni["is_idcw"] == want_idcw]


@cached(ttl=600)
def _fund_aum() -> pd.DataFrame:
    """AMFI's per-FUND assets (one row per fund, all plans together) with the fund's asset
    class and SEBI category. Empty until the first fund-performance snapshot is taken."""
    from app.classification import classify, sebi_category

    con = get_connection()
    try:
        snap = _fetchdf(con.execute("""
            SELECT f.fund_name, f.sub_category, f.category_id, f.fund_house, f.aum_cr, f.as_of,
                   s.category, s.broad_category, s.scheme_name
            FROM amfi_fund_snapshot f
            LEFT JOIN LATERAL (
                SELECT st.category, st.broad_category, st.scheme_name FROM summary_table st
                WHERE st.scheme_code = ANY(f.scheme_codes) LIMIT 1
            ) s ON TRUE
            WHERE f.aum_cr IS NOT NULL
        """))
    except Exception:
        return pd.DataFrame(columns=["fund_name", "fund_house", "aum_cr", "as_of", "asset_class", "peer_category"])
    finally:
        con.close()
    if snap.empty:
        return snap.assign(asset_class=[], peer_category=[])
    classes, cats = [], []
    for r in snap.itertuples(index=False):
        category, broad = r.category, r.broad_category
        if not isinstance(category, str) or not category:
            # No scheme of ours matched: rebuild the label from the feed's own taxonomy.
            broad, prefix = _FEED_BROAD.get(int(r.category_id or 0), ("", "Other Scheme"))
            sub = str(r.sub_category or "")
            if "index" in sub.lower() or "etf" in sub.lower():
                sub = "Index Funds"
            elif "fof" in sub.lower():
                sub = "FoF Domestic"
            elif not sub.lower().endswith("fund") and broad in ("Debt", "Hybrid"):
                sub = f"{sub} Fund"
            category = f"{prefix} - {sub}"
        classes.append(classify(category, broad, r.fund_name))
        cats.append(sebi_category(category) or "Uncategorised")
    snap["asset_class"] = classes
    snap["peer_category"] = cats
    return snap


def _peer_alpha(pool: pd.DataFrame, column: str) -> pd.Series:
    """Each scheme's return minus the median of its peers: same asset class, SEBI category
    and plan. NaN where the peer group is too small to have a meaningful median."""
    plan = pool["plan_type"].fillna(UNSPECIFIED_FILTER)
    grp = pool.groupby([pool["asset_class"], pool["peer_category"], plan])[column]
    med = grp.transform("median")
    n = grp.transform("count")
    return (pool[column] - med).where(n >= _MIN_PEERS)


@cached(ttl=600)
def get_market_overview_stats(plan_type: str = "All Plans", option_type: str = "All Options") -> Dict[str, Any]:
    """Market-pulse aggregates.

    Counts are of schemes that still publish NAVs, not of every scheme ever listed: about
    two thirds of the table is matured FMPs and wound-up plans. `total_schemes` keeps the
    all-time figure alongside, since the two answer different questions. Honours the global
    Plan/Option picker, like the rest of the page.

    Returns are over Growth-type plans only (see _returns_pool) and grouped by the asset
    class the money is actually in (classification.classify) rather than SEBI's broad
    buckets, whose "Other / Index / ETF" put Nifty 50 index funds, gilt ETFs and gold in one
    average. Medians sit beside means: one fund's 40% month moves a mean, not a median.

    Fund houses are sized by AMFI's reported assets (fund level, all plans) and scored by
    skill rather than mix: the median gap between each of their schemes and its own peer
    median. An equal-weighted average return mostly measured how much equity a house runs."""
    from app.classification import ASSET_CLASSES

    filt, filt_params = plan_option_clauses(plan_type, option_type, alias="s")
    con = get_connection()
    try:
        total_schemes = con.execute(f"SELECT count(*) FROM schemes s WHERE TRUE{filt};", filt_params).fetchone()[0]
        total_nav_records = con.execute("SELECT count(*) FROM nav_history;").fetchone()[0]
        date_row = con.execute("SELECT min(nav_date), max(nav_date) FROM nav_history;").fetchone()
    finally:
        con.close()

    uni = _pulse_universe(plan_type, option_type)
    pool = _returns_pool(uni, option_type)
    aum = _fund_aum()
    aum_total = float(aum["aum_cr"].sum()) if not aum.empty else 0.0

    # --- Asset classes ---------------------------------------------------------------
    asset_rows = []
    for cls in ASSET_CLASSES:
        part = pool[pool["asset_class"] == cls] if not pool.empty else pool
        live = int((uni["asset_class"] == cls).sum()) if not uni.empty else 0
        if live == 0:
            continue
        row: Dict[str, Any] = {"asset_class": cls, "broad_category": cls, "count": int(len(part)), "schemes_all": live}
        for h, col in _HORIZONS.items():
            vals = part[col].dropna() if not part.empty else pd.Series(dtype=float)
            row[f"avg_{h}"] = round(float(vals.mean()), 4) if len(vals) else None
            row[f"med_{h}"] = round(float(vals.median()), 4) if len(vals) else None
            row[f"n_{h}"] = int(len(vals))
        cls_aum = float(aum.loc[aum["asset_class"] == cls, "aum_cr"].sum()) if not aum.empty else 0.0
        row["aum_cr"] = round(cls_aum, 2) if cls_aum else None
        row["aum_share_pct"] = round(cls_aum / aum_total * 100.0, 2) if aum_total else None
        asset_rows.append(row)
    asset_dist = pd.DataFrame(asset_rows)

    # --- Fund houses by assets ----------------------------------------------------------
    amc_aum = pd.DataFrame(columns=["fund_house", "aum_cr", "share_pct", "funds"])
    if not aum.empty:
        g = aum.dropna(subset=["fund_house"]).groupby("fund_house").agg(aum_cr=("aum_cr", "sum"), funds=("fund_name", "count"))
        g["share_pct"] = (g["aum_cr"] / aum_total * 100.0).round(4)
        amc_aum = g.sort_values("aum_cr", ascending=False).reset_index()
        amc_aum["aum_cr"] = amc_aum["aum_cr"].round(2)

    # --- Fund-house scorecard (peer alpha) ------------------------------------------------
    scorecard = pd.DataFrame(columns=["fund_house", "ranked_schemes", "median_alpha_1y", "beat_peers_pct",
                                      "median_alpha_90d", "schemes_count", "aum_cr"])
    if not pool.empty:
        p = pool.copy()
        p["alpha_1y"] = _peer_alpha(p, "return_1y_pct")
        p["alpha_90d"] = _peer_alpha(p, "return_90d_pct")
        ranked = p.dropna(subset=["alpha_1y"])
        if not ranked.empty:
            s = ranked.groupby("fund_house").agg(
                ranked_schemes=("alpha_1y", "count"),
                median_alpha_1y=("alpha_1y", "median"),
                beat_peers_pct=("alpha_1y", lambda a: float((a > 0).mean() * 100.0)),
                median_alpha_90d=("alpha_90d", "median"),
            )
            s = s[s["ranked_schemes"] >= MIN_AMC_SCHEMES_FOR_RANKING]
            s["schemes_count"] = pool.groupby("fund_house").size().reindex(s.index).fillna(0).astype(int)
            if not amc_aum.empty:
                s["aum_cr"] = amc_aum.set_index("fund_house")["aum_cr"].reindex(s.index)
            else:
                s["aum_cr"] = None
            for c in ("median_alpha_1y", "median_alpha_90d", "beat_peers_pct"):
                s[c] = s[c].astype(float).round(4)
            scorecard = s.sort_values("median_alpha_1y", ascending=False).reset_index()

    # --- Best category (30D, by median, big enough to mean something) ----------------------
    best_cat = None
    if not pool.empty:
        # Distinct funds, not schemes: Direct and Regular are one fund counted twice.
        cats = pool.dropna(subset=["return_30d_pct"]).groupby(["asset_class", "peer_category"]).agg(
            median=("return_30d_pct", "median"), count=("scheme_name", "nunique"))
        cats = cats[cats["count"] >= _MIN_PEERS].sort_values("median", ascending=False)
        if not cats.empty:
            (cls, cat), top = cats.index[0], cats.iloc[0]
            best_cat = {"name": f"{cat} ({cls})", "return_pct": round(float(top["median"]), 4)}

    return {
        "total_schemes": total_schemes,
        "active_schemes": int(len(uni)),
        "total_amcs": int(uni["fund_house"].dropna().nunique()) if not uni.empty else 0,
        "total_nav_records": total_nav_records,
        "min_date": date_row[0],
        "max_date": date_row[1],
        "returns_pool": int(len(pool)),
        "idcw_excluded": int(len(uni) - len(pool)),
        "asset_classes": [r["asset_class"] for r in asset_rows],
        "asset_dist": asset_dist,
        "amc_aum": amc_aum,
        "amc_scorecard": scorecard,
        "aum_total_cr": round(aum_total, 2) if aum_total else None,
        "aum_as_of": aum["as_of"].max() if not aum.empty else None,
        "best_cat": best_cat,
    }


@cached(ttl=600)
def get_category_performance_matrix(broad_category: str = "All", plan_type: str = "All Plans",
                                    option_type: str = "All Options") -> pd.DataFrame:
    """Per-category aggregates for the Overview: one row per (asset class, SEBI category),
    over the same Growth-type pool and live schemes as the rest of Market Pulse. Grouped on
    the normalised category, so the "Liquid Fund" filed under AMFI's pre-2018 header and the
    one filed under the new header are one row, and "Index Funds" is split by what the fund
    tracks. `broad_category` takes an asset class (or, for older links, a SEBI broad bucket)."""
    columns = ["Asset Class", "Category", "Schemes", "AUM (Rs cr)", "Avg TER %", "Median 1D %", "Median 7D %",
               "Median 30D %", "Median 90D %", "Median 1Y %", "Funds with 1Y", "Median 52W High Gap %",
               "Top Fund (30D) %", "Top Fund (30D)", "Bottom Fund (30D) %", "Bottom Fund (30D)"]
    uni = _pulse_universe(plan_type, option_type)
    pool = _returns_pool(uni, option_type)
    if pool.empty:
        return pd.DataFrame(columns=columns)
    if broad_category and broad_category != "All":
        pool = pool[(pool["asset_class"] == broad_category) | (pool["broad_category"] == broad_category)]
        if pool.empty:
            return pd.DataFrame(columns=columns)
    aum = _fund_aum()
    aum_by = aum.groupby(["asset_class", "peer_category"])["aum_cr"].sum() if not aum.empty else pd.Series(dtype=float)
    rows = []
    for (cls, cat), part in pool.groupby(["asset_class", "peer_category"]):
        r30 = part.dropna(subset=["return_30d_pct"])
        top = r30.loc[r30["return_30d_pct"].idxmax()] if not r30.empty else None
        bottom = r30.loc[r30["return_30d_pct"].idxmin()] if not r30.empty else None

        def med(col: str) -> Optional[float]:
            v = part[col].dropna()
            return round(float(v.median()), 4) if len(v) else None

        ter = part["expense_ratio"].dropna()
        cat_aum = aum_by.get((cls, cat)) if len(aum_by) else None
        rows.append({
            "Asset Class": cls, "Category": cat, "Schemes": int(len(part)),
            "AUM (Rs cr)": round(float(cat_aum), 2) if cat_aum else None,
            "Avg TER %": round(float(ter.mean()), 4) if len(ter) else None,
            "Median 1D %": med("change_1d_pct"), "Median 7D %": med("return_7d_pct"),
            "Median 30D %": med("return_30d_pct"), "Median 90D %": med("return_90d_pct"),
            "Median 1Y %": med("return_1y_pct"), "Funds with 1Y": int(part["return_1y_pct"].notna().sum()),
            "Median 52W High Gap %": med("dist_from_52w_high_pct"),
            "Top Fund (30D) %": round(float(top["return_30d_pct"]), 4) if top is not None else None,
            "Top Fund (30D)": top["display_name"] if top is not None else None,
            "Bottom Fund (30D) %": round(float(bottom["return_30d_pct"]), 4) if bottom is not None else None,
            "Bottom Fund (30D)": bottom["display_name"] if bottom is not None else None,
        })
    out = pd.DataFrame(rows, columns=columns)
    return out.sort_values("Median 30D %", ascending=False, na_position="last").reset_index(drop=True)


@cached(ttl=600)
def get_macro_asset_class_trend(start_date: datetime.date, end_date: datetime.date, plan_type: str = "All Plans", option_type: str = "All Options") -> pd.DataFrame:
    """
    An indexed base-100 line per asset class (the same classes as the rest of Market Pulse)
    over the chosen window: the equal-weighted average fund in each class.
    """
    start_date, end_date = _normalize_date_range(start_date, end_date)
    empty = pd.DataFrame(columns=["nav_date", "Asset Class", "Indexed Performance"])
    if not start_date or not end_date:
        return empty
    uni = _pulse_universe(plan_type, option_type)
    pool = _returns_pool(uni, option_type)
    # "Other" is the classifier's catch-all, not an asset class anyone can hold a view on.
    pool = pool[pool["asset_class"] != "Other"] if not pool.empty else pool
    if pool.empty:
        return empty
    codes = [int(c) for c in pool["scheme_code"]]
    classes = pool["asset_class"].tolist()

    # Equal-weighted index of a FIXED basket: each fund is indexed to its own first NAV in
    # the window, and those ratios are averaged. The obvious-looking alternative -- average
    # the raw NAVs each day and divide by day one's average -- measures the population, not
    # performance, and that is what this did. Two reasons it fails:
    #   * Funds enter and leave daily (2,399 equity funds on day one, 2,438 ninety days
    #     later). A fund launching at Rs 10 into a pool averaging Rs 90 drags the mean down
    #     although nothing lost value; a matured Rs 10 FMP dropping out pushes it up.
    #   * Within one class NAVs span Rs 10 to Rs 3,000, so the mean tracks the biggest
    #     numbers rather than the typical fund.
    # Measured over 2026-06-24..2026-09-22 the old form reported Equity at 98.93 (down 1.1%)
    # while the funds were up 1.3%, and Debt at 102.99 against an actual 0.5%.
    sql = """
        WITH matching_schemes AS (
            SELECT * FROM unnest(%s::bigint[], %s::text[]) AS m(scheme_code, asset_class)
        ),
        first_nav AS (
            SELECT DISTINCT ON (nh.scheme_code)
                   nh.scheme_code, ms.asset_class, nh.nav AS base_nav, nh.nav_date AS base_date
            FROM nav_history nh
            JOIN matching_schemes ms ON nh.scheme_code = ms.scheme_code
            WHERE nh.nav_date >= %s AND nh.nav_date <= %s AND nh.nav > 0
            ORDER BY nh.scheme_code, nh.nav_date ASC
        ),
        window_start AS (SELECT min(base_date) AS d0 FROM first_nav),
        basket AS (
            -- Present when the window opened. A fund launched mid-window would otherwise
            -- join the average at exactly 100 and flatten everyone else's movement.
            SELECT f.* FROM first_nav f, window_start w
            WHERE f.base_date <= w.d0 + INTERVAL '7 days'
        )
        SELECT
            nh.nav_date,
            b.asset_class as "Asset Class",
            ROUND((AVG(nh.nav / b.base_nav) * 100.0)::numeric, 2)::double precision as "Indexed Performance",
            count(*) AS "Funds"
        FROM nav_history nh
        JOIN basket b ON b.scheme_code = nh.scheme_code
        WHERE nh.nav_date >= %s AND nh.nav_date <= %s
        GROUP BY nh.nav_date, b.asset_class
        ORDER BY nh.nav_date ASC;
    """
    con = get_connection()
    try:
        df = _fetchdf(con.execute(sql, [codes, classes, start_date, end_date, start_date, end_date]))
    except Exception:
        df = empty
    finally:
        con.close()
    if df.empty:
        return df
    # A point is an average only of the funds that priced that day. Overseas FoFs publish a
    # day late, so the latest date carried 26 of ~130 international funds; a weekend carries
    # only the liquid funds that price every calendar day. Such thin days are dropped rather
    # than let a quarter of the basket speak for all of it.
    full = df.groupby("Asset Class")["Funds"].transform("max")
    return df[df["Funds"] >= 0.6 * full].reset_index(drop=True)

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

def idcw_exclusion_clause(option_type: Any, alias: str = "s") -> str:
    """Predicate that keeps IDCW schemes out of "best/worst single fund" rankings.

    An IDCW scheme's NAV drops by exactly what it distributes, so a payout is indistinguishable
    from a crash to any return formula. On live data the worst 30-day "performer" in the whole
    market was an HDFC FMP IDCW plan at -20.95%, which had simply paid out; the worst fund that
    actually lost money was down 7.00%. A card that names one fund as the market's worst has to
    exclude them, or it reports a distribution as a loss every time.

    Returns "" when the user has explicitly filtered to IDCW -- then they asked for these
    schemes and an empty card would be the wrong answer. Matching on the name as well as the
    column catches the 46 live schemes AMFI labels "Growth" while naming them IDCW."""
    if str(option_type or "").strip().upper() == "IDCW":
        return ""
    p = f"{alias}." if alias else ""
    # "dividend" not followed by "yield": a Growth Dividend Yield fund is named for the stocks
    # it picks, and a bare '%%dividend%%' silently dropped all 62 of them from every ranking.
    return (f" AND ({p}option_type IS NULL OR {p}option_type <> 'IDCW')"
            f" AND {p}scheme_name NOT ILIKE '%%IDCW%%'"
            f" AND {p}scheme_name !~* 'dividend(?!\\s*yield)'")


@cached(ttl=600)
def get_kpis(amc="All Fund Houses", broad_cat="All Categories", sub_cat="All Sub-Categories", plan_type="All Plans", option_type="All Options", search_term="", start_date=None, end_date=None, scheme_code=None, max_expense_ratio=None, official_ter_only=False) -> Dict[str, Any]:
    con = get_connection()
    start_date, end_date = _normalize_date_range(start_date, end_date)
    where_sql, params = _build_screener_where(amc, broad_cat, sub_cat, plan_type, option_type, search_term, scheme_code=scheme_code, max_expense_ratio=max_expense_ratio, official_ter_only=official_ter_only)
    # Isolating one scheme by code is the user pointing at that exact fund, so the IDCW rule
    # is suspended there -- it exists to stop a payout winning a market-wide ranking, and
    # there is no ranking when the pool is a single fund the user chose.
    # period_calc exposes bare column names; the `filtered` CTE is aliased f.
    idcw_excl = "" if scheme_code else idcw_exclusion_clause(option_type, alias="")
    idcw_excl_f = "" if scheme_code else idcw_exclusion_clause(option_type, alias="f")

    try:
        # Counted over the same window as every other KPI below, and as the table the user
        # is looking at. Without this the headline spanned all of history -- 25,377 schemes
        # against the 8,864 actually listed, because ~16,600 of them stopped publishing
        # years ago (merged or wound up) and only the table excluded them.
        overview_where, overview_params = where_sql, list(params)
        if start_date:
            overview_where = f"{where_sql} AND s.latest_date >= %s" if where_sql else "WHERE s.latest_date >= %s"
            overview_params = list(params) + [start_date]
        sql_overview = f"""
            SELECT
                count(*) as total_schemes,
                MAX(latest_date) as latest_date
            FROM summary_table s
            {overview_where};
        """
        row = con.execute(sql_overview, overview_params).fetchone()
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
                    SELECT s.scheme_name, s.plan_type, s.option_type, s.scheme_code,
                           s.broad_category, s.category,
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
                        COUNT(CASE WHEN period_return_pct > 0 THEN 1 END) as advancers,
                        COUNT(CASE WHEN period_return_pct < 0 THEN 1 END) as decliners,
                        COUNT(CASE WHEN period_return_pct = 0 THEN 1 END) as unchanged,
                        ROUND((percentile_cont(0.5) WITHIN GROUP (ORDER BY period_return_pct))::numeric, 4)::double precision as median_return,
                        ROUND((AVG(period_return_pct))::numeric, 4)::double precision as avg_return
                    FROM period_calc
                    WHERE period_return_pct IS NOT NULL
                ),
                -- The pool the single-fund cards rank over: IDCW schemes are dropped here
                -- (see idcw_exclusion_clause) but deliberately kept in kpi_stats above, which
                -- measures how the whole market moved rather than naming one fund.
                rankable AS (
                    SELECT * FROM period_calc
                    WHERE period_return_pct IS NOT NULL{idcw_excl}
                ),
                -- The winner/laggard carry their plan, option and code so the caller can
                -- build the app-wide label. Selecting scheme_name alone put a bare base
                -- name on the KPI card -- ambiguous, since every plan/option variant of a
                -- fund shares it, and inconsistent with the Leaders tables beside it. They
                -- carry their own return too, so the number on the card is always the named
                -- fund's -- a MAX()/MIN() over the unfiltered pool would caption an excluded
                -- IDCW scheme's payout with the surviving fund's name.
                top_row AS (
                    SELECT scheme_name, plan_type, option_type, scheme_code, period_return_pct
                    FROM rankable ORDER BY period_return_pct DESC LIMIT 1
                ),
                lag_row AS (
                    SELECT scheme_name, plan_type, option_type, scheme_code, period_return_pct
                    FROM rankable ORDER BY period_return_pct ASC LIMIT 1
                ),
                best_cat AS (
                    SELECT category, ROUND((AVG(period_return_pct))::numeric, 4)::double precision as avg_cat_ret
                    FROM period_calc
                    WHERE category IS NOT NULL AND period_return_pct IS NOT NULL
                    GROUP BY category
                    ORDER BY avg_cat_ret DESC
                    LIMIT 1
                )
                SELECT k.*, b.category as best_cat_name, b.avg_cat_ret as best_cat_return,
                       t.scheme_name, t.plan_type, t.option_type, t.scheme_code, t.period_return_pct,
                       l.scheme_name, l.plan_type, l.option_type, l.scheme_code, l.period_return_pct
                FROM kpi_stats k
                LEFT JOIN best_cat b ON TRUE
                LEFT JOIN top_row t ON TRUE
                LEFT JOIN lag_row l ON TRUE;
            """
            all_p = [start_date, end_date, start_date, end_date] + params + [start_date]
            kpi_row = con.execute(sql_combined, all_p).fetchone()
            # k.* is: advancers, decliners, unchanged, median, avg; then best_cat_name and
            # best_cat_return; then top and lag, each as name/plan/option/code/return. The
            # single-fund cards are gated on their own return rather than on the breadth
            # counts: filtering to a lone IDCW scheme empties `rankable` while leaving
            # thousands of rows in kpi_stats.
            if kpi_row:
                advancers = kpi_row[0] or 0
                decliners = kpi_row[1] or 0
                unchanged = kpi_row[2] or 0
                median_return = kpi_row[3] or 0.0
                avg_return = kpi_row[4] or 0.0
                if kpi_row[5] is not None:
                    best_cat = {"name": kpi_row[5], "return_pct": kpi_row[6]}
                if kpi_row[11] is not None:
                    top_performer = {"name": format_scheme_display_name(kpi_row[7], kpi_row[8], kpi_row[9], kpi_row[10]),
                                     "return_pct": kpi_row[11]}
                if kpi_row[16] is not None:
                    lag_performer = {"name": format_scheme_display_name(kpi_row[12], kpi_row[13], kpi_row[14], kpi_row[15]),
                                     "return_pct": kpi_row[16]}
        else:
            sql_summary_kpi = f"""
                WITH filtered AS MATERIALIZED (
                    SELECT s.scheme_name, s.plan_type, s.option_type, s.scheme_code, s.return_30d_pct
                    FROM summary_table s
                    {where_sql} {'AND' if where_sql else 'WHERE'} s.return_30d_pct IS NOT NULL
                ),
                -- Same split as the windowed branch above: the named-fund cards rank over
                -- the IDCW-free pool, the breadth counts still cover every scheme.
                rankable AS (
                    SELECT * FROM filtered f WHERE TRUE{idcw_excl_f}
                )
                SELECT
                    (SELECT to_jsonb(r) FROM rankable r ORDER BY r.return_30d_pct DESC LIMIT 1) as top_row,
                    (SELECT MAX(return_30d_pct) FROM rankable) as top_return,
                    (SELECT to_jsonb(r) FROM rankable r ORDER BY r.return_30d_pct ASC LIMIT 1) as lag_row,
                    (SELECT MIN(return_30d_pct) FROM rankable) as lag_return,
                    COUNT(CASE WHEN return_30d_pct > 0 THEN 1 END) as advancers,
                    COUNT(CASE WHEN return_30d_pct < 0 THEN 1 END) as decliners,
                    COUNT(CASE WHEN return_30d_pct = 0 THEN 1 END) as unchanged,
                    ROUND((percentile_cont(0.5) WITHIN GROUP (ORDER BY return_30d_pct))::numeric, 4)::double precision as median_return,
                    ROUND((AVG(return_30d_pct))::numeric, 4)::double precision as avg_return
                FROM filtered;
            """
            kpi_row = con.execute(sql_summary_kpi, params).fetchone()
            if kpi_row:
                def _label(row: Optional[Dict[str, Any]]) -> Optional[str]:
                    if not row:
                        return None
                    return format_scheme_display_name(row.get("scheme_name"), row.get("plan_type"),
                                                      row.get("option_type"), row.get("scheme_code"))

                if kpi_row[0] is not None:
                    top_performer = {"name": _label(kpi_row[0]), "return_pct": kpi_row[1]}
                if kpi_row[2] is not None:
                    lag_performer = {"name": _label(kpi_row[2]), "return_pct": kpi_row[3]}
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

    for column, value, head in (("s.plan_type", plan_type, "All Plans"), ("s.option_type", option_type, "All Options")):
        if not value or value == head:
            continue
        if value == UNSPECIFIED_FILTER:
            # NULL never satisfies `= 'Unspecified'`, so this needs its own predicate --
            # and it must also catch the legacy "Other" spelling still held by schemes
            # that no longer re-sync.
            clauses.append(f"({column} IS NULL OR {column} = ANY(%s))")
            params.append(list(_UNKNOWN_OPTION_VALUES))
        else:
            clauses.append(f"{column} = %s")
            params.append(value)

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
    # The app-wide label, alongside the raw name the callers already sort and search on --
    # the results table showed the bare base name, which every plan/option variant shares.
    if not df.empty and "scheme_name" in df.columns:
        df["display_name"] = [
            format_scheme_display_name(n, p, o, c)
            for n, p, o, c in zip(df["scheme_name"], df.get("plan_type", ""), df.get("option_type", ""), df["scheme_code"])
        ]
    return df

get_screener_data = get_screener_dataframe

def format_scheme_display_name(name: str, plan: str = "", option: str = "", code: Any = "") -> str:
    """The app-wide fund label: "Name (Plan - Option) [AMFI code]".

    Callers hand this values straight out of pandas, where a NULL plan arrives as float
    NaN -- which is truthy, so `(plan or "").strip()` raised AttributeError rather than
    treating it as absent. Since AMFI leaves the plan blank for ~40% of its rows, that is
    the common case, not an edge one."""
    def text(value: Any) -> str:
        return value.strip() if isinstance(value, str) else ""

    name = text(name)
    plan = text(plan)
    option = text(option)
    code_str = str(code).strip() if code is not None and code == code and code != "" else ""

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

#: A fund must be priced within this many calendar days of the window's first and last NAV
#: date to be ranked. Long enough for a weekend plus a market holiday (Diwali, Holi), short
#: enough that a fund launched a week into a 30-day window does not compete on 23 days.
LEADERS_EDGE_DAYS = 5
#: Fewer peers than this and "the category median" is one or two other funds: an alpha or a
#: quartile against them is noise, so neither is reported.
MIN_PEERS_FOR_ALPHA = 5


def recent_days_for(start_date: datetime.date, end_date: datetime.date) -> int:
    """The "recent" stretch rotation looks at: the last third of the window, at least a week
    (a 90-day window -> its last 30 days; a year -> its last 122)."""
    return max(7, round((end_date - start_date).days / 3))


def _vol_start_for(vol_lookback: str, start_date: datetime.date, end_date: datetime.date) -> datetime.date:
    if vol_lookback in ("6M", "6m", "180d", "180"):
        return max(start_date, end_date - datetime.timedelta(days=180))
    if vol_lookback in ("3Y", "3y", "1095d", "1095"):
        return max(start_date, end_date - datetime.timedelta(days=1095))
    if vol_lookback in ("all", "full", "Full Window", "Full"):
        return start_date
    return max(start_date, end_date - datetime.timedelta(days=365))  # default "1Y"


@cached(ttl=600)
def _leaders_universe(start_date: datetime.date, end_date: datetime.date, vol_lookback: str) -> pd.DataFrame:
    """Every scheme alive in the window, with its window return, volatility, drawdown and the
    flags that decide whether it may be RANKED. Unfiltered on purpose: peer medians are a
    property of the market, not of whatever the page is filtered to, so they are computed
    over this and the page's filters are applied afterwards (get_advanced_leaders_dataframe)."""
    from app.classification import classify, is_idcw, sebi_category

    vol_start = _vol_start_for(vol_lookback, start_date, end_date)
    recent_start = end_date - datetime.timedelta(days=recent_days_for(start_date, end_date))
    con = get_connection()
    try:
        _configure_parallel_planner(con)
        df = _fetchdf(con.execute("""
            WITH universe AS MATERIALIZED (
                SELECT s.scheme_code FROM summary_table s WHERE s.latest_date >= %(start)s
            ),
            navs AS (
                SELECT nh.scheme_code, nh.nav_date, nh.nav,
                       LAG(nh.nav) OVER w AS prev_nav,
                       MAX(nh.nav) OVER (w ROWS UNBOUNDED PRECEDING) AS run_max
                FROM nav_history nh JOIN universe u ON u.scheme_code = nh.scheme_code
                WHERE nh.nav_date >= %(start)s AND nh.nav_date <= %(end)s AND nh.nav > 0
                WINDOW w AS (PARTITION BY nh.scheme_code ORDER BY nh.nav_date)
            ),
            per AS (
                SELECT scheme_code,
                       min(nav_date) AS start_nav_date,
                       max(nav_date) AS end_nav_date,
                       (array_agg(nav ORDER BY nav_date ASC))[1] AS start_nav,
                       (array_agg(nav ORDER BY nav_date DESC))[1] AS end_nav,
                       (array_agg(nav ORDER BY nav_date ASC) FILTER (WHERE nav_date >= %(recent)s))[1] AS recent_nav,
                       min(nav / run_max - 1.0) AS max_dd,
                       stddev_samp(nav / prev_nav - 1.0) FILTER (WHERE nav_date >= %(vol_start)s AND prev_nav > 0) AS sd,
                       count(*) FILTER (WHERE nav_date >= %(vol_start)s AND prev_nav > 0) AS n_ret,
                       count(*) FILTER (WHERE nav_date >= %(vol_start)s AND prev_nav > 0 AND nav > prev_nav) AS up_days,
                       min(nav_date) FILTER (WHERE nav_date >= %(vol_start)s AND prev_nav > 0) AS vol_first,
                       max(nav_date) FILTER (WHERE nav_date >= %(vol_start)s AND prev_nav > 0) AS vol_last
                FROM navs
                GROUP BY scheme_code
            )
            SELECT s.scheme_code, s.scheme_name, s.fund_house, s.category, s.broad_category,
                   s.plan_type, s.option_type, s.expense_ratio, s.latest_nav,
                   s.dist_from_52w_high_pct, s.high_52w, s.low_52w,
                   p.start_nav_date, p.end_nav_date, p.start_nav, p.end_nav, p.recent_nav, p.max_dd,
                   p.sd, p.n_ret, p.up_days, p.vol_first, p.vol_last
            FROM per p JOIN summary_table s ON s.scheme_code = p.scheme_code
        """, {"start": start_date, "end": end_date, "vol_start": vol_start, "recent": recent_start}))
    finally:
        con.close()
    if df.empty:
        return df

    df["period_return_pct"] = ((df["end_nav"] / df["start_nav"] - 1.0) * 100.0).round(4)
    # The window's closing stretch (see recent_days_for): what category rotation compares
    # against the whole window to tell a category gaining leadership from one losing it.
    df["recent_return_pct"] = ((df["end_nav"] / df["recent_nav"] - 1.0) * 100.0).round(4)
    # Annualised at the rate this scheme actually publishes. sqrt(252) assumes trading
    # days; a liquid fund prices every calendar day, and scaling its 365 daily moves as if
    # there were 252 understated its volatility by a sixth.
    span_days = (pd.to_datetime(df["vol_last"]) - pd.to_datetime(df["vol_first"])).dt.days
    obs_per_year = (df["n_ret"] - 1).clip(lower=1) / (span_days / 365.25)
    obs_per_year = obs_per_year.where(span_days >= 7).clip(lower=12, upper=366)
    df["obs_per_year"] = obs_per_year.round(1)
    df["annualized_vol_pct"] = (df["sd"] * np.sqrt(obs_per_year) * 100.0).round(4)
    df["n_trading_days"] = df["n_ret"].fillna(0).astype(int)
    df["win_rate_pct"] = (df["up_days"] * 100.0 / df["n_ret"].where(df["n_ret"] > 0)).round(2)
    df["max_drawdown_pct"] = (df["max_dd"] * 100.0).round(4)

    names, plans, options, codes = (df["scheme_name"].tolist(), df["plan_type"].tolist(),
                                    df["option_type"].tolist(), df["scheme_code"].tolist())
    df["display_name"] = [format_scheme_display_name(n, p, o, c) for n, p, o, c in zip(names, plans, options, codes)]
    df["asset_class"] = [classify(c, b, n) for c, b, n in zip(df["category"], df["broad_category"], names)]
    df["peer_category"] = [sebi_category(c) for c in df["category"]]
    df["is_idcw"] = [is_idcw(o, n) for o, n in zip(options, names)]

    # Ranked only over the whole window: a fund priced from its launch a week in, or one that
    # stopped publishing before the end, would otherwise compete on a shorter period.
    first_common = pd.to_datetime(df["start_nav_date"]).min()
    last_common = pd.to_datetime(df["end_nav_date"]).max()
    edge = pd.Timedelta(days=LEADERS_EDGE_DAYS)
    df["full_window"] = ((pd.to_datetime(df["start_nav_date"]) <= first_common + edge)
                         & (pd.to_datetime(df["end_nav_date"]) >= last_common - edge))
    return df.drop(columns=["end_nav", "recent_nav", "max_dd", "sd", "up_days", "vol_first", "vol_last"])


def _plan_key(plan: Any) -> str:
    if not isinstance(plan, str) or not plan.strip() or plan.strip() in _UNKNOWN_OPTION_VALUES:
        return UNSPECIFIED_FILTER
    return plan.strip()


def _matches_filter(series: pd.Series, value: Optional[str], head: str) -> pd.Series:
    if not value or value == head:
        return pd.Series(True, index=series.index)
    if value == UNSPECIFIED_FILTER:
        return series.isna() | series.isin(list(_UNKNOWN_OPTION_VALUES))
    return series == value


def get_advanced_leaders_dataframe(
    broad_cat="All Categories",
    sub_cat="All Sub-Categories",
    plan_type="All Plans",
    option_type="All Options",
    start_date=None,
    end_date=None,
    vol_lookback="1Y"
) -> pd.DataFrame:
    """Funds ranked against their peers over the window: return, the peer median, category
    alpha (the gap to it), rank and quartile among peers, volatility, drawdown, win rate.

    What makes a fair ranking, and what this used to get wrong:
      * Peers are the same asset class, the same SEBI category (AMFI's legacy label folded
        in, see classification.sebi_category) and the same PLAN. Direct is compared with
        Direct: a Regular plan trails its own Direct twin by the distributor commission,
        which is a cost, not skill. "Index Funds" is split by asset class, because a Nifty 50
        fund and a G-sec index fund share that SEBI category and nothing else.
      * IDCW plans are left out (unless IDCW is what the filter asks for): a payout drops the
        NAV by exactly what it pays, so on NAV it reads as a loss and ranked them as laggards.
      * Only funds priced across the whole window are ranked.
      * Peer medians come from the whole market, so the Asset-class or category filter
        changes which funds are LISTED, never their alpha.
    The frame carries `attrs["exclusions"]` counting what was left out and why."""
    start_date, end_date = _normalize_date_range(start_date, end_date)
    if not start_date or not end_date:
        # No window: nothing to rank on. The page always sends one.
        return pd.DataFrame()

    uni = _leaders_universe(start_date, end_date, str(vol_lookback or "1Y"))
    if uni.empty:
        return uni

    want_idcw = str(option_type or "").strip().upper() == "IDCW"
    usable = uni["period_return_pct"].notna()
    ranked_pool = uni[usable & uni["full_window"] & (uni["is_idcw"] == want_idcw)].copy()
    exclusions = {
        "partial_window": int((usable & ~uni["full_window"]).sum()),
        "idcw": int((usable & uni["full_window"] & (uni["is_idcw"] != want_idcw)).sum()),
    }

    df = ranked_pool
    df["plan_key"] = [_plan_key(p) for p in df["plan_type"]]
    group = df.groupby(["asset_class", "peer_category", "plan_key"], dropna=False)["period_return_pct"]
    df["peer_count"] = group.transform("count").astype(int)
    df["cat_median_return"] = group.transform("median").round(4)
    df["peer_rank"] = group.rank(ascending=False, method="min").astype(int)
    thin = df["peer_count"] < MIN_PEERS_FOR_ALPHA
    df["cat_alpha_pct"] = (df["period_return_pct"] - df["cat_median_return"]).round(4).where(~thin)
    df.loc[thin, "cat_median_return"] = np.nan
    # 1 = top quarter. From the rank, so ties share a quartile instead of being split by row order.
    df["quartile"] = np.ceil(df["peer_rank"] * 4.0 / df["peer_count"]).clip(1, 4).where(~thin)
    # Share of peers this fund beat: 100 = best of its group.
    df["peer_percentile"] = (100.0 * (df["peer_count"] - df["peer_rank"]) / (df["peer_count"] - 1).clip(lower=1)).round(1).where(~thin)
    # Risk judged the same way as return: against the fund's own peers. A liquid fund at 0.5%
    # volatility is not "calm" next to a small-cap fund at 18% -- it is calm or not next to
    # other liquid funds. Compared across the whole market, volatility just sorted funds by
    # asset class (241 of 242 liquid funds came out "low risk").
    vol_group = df.groupby(["asset_class", "peer_category", "plan_key"], dropna=False)["annualized_vol_pct"]
    df["peer_median_vol"] = vol_group.transform("median").where(~thin).round(4)
    df["vol_vs_peers_pct"] = ((df["annualized_vol_pct"] / df["peer_median_vol"].where(df["peer_median_vol"] > 0) - 1.0)
                              * 100.0).round(2)
    df["return_to_risk"] = np.where(df["annualized_vol_pct"] > 0.01,
                                    (df["period_return_pct"] / df["annualized_vol_pct"]).round(4), np.nan)

    # The page's filters, applied after the medians so they never move them. The asset-class
    # filter takes the classes this page shows; the older SEBI broad buckets still work.
    mask = pd.Series(True, index=df.index)
    if broad_cat and broad_cat != "All Categories":
        mask &= (df["asset_class"] == broad_cat) | (df["broad_category"] == broad_cat)
    if sub_cat and sub_cat != "All Sub-Categories":
        mask &= (df["peer_category"] == sub_cat) | (df["category"] == sub_cat)
    mask &= _matches_filter(df["plan_type"], plan_type, "All Plans")
    mask &= _matches_filter(df["option_type"], option_type, "All Options") | (want_idcw & df["is_idcw"])
    out = df[mask].drop(columns=["plan_key"]).reset_index(drop=True)
    out.attrs["exclusions"] = exclusions
    # The filter choices, from the whole ranked pool -- taken from the filtered rows they
    # would shrink to whatever is already selected.
    pairs = df[["asset_class", "peer_category"]].dropna().drop_duplicates()
    out.attrs["categories_by_class"] = {cls: sorted(g["peer_category"]) for cls, g in pairs.groupby("asset_class")}
    out.attrs["window"] = {
        "first_nav_date": uni["start_nav_date"].min(), "last_nav_date": uni["end_nav_date"].max(),
        "vol_start": _vol_start_for(str(vol_lookback or "1Y"), start_date, end_date),
    }
    return out

get_leaders_laggards = get_advanced_leaders_dataframe


@cached(ttl=600)
def get_official_returns_by_scheme() -> pd.DataFrame:
    """Per scheme: its fund's SEBI benchmark and AMFI's own 1Y/3Y returns for the scheme's
    plan and for that benchmark, from amfi_fund_snapshot. Only for Growth-type schemes --
    AMFI computes these on the Growth NAV, and pinning them on an IDCW scheme would state a
    return it never delivered. Empty (not an error) before the first snapshot exists."""
    con = get_connection()
    try:
        df = _fetchdf(con.execute("""
            SELECT code AS scheme_code, f.benchmark, f.as_of AS official_as_of,
                   CASE WHEN s.plan_type = 'Direct' THEN f.return_1y_direct
                        WHEN s.plan_type = 'Regular' THEN f.return_1y_regular END AS official_1y_pct,
                   f.return_1y_benchmark AS benchmark_1y_pct,
                   CASE WHEN s.plan_type = 'Direct' THEN f.return_3y_direct
                        WHEN s.plan_type = 'Regular' THEN f.return_3y_regular END AS official_3y_pct,
                   f.return_3y_benchmark AS benchmark_3y_pct
            FROM amfi_fund_snapshot f
            CROSS JOIN LATERAL unnest(f.scheme_codes) AS code
            JOIN schemes s ON s.scheme_code = code
            WHERE (s.option_type IS NULL OR s.option_type <> 'IDCW')
        """))
    except Exception:
        return pd.DataFrame(columns=["scheme_code", "benchmark", "official_as_of", "official_1y_pct",
                                     "benchmark_1y_pct", "official_3y_pct", "benchmark_3y_pct"])
    finally:
        con.close()
    if df.empty:
        return df
    df["excess_1y_pp"] = (df["official_1y_pct"] - df["benchmark_1y_pct"]).round(4)
    df["excess_3y_pp"] = (df["official_3y_pct"] - df["benchmark_3y_pct"]).round(4)
    return df.drop_duplicates("scheme_code")


ROTATION_LEADING, ROTATION_WEAKENING, ROTATION_LAGGING, ROTATION_IMPROVING = "Leading", "Weakening", "Lagging", "Improving"
#: Categories with fewer distinct funds than this are not shown: one or two funds are a fund
#: story, not a category one.
MIN_FUNDS_FOR_ROTATION = 3


def _rotation_quadrant(strength: float, momentum: float) -> str:
    if strength >= 0:
        return ROTATION_LEADING if momentum >= 0 else ROTATION_WEAKENING
    return ROTATION_IMPROVING if momentum >= 0 else ROTATION_LAGGING


def _one_row_per_fund(df: pd.DataFrame) -> pd.DataFrame:
    """A fund's Direct and Regular plans are one portfolio: counted twice, a category with
    ten funds reads as twenty and every median weights each fund double. Keeps the Direct
    plan where a fund has one."""
    key = df["scheme_name"].fillna("").str.strip().str.lower() + "||" + df["option_type"].fillna("").str.lower()
    direct_first = df.assign(_k=key, _d=(df["plan_type"] != "Direct").astype(int)).sort_values(["_k", "_d"])
    return direct_first.drop_duplicates("_k").drop(columns=["_k", "_d"])


@cached(ttl=600)
def get_category_rotation(start_date=None, end_date=None, plan_type: str = "All Plans", option_type: str = "All Options",
                          broad_cat: str = "All Categories") -> Dict[str, Any]:
    """Which categories are gaining or losing leadership, relative-rotation style.

    For each (asset class, SEBI category), over the same fair pool the Leaders tab ranks:
      * strength  = the category's median return over the window minus the market's median;
      * momentum  = the same over the window's closing stretch (its last third, see
                    recent_days_for) -- is the category ahead of the market lately?
    Leading (strong, still ahead lately), Weakening (strong, but behind lately), Lagging,
    Improving (weak over the window, ahead lately). Plus the category's median return in each
    week (month, for windows over four months) -- the rotation itself, period by period.

    One row per fund (Direct plan kept) so a fund's two plans are not counted as two funds.
    The market medians are always the whole market's; `broad_cat` only narrows which
    categories are listed."""
    start_date, end_date = _normalize_date_range(start_date, end_date)
    empty: Dict[str, Any] = {"categories": [], "periods": [], "market_period_median": [], "unit": None,
                             "recent_days": None, "market_median": None, "market_recent_median": None}
    if not start_date or not end_date:
        return empty
    df = get_advanced_leaders_dataframe(plan_type=plan_type, option_type=option_type,
                                        start_date=start_date, end_date=end_date)
    if df.empty:
        return empty
    if not plan_type or plan_type == "All Plans":
        df = _one_row_per_fund(df)
    market_med = float(df["period_return_pct"].median())
    market_recent = float(df["recent_return_pct"].median())
    if broad_cat and broad_cat != "All Categories":
        df = df[(df["asset_class"] == broad_cat) | (df["broad_category"] == broad_cat)]
    if df.empty:
        return {**empty, "market_median": market_med, "market_recent_median": market_recent}

    # --- Period-by-period returns ---------------------------------------------------------
    span = (end_date - start_date).days
    unit = "month" if span > 120 else "week"
    codes = [int(c) for c in df["scheme_code"]]
    con = get_connection()
    try:
        closes = _fetchdf(con.execute("""
            SELECT scheme_code, date_trunc(%s, nav_date)::date AS bucket,
                   (array_agg(nav ORDER BY nav_date DESC))[1] AS close
            FROM nav_history
            WHERE scheme_code = ANY(%s) AND nav_date >= %s AND nav_date <= %s AND nav > 0
            GROUP BY 1, 2
        """, (unit, codes, start_date, end_date)))
    finally:
        con.close()
    periods: List[datetime.date] = []
    per_bucket = pd.DataFrame(columns=["scheme_code", "bucket", "ret"])
    if not closes.empty:
        closes = closes.sort_values(["scheme_code", "bucket"])
        start_nav = df.set_index("scheme_code")["start_nav"]
        prev = closes.groupby("scheme_code")["close"].shift(1)
        # The first period runs from the window's opening NAV, not from nothing.
        prev = prev.fillna(closes["scheme_code"].map(start_nav))
        closes["ret"] = (closes["close"] / prev - 1.0) * 100.0
        per_bucket = closes[["scheme_code", "bucket", "ret"]]
        periods = sorted(per_bucket["bucket"].unique())
    tagged = per_bucket.merge(df[["scheme_code", "asset_class", "peer_category"]], on="scheme_code")
    heat = tagged.groupby(["asset_class", "peer_category", "bucket"])["ret"].median() if not tagged.empty else pd.Series(dtype=float)
    market_by_period = per_bucket.groupby("bucket")["ret"].median() if not per_bucket.empty else pd.Series(dtype=float)

    aum = _fund_aum()
    aum_by = aum.groupby(["asset_class", "peer_category"])["aum_cr"].sum() if not aum.empty else pd.Series(dtype=float)

    def r4(v: Any) -> Optional[float]:
        return None if v is None or pd.isna(v) else round(float(v), 4)

    rows = []
    for (cls, cat), part in df.groupby(["asset_class", "peer_category"]):
        if len(part) < MIN_FUNDS_FOR_ROTATION:
            continue
        med = float(part["period_return_pct"].median())
        recent = part["recent_return_pct"].dropna()
        recent_med = float(recent.median()) if len(recent) else None
        strength = med - market_med
        momentum = (recent_med - market_recent) if recent_med is not None else None
        ter = part["expense_ratio"].dropna()
        cat_aum = aum_by.get((cls, cat)) if len(aum_by) else None
        rows.append({
            "asset_class": cls, "category": cat, "funds": int(len(part)),
            "aum_cr": r4(cat_aum) if cat_aum else None,
            "median_return": r4(med), "recent_median_return": r4(recent_med),
            "strength_pp": r4(strength), "momentum_pp": r4(momentum),
            "quadrant": _rotation_quadrant(strength, momentum) if momentum is not None else None,
            "p25_return": r4(part["period_return_pct"].quantile(0.25)),
            "p75_return": r4(part["period_return_pct"].quantile(0.75)),
            "pct_up": r4((part["period_return_pct"] > 0).mean() * 100.0),
            "median_vol": r4(part["annualized_vol_pct"].median()),
            "median_drawdown": r4(part["max_drawdown_pct"].median()),
            "avg_ter": r4(ter.mean()) if len(ter) else None,
            "heat": [r4(heat.get((cls, cat, p))) for p in periods],
        })
    rows.sort(key=lambda r: -(r["median_return"] or 0))
    return {
        "categories": rows,
        "periods": [p.isoformat() for p in periods],
        "market_period_median": [r4(market_by_period.get(p)) for p in periods],
        "unit": unit,
        "recent_days": recent_days_for(start_date, end_date),
        "market_median": r4(market_med),
        "market_recent_median": r4(market_recent),
    }

@cached(ttl=600)
def get_scheme_profile(scheme_code: int) -> Tuple[Optional[Dict[str, Any]], pd.DataFrame]:
    con = get_connection()
    summary = _fetchdf(con.execute("SELECT * FROM summary_table WHERE scheme_code = %s", [scheme_code]))
    if summary.empty:
        summary = _fetchdf(con.execute("SELECT * FROM schemes WHERE scheme_code = %s", [scheme_code]))
    history = _fetchdf(con.execute("SELECT nav_date, nav FROM nav_history WHERE scheme_code = %s ORDER BY nav_date DESC", [scheme_code]))
    con.close()

    profile = summary.to_dict(orient="records")[0] if not summary.empty else None
    if profile is not None:
        # The sibling get_scheme_profile_only() has always carried this; this one did not,
        # which is why the Quant page header, its factor and stress chart titles, and every
        # Compare table and legend showed a bare base name.
        profile["display_name"] = format_scheme_display_name(
            profile.get("scheme_name"), profile.get("plan_type"),
            profile.get("option_type"), profile.get("scheme_code"))
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
    'legacy_unverified' (leftover unverified name-match rows still stored on
    schemes) vs. 'unknown' TER record.
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
    only populated once amfi_sync.sync_official_ter has matched that scheme at least once.

    One row per change, not per day: `ter_date` is when these figures took effect and
    `valid_to` the last date they were disclosed. A consumer plotting this must draw steps,
    since joining the points with straight lines would invent a gradual slide between two
    fee levels that actually jumped."""
    con = get_connection()
    df = _fetchdf(con.execute(
        """
        SELECT ter_date, valid_to, base_expense_ratio_pct, brokerage_cost_pct,
               transaction_cost_pct, statutory_levies_pct, total_ter_pct, source_url
        FROM ter_history
        WHERE scheme_code = %s
        ORDER BY ter_date ASC;
        """,
        [scheme_code],
    ))
    con.close()
    return df


def upsert_ter_history(records: pd.DataFrame, authoritative: bool = False) -> int:
    """Serialized via WRITE_LOCK â€” see refresh_summary_table()."""
    with WRITE_LOCK:
        return _upsert_ter_history_impl(records, authoritative=authoritative)


#: Columns whose combined value defines "the TER changed".
_TER_VALUE_COLS = ("base_expense_ratio_pct", "brokerage_cost_pct", "transaction_cost_pct",
                   "statutory_levies_pct", "total_ter_pct")


def _upsert_ter_history_impl(records: pd.DataFrame, authoritative: bool = False) -> int:
    """Merges a batch of daily AMFI disclosures into the change-point table.

    `records` arrives dense -- one row per scheme per calendar day, exactly as the portal
    publishes it. What gets stored is one row per *change*, so this cannot be the old
    delete-by-key-then-insert: a day landing inside an existing run has to split it, and a
    day matching its neighbours has to disappear into them.

    The merge is defined over *days* rather than over the stored rows, which is what keeps it
    order-independent. Runs in the affected window are expanded back to the days they assert,
    the incoming batch contributes its own days, the batch wins wherever both describe the
    same date, and runs are recomputed from that union. Re-running the same batch therefore
    lands on exactly the same table, and the historical backfill can keep walking months
    backwards into data that already exists -- which the resume/checkpoint logic depends on.

    `authoritative` says the batch is a complete fetch of the window it covers, so what is
    already stored for those days should be discarded rather than merged. The caller sets it
    only when AMFI returned every page, because the merge is otherwise what protects days a
    dropped page failed to re-deliver. It is what makes a deliberate re-fetch able to *undo*
    bad data: a merge can add days and correct figures, but it can never remove a day that
    should not be there, since the stored value simply survives as an observation."""
    if records.empty:
        return 0
    value_cols = ", ".join(_TER_VALUE_COLS)
    con = get_connection()
    try:
        con.begin()
        _write_staging_table(con, "stg_ter_history", records,
                             like_table="ter_history",
                             key_columns=["scheme_code", "ter_date"])
        # Only the window the batch can affect is rebuilt: the runs it overlaps, plus any
        # immediately abutting it so an unchanged figure either side of the boundary still
        # coalesces into one run. Runs elsewhere in the scheme's history are left alone, so
        # the cost of a monthly sync does not grow with the depth of history behind it.
        con.execute("""
            CREATE TEMP TABLE stg_ter_window AS
            SELECT scheme_code, min(ter_date) AS lo, max(ter_date) AS hi
            FROM stg_ter_history GROUP BY scheme_code;
        """)
        # Expanded back to one row per day, which is what makes the merge exact: a stored
        # run is an assertion about every day it spans, and re-deriving runs from the days
        # (rather than from the two endpoints) is what lets an incoming day split a run it
        # lands inside, or extend one it abuts.
        # An authoritative batch keeps only the days outside its own span, so that days the
        # batch does not claim are dropped rather than resurrected. Days either side of the
        # span are still carried in, or a re-fetch of one month would sever the periods
        # running up to and away from it.
        carry_in = "AND (gs::date < w.lo OR gs::date > w.hi)" if authoritative else ""
        con.execute(f"""
            CREATE TEMP TABLE stg_ter_days AS
            SELECT DISTINCT ON (scheme_code, obs_date) scheme_code, obs_date, {value_cols}, source_url
            FROM (
                SELECT s.scheme_code, s.ter_date AS obs_date, {value_cols}, s.source_url, 0 AS src_rank
                FROM stg_ter_history s
                UNION ALL
                SELECT h.scheme_code, gs::date AS obs_date, {value_cols}, h.source_url, 1 AS src_rank
                FROM ter_history h
                JOIN stg_ter_window w USING (scheme_code)
                CROSS JOIN LATERAL generate_series(h.ter_date,
                                                   COALESCE(h.valid_to, h.ter_date),
                                                   interval '1 day') gs
                WHERE h.ter_date <= w.hi + 1
                  AND COALESCE(h.valid_to, h.ter_date) >= w.lo - 1
                  {carry_in}
            ) u
            ORDER BY scheme_code, obs_date, src_rank;
        """)
        con.execute(f"""
            CREATE TEMP TABLE stg_ter_runs AS
            WITH marked AS (
                SELECT *,
                       CASE WHEN ({value_cols}) IS DISTINCT FROM
                                 LAG(({value_cols})) OVER (PARTITION BY scheme_code ORDER BY obs_date)
                              OR LAG(obs_date) OVER (PARTITION BY scheme_code ORDER BY obs_date)
                                 IS DISTINCT FROM obs_date - 1
                            THEN 1 ELSE 0 END AS starts_run
                FROM stg_ter_days
            ), grouped AS (
                SELECT *, SUM(starts_run) OVER (PARTITION BY scheme_code ORDER BY obs_date
                                                ROWS UNBOUNDED PRECEDING) AS run_id
                FROM marked
            )
            SELECT scheme_code,
                   min(obs_date) AS ter_date,
                   max(obs_date) AS valid_to,
                   min(base_expense_ratio_pct) AS base_expense_ratio_pct,
                   min(brokerage_cost_pct)     AS brokerage_cost_pct,
                   min(transaction_cost_pct)   AS transaction_cost_pct,
                   min(statutory_levies_pct)   AS statutory_levies_pct,
                   min(total_ter_pct)          AS total_ter_pct,
                   min(source_url)             AS source_url
            FROM grouped
            GROUP BY scheme_code, run_id;
        """)
        con.execute("""
            DELETE FROM ter_history h
            USING stg_ter_window w
            WHERE h.scheme_code = w.scheme_code
              AND h.ter_date <= w.hi + 1
              AND COALESCE(h.valid_to, h.ter_date) >= w.lo - 1;
        """)
        written = con.execute("""
            INSERT INTO ter_history (
                scheme_code, ter_date, valid_to, base_expense_ratio_pct, brokerage_cost_pct,
                transaction_cost_pct, statutory_levies_pct, total_ter_pct, source_url
            )
            SELECT scheme_code, ter_date, valid_to, base_expense_ratio_pct, brokerage_cost_pct,
                   transaction_cost_pct, statutory_levies_pct, total_ter_pct, source_url
            FROM stg_ter_runs
            RETURNING 1;
        """).fetchall()
        con.execute("DROP TABLE IF EXISTS stg_ter_history")
        con.execute("DROP TABLE IF EXISTS stg_ter_window")
        con.execute("DROP TABLE IF EXISTS stg_ter_days")
        con.execute("DROP TABLE IF EXISTS stg_ter_runs")
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
    return len(written)


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
    # summary_table is built FROM schemes, so if the regression guard above blocked every
    # row there is nothing for a rebuild to pick up. That is the common case during a
    # historical backfill: it walks backwards from the current month, so each month it
    # fetches carries an as-of date older than the one already stored and updates nothing
    # (the exception is a scheme with no official TER at all, which the first month that
    # names it fills in). Every one of those no-op months was still paying 7.7s to rebuild
    # a 25,377-scheme cache from 27M NAV rows to arrive at exactly the same table.
    if updated:
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
