"""Shared PostgreSQL connection pool, write-serialization, and change-version tracking.

Third database this app has used, so the lineage is worth stating once:

  DuckDB -> SQLite   Dropped because DuckDB refuses to even *open* a file while
                     another process holds it read-write, which collided badly
                     with having Streamlit and this backend as two independent
                     processes during the migration.
  SQLite -> Postgres (2026-09-12) Dropped for this workload's shape, not its
                     size. See docker-compose.yml's postgres service for the
                     measured numbers. The short version: an hour-long
                     continuous bulk ingest running underneath a UI that polls
                     several endpoints every 5s starves SQLite's WAL
                     checkpointer -- a checkpoint can only reclaim up to the
                     oldest snapshot any reader still holds, so it kept getting
                     SQLITE_BUSY and silently giving up. The WAL reached 547MB
                     against a 256MB database, and ingest throughput collapsed
                     121x (234,760 -> 1,929 rows/s) as reads and writes both
                     started paging through that WAL. Postgres is MVCC:
                     readers never block the checkpointer, and concurrent
                     ingest-plus-query is its ordinary operating mode.

What changed structurally, versus the SQLite version this replaced:

  * A real connection **pool** replaces one thread-local connection per thread.
    The thread-local scheme existed because SQLite connections can't be shared
    across concurrently-running threads; with a pool, a connection is checked
    out for the life of one caller and handed back, so a FastAPI page load that
    fans out to ~8 threadpool threads borrows 8 connections briefly instead of
    permanently owning 8. It also ends the memory problem that scheme caused:
    53 thread-local SQLite connections x a 64MB page cache each was most of the
    5.8GB RSS measured just before the switch. Postgres has ONE shared buffer
    pool, sized once in docker-compose.yml.
  * No PRAGMAs, no WAL tuning, no `synchronous` trade-off. Durability and
    checkpointing are the server's job now.
  * `MEDIAN`/`STDDEV`/`SQRT` are no longer custom Python aggregates registered
    per connection -- Postgres has `percentile_cont`, `stddev_samp` and `sqrt`
    natively, which are also far faster than a Python callback per row.
  * No date adapters/converters. Postgres has real DATE/TIMESTAMP types and
    psycopg returns `datetime.date`/`datetime.datetime` directly.

Deliberately kept: the `con = get_connection(); ...; con.close()` calling
convention, so db/queries.py's ~1,600 lines of call sites did not all have to
change shape; and WRITE_LOCK, which is no longer a database limitation but is
still useful application-level ordering (see its comment).
"""

import datetime
import logging
import threading
import time
from typing import Any, Optional, Sequence

import numpy as np
import pandas as pd
import psycopg
from psycopg import sql as pgsql
from psycopg_pool import ConnectionPool

from app.core.cache import clear_all_caches
from app.core.config import settings

logger = logging.getLogger(__name__)

DATABASE_URL = settings.database_url

# Pool sizing: FastAPI runs sync route handlers in anyio's threadpool (40 threads
# by default) and one page load can fan out to ~8 concurrent requests, so the
# pool needs real width -- but not 40, because Postgres's own default
# max_connections is 100 and a long-running backfill holds one of these for the
# duration of a chunk. 20 leaves headroom for psql/inspection alongside the app.
POOL_MIN_SIZE = 2
POOL_MAX_SIZE = 20
# Wait rather than fail if every connection is briefly busy; a request queueing
# for a moment beats surfacing "pool exhausted" to the UI.
POOL_TIMEOUT_SECONDS = 45.0

_POOL: Optional[ConnectionPool] = None
_POOL_LOCK = threading.Lock()

# Serializes every DB *write* path (daily sync, backfill chunks,
# summary-table rebuild) so the background sync daemon's scheduled runs can never
# race a manual "Sync Now" / "Recompute" click, or each other. Reentrant so a
# writer that itself calls refresh_summary_table() (which also takes this lock)
# doesn't deadlock on itself.
#
# Under SQLite this was load-bearing: SQLite allows exactly one writer. Under
# Postgres it is NOT required for correctness -- MVCC handles concurrent writers
# fine. It is kept on purpose for two reasons that still apply: it gives the
# app's own jobs a defined order (a summary-table rebuild sees a settled dataset
# rather than one mid-ingest), and it removes any chance of two bulk upserts
# touching the same rows in opposite orders and deadlocking, which Postgres would
# resolve by killing one of them.
WRITE_LOCK = threading.RLock()

_DATA_VERSION = 0
_DATA_VERSION_LOCK = threading.Lock()


def bump_data_version() -> int:
    """Marks the dataset as changed: bumps the version open UI sessions poll for
    (see /api/meta/status), and clears every cached query result app-wide so
    the next query re-reads the DB instead of serving pre-sync results."""
    global _DATA_VERSION
    with _DATA_VERSION_LOCK:
        _DATA_VERSION += 1
        version = _DATA_VERSION
    try:
        clear_all_caches()
    except Exception:
        pass
    return version


def get_data_version() -> int:
    with _DATA_VERSION_LOCK:
        return _DATA_VERSION


def get_pool() -> ConnectionPool:
    """The process-wide pool, opened on first use.

    `autocommit=True` on every pooled connection deliberately mirrors the old
    sqlite3 `isolation_level=None`: each statement stands alone unless a caller
    opens an explicit transaction with `con.begin()`. That keeps the semantics
    every existing call site was written against, rather than silently wrapping
    everything in psycopg's default implicit transaction -- which would leave a
    read-only request holding an idle-in-transaction snapshot open, the exact
    thing that starved SQLite's checkpointer and that also blocks Postgres's
    autovacuum from reclaiming dead rows."""
    global _POOL
    if _POOL is None:
        with _POOL_LOCK:
            if _POOL is None:
                _POOL = ConnectionPool(
                    DATABASE_URL,
                    min_size=POOL_MIN_SIZE,
                    max_size=POOL_MAX_SIZE,
                    timeout=POOL_TIMEOUT_SECONDS,
                    kwargs={
                        "autocommit": True,
                        "options": "-c statement_timeout=60000",
                    },
                    open=True,
                    name="mf_pool",
                )
                _POOL.wait(timeout=30.0)
                logger.info(
                    f"PostgreSQL pool opened (min={POOL_MIN_SIZE}, max={POOL_MAX_SIZE})."
                )
    return _POOL


def get_pool_stats() -> dict:
    try:
        stats = get_pool().get_stats()
        size = stats.get("pool_size") or stats.get("connections_num")
        avail = stats.get("pool_available")
        checked = None
        if size is not None and avail is not None:
            checked = int(size) - int(avail)
        return {"pool_checked_out": checked, "pool_size": size}
    except Exception:
        return {"pool_checked_out": None}


def reset_pool() -> None:
    """Closes the pool so the next get_connection() builds a new one. Used by
    tests when they repoint DATABASE_URL, and available as a manual recovery
    lever if the pool ever ends up wedged."""
    global _POOL
    with _POOL_LOCK:
        pool, _POOL = _POOL, None
    if pool is not None:
        try:
            pool.close()
        except Exception:
            pass


class PooledCursor:
    """A cursor that owns a pooled connection for its lifetime and returns it on
    close(). Exists to preserve the `con = get_connection(); con.execute(...);
    con.close()` convention every call site in db/queries.py and amfi_sync.py
    already used against sqlite3, so switching database engines did not mean
    rewriting ~1,600 lines of unrelated query code.

    Unknown attributes delegate to the real psycopg cursor, so `.rowcount`,
    `.description`, `.fetchmany()` and friends work untouched."""

    __slots__ = ("_conn", "_cur", "_closed")

    def __init__(self, pool: ConnectionPool):
        self._closed = False
        self._conn = pool.getconn()
        try:
            self._cur = self._conn.cursor()
        except Exception:
            pool.putconn(self._conn)
            self._closed = True
            raise

    # --- statement execution ---------------------------------------------

    def execute(self, query: Any, params: Optional[Sequence[Any]] = None) -> "PooledCursor":
        self._cur.execute(query, params)
        return self

    def executemany(self, query: Any, params_seq: Sequence[Sequence[Any]]) -> "PooledCursor":
        self._cur.executemany(query, params_seq)
        return self

    def copy(self, statement: Any):
        """psycopg's COPY context manager -- the bulk-load fast path. See db/bulk.py."""
        return self._cur.copy(statement)

    def fetchall(self):
        return self._cur.fetchall()

    def fetchone(self):
        return self._cur.fetchone()

    # --- explicit transactions -------------------------------------------
    # Named methods rather than `execute("BEGIN")` string literals: the SQLite
    # version of this code said "BEGIN IMMEDIATE", which is not valid Postgres,
    # and burying dialect in string literals at a dozen call sites is how that
    # kind of thing gets missed.

    def begin(self) -> None:
        if not self.in_transaction:
            self._cur.execute("BEGIN")

    def commit(self) -> None:
        if self.in_transaction:
            self._cur.execute("COMMIT")

    def rollback(self) -> None:
        if self.in_transaction:
            self._cur.execute("ROLLBACK")

    @property
    def in_transaction(self) -> bool:
        return self._conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE

    @property
    def raw_connection(self) -> psycopg.Connection:
        return self._conn

    # --- lifecycle --------------------------------------------------------

    def close(self) -> None:
        """Returns the connection to the pool. Idempotent, so the codebase's
        existing habit of calling close() on both the success and the exception
        path is safe."""
        if self._closed:
            return
        self._closed = True
        try:
            self._cur.close()
        except Exception:
            pass
        try:
            # Never hand a connection back mid-transaction: the next borrower
            # would inherit an open snapshot. psycopg's own pool resets state on
            # putconn, but rolling back here makes the intent explicit and keeps
            # a failed writer from leaving locks held a moment longer.
            if self._conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                self._conn.rollback()
        except Exception:
            pass
        try:
            get_pool().putconn(self._conn)
        except Exception:
            try:
                self._conn.close()
            except Exception:
                pass

    def __del__(self):
        # Safety net only. A call site that forgets close() would otherwise leak a
        # pooled connection permanently and, after 20 of them, hang every request
        # on pool timeout -- a failure that looks like a database problem and is
        # not one.
        try:
            if not self._closed:
                logger.warning("PooledCursor garbage-collected without close(); "
                               "returning its connection to the pool.")
                self.close()
        except Exception:
            pass

    def __enter__(self) -> "PooledCursor":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cur, name)


def get_connection() -> PooledCursor:
    """Checks out a pooled connection and returns a cursor-like handle on it.

    Contract note: callers still call `.close()` when done, exactly as they did
    with sqlite3 cursors -- but where that used to close only a cursor and leave
    the thread's connection open forever, it now *returns the connection to the
    pool*. So close() actually matters here; prefer try/finally (or use it as a
    context manager) rather than a bare close() at the end of a happy path."""
    return PooledCursor(get_pool())


# Errors worth retrying: another transaction held a conflicting lock, or the
# server aborted ours to break a deadlock/serialization conflict. All of these
# are transient by definition -- the same statement can simply be run again.
_RETRYABLE = (
    psycopg.errors.DeadlockDetected,
    psycopg.errors.SerializationFailure,
    psycopg.errors.LockNotAvailable,
)


def is_retryable(err: BaseException) -> bool:
    return isinstance(err, _RETRYABLE)


def _execute_with_conflict_retry(con, sql, attempts=3, delay_seconds=1.0):
    """Retries a DDL/DML statement a couple of times on a transient lock/deadlock
    error -- e.g. the background sync daemon's summary-table rebuild racing a
    concurrent read. A real (non-transient) error still raises immediately."""
    last_err = None
    for attempt in range(attempts):
        try:
            con.execute(sql)
            return
        except psycopg.Error as e:
            if not is_retryable(e):
                raise
            last_err = e
            if attempt < attempts - 1:
                time.sleep(delay_seconds)
    raise last_err


# --- Shared helpers --------------------------------------------------------
# Used by both db/queries.py and amfi_sync.py (both read result sets into
# DataFrames, and the smaller cost/TER merges still stage a DataFrame).


def fetchdf(cursor) -> pd.DataFrame:
    """DuckDB's cursor.fetchdf() equivalent, kept so call sites read the same."""
    cols = [d[0] for d in cursor.description] if cursor.description else []
    return pd.DataFrame(cursor.fetchall(), columns=cols)


def pyval(v: Any) -> Any:
    """Normalizes a pandas/numpy scalar to a type the driver's parameter binder
    accepts natively. psycopg, like sqlite3 before it, does not know numpy
    scalars -- an np.int64 raises "cannot adapt type" rather than silently
    working, so every DataFrame-sourced value goes through here."""
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        f = float(v)
        return None if f != f else f  # NaN != NaN
    if isinstance(v, pd.Timestamp):
        return v.date()
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, np.datetime64):
        ts = pd.Timestamp(v)
        return None if pd.isna(ts) else ts.date()
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v
    return v


def write_staging_table(
    con,
    table_name: str,
    df: pd.DataFrame,
    like_table: Optional[str] = None,
    key_columns: Optional[list] = None,
) -> None:
    """Loads a DataFrame into a session-scoped TEMP TABLE for the
    UPDATE...FROM / INSERT...SELECT / DELETE...USING patterns in db/queries.py.

    ``like_table`` mirrors the destination table's column types onto the staging
    table. Under SQLite that was critical for a subtle reason -- an untyped
    column has no type affinity, so SQLite refused to use an index on it for a
    join against a typed column, and every staging merge silently degraded to a
    full scan of the staging table per outer row (one such join ran 81 minutes
    without finishing). Postgres has no affinity concept and would reject a type
    mismatch outright instead of quietly mis-planning, so this is no longer a
    correctness trap -- but mirroring types is still the right thing: it keeps
    the join key comparisons index-eligible and avoids per-row casts.

    ``key_columns`` names the columns the caller joins on, and gets an index.
    Postgres will not have statistics for a table it just created, so the ANALYZE
    at the end matters more here than the index does: without it the planner
    assumes a default row count and can pick a nested loop over a hash join for
    a table that turns out to be 100x bigger than it guessed.

    The hot AMFI NAV/schemes ingest does not come through here -- see db/bulk.py,
    which COPYs into a temp table and upserts in one statement."""
    cols = list(df.columns)
    if not cols:
        raise ValueError(f"write_staging_table({table_name}): DataFrame has no columns")

    ident = pgsql.Identifier(table_name)
    con.execute(pgsql.SQL("DROP TABLE IF EXISTS {}").format(ident))

    dest_types: dict = {}
    if like_table:
        con.execute(
            """SELECT column_name, data_type FROM information_schema.columns
               WHERE table_name = %s""",
            (like_table,),
        )
        dest_types = {r[0]: r[1] for r in con.fetchall()}

    col_defs = pgsql.SQL(", ").join(
        pgsql.SQL("{} {}").format(
            pgsql.Identifier(c),
            pgsql.SQL(dest_types.get(c, "text")),
        )
        for c in cols
    )
    # UNLOGGED is not available for TEMP tables (they are already unlogged), so
    # there is nothing further to turn off here.
    con.execute(pgsql.SQL("CREATE TEMP TABLE {} ({})").format(ident, col_defs))

    rows = [tuple(pyval(v) for v in row) for row in df.itertuples(index=False, name=None)]
    if rows:
        copy_sql = pgsql.SQL("COPY {} ({}) FROM STDIN").format(
            ident, pgsql.SQL(", ").join(pgsql.Identifier(c) for c in cols)
        )
        with con.copy(copy_sql) as copy:
            for row in rows:
                copy.write_row(row)

    keys = [c for c in (key_columns or cols[:1]) if c in cols]
    if keys:
        con.execute(
            pgsql.SQL("CREATE INDEX ON {} ({})").format(
                ident, pgsql.SQL(", ").join(pgsql.Identifier(c) for c in keys)
            )
        )
    con.execute(pgsql.SQL("ANALYZE {}").format(ident))
