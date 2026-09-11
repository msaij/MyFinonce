"""Shared SQLite connection, write-serialization, and change-version tracking.

Replaces the original DuckDB-based connection module. DuckDB was dropped
after repeated file-locking failures during the migration itself: DuckDB
refuses to even *open* a file (read-only or not) while another process holds
it read-write, and if that process shuts down leaving unreplayed WAL
entries, the next opener needs write access just to finish replaying them --
which collided badly with having Streamlit and this backend as two
independent processes during the transition. SQLite's WAL mode is
specifically built for "multiple readers + one writer, none of them block
each other" -- exactly the shape of problem that kept recurring. See
[full session record] for the concrete errors this replaced.

This is a clean-slate database: no data was migrated from the old DuckDB
file. init_db() creates empty tables; the user re-runs the AMFI sync to
repopulate from scratch.

Structural pattern kept mostly identical to the old DuckDB version on purpose
-- threading.RLock write-serialization, self-healing get_connection(),
bump_data_version()/get_data_version() -- so queries.py's calling convention
(`con = get_connection(); ...; con.close()`) needed no structural change,
only the SQL dialect inside each query. One deliberate exception: connections
are thread-local, not a single shared global -- see get_connection()'s
docstring for why (sharing one connection's cursors across concurrently-
running threads made the whole app hang intermittently under real load).
"""

import datetime
import os
import sqlite3
import threading
import time
from typing import Any, Optional

import numpy as np
import pandas as pd

from app.core.cache import clear_all_caches
from app.core.config import settings

DB_PATH = settings.db_path

# datetime.date <-> SQLite TEXT (ISO 8601) round-tripping, so DATE columns and
# date-valued query parameters behave like DuckDB's native DATE type without
# every call site needing to know it's actually TEXT under the hood.
sqlite3.register_adapter(datetime.date, lambda d: d.isoformat())
sqlite3.register_adapter(datetime.datetime, lambda dt: dt.isoformat(sep=" "))
sqlite3.register_converter("DATE", lambda b: datetime.date.fromisoformat(b.decode()))
sqlite3.register_converter("TIMESTAMP", lambda b: datetime.datetime.fromisoformat(b.decode()))


class _Median:
    """SQLite has no builtin MEDIAN aggregate (unlike DuckDB) -- this
    replicates it: standard 50th-percentile, NULLs ignored, average of the
    two middle values on an even count."""

    def __init__(self) -> None:
        self.values: list[float] = []

    def step(self, value) -> None:
        if value is not None:
            self.values.append(value)

    def finalize(self) -> Optional[float]:
        n = len(self.values)
        if n == 0:
            return None
        s = sorted(self.values)
        mid = n // 2
        if n % 2:
            return s[mid]
        return (s[mid - 1] + s[mid]) / 2.0


class _Stddev:
    """Sample standard deviation (N-1 denominator), matching DuckDB's
    STDDEV/STDDEV_SAMP default. NULLs ignored; returns NULL for fewer than 2
    values (matches STDDEV_SAMP's own convention -- population variance is
    undefined from a single point)."""

    def __init__(self) -> None:
        self.values: list[float] = []

    def step(self, value) -> None:
        if value is not None:
            self.values.append(value)

    def finalize(self) -> Optional[float]:
        n = len(self.values)
        if n < 2:
            return None
        mean = sum(self.values) / n
        var = sum((v - mean) ** 2 for v in self.values) / (n - 1)
        return var ** 0.5


# One real sqlite3.Connection per thread, not one shared global -- see get_connection()'s
# docstring below for why (sharing cursors of one connection across concurrently-running
# threads was found to make the whole app hang intermittently). Tests force a fresh
# connection for a new temp DB path via `connection._THREAD_LOCAL.con = None`.
_THREAD_LOCAL = threading.local()

# Serializes every DB *write* path (daily sync, backfill chunks, cost import, summary-table
# rebuild) so the background sync daemon's scheduled/heartbeat runs can never race a manual
# "Sync Now" / "Recompute" click (or each other). Reentrant so a writer that itself calls
# refresh_summary_table() (which also takes this lock) doesn't deadlock on itself. This is
# still needed even with thread-local connections: SQLite's WAL mode allows multiple
# concurrent readers alongside one writer, but still only one writer at a time -- this lock
# is what makes that true across this app's own multiple threads (SQLite's own busy_timeout
# handles it too, but returning a clean queued wait beats surfacing "database is locked").
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


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(
        DB_PATH,
        check_same_thread=False,
        detect_types=sqlite3.PARSE_DECLTYPES,
        isolation_level=None,  # autocommit -- every statement (incl. INSERT/UPDATE/DDL)
                                # commits immediately, matching the prior DuckDB code's
                                # behavior; nothing in this codebase calls .commit() itself.
    )
    con.execute("PRAGMA journal_mode=WAL")
    # Readers/writers don't block each other in WAL mode; this only matters for the rare
    # case of two writers overlapping (WRITE_LOCK already prevents that within this
    # process) -- set generously anyway rather than surfacing "database is locked".
    con.execute("PRAGMA busy_timeout=10000")
    con.execute("PRAGMA foreign_keys=ON")
    con.create_aggregate("MEDIAN", 1, _Median)
    con.create_aggregate("STDDEV", 1, _Stddev)
    con.create_aggregate("STDDEV_SAMP", 1, _Stddev)
    # SQLite's builtin SQRT is a compile-time-optional extension (SQLITE_ENABLE_MATH_FUNCTIONS)
    # not guaranteed present in every build -- register it explicitly rather than assume.
    con.create_function("SQRT", 1, lambda x: None if x is None else x ** 0.5)
    return con


def get_connection() -> sqlite3.Connection:
    """Returns a cursor on a connection unique to the calling thread, transparently
    reconnecting if that thread's connection has gone bad (mirrors the old DuckDB
    self-healing check). A cheap SELECT 1 health-check catches this before it can take
    down every page until a human intervenes.

    Thread-local, NOT a shared global connection (an earlier version of this function
    used one global `sqlite3.Connection` and handed out `.cursor()`s from it to every
    thread) -- found live, the hard way, that sharing one connection's cursors across
    concurrently-running threads (FastAPI's sync route handlers each run in their own
    thread via run_in_threadpool; a single page load fires ~8 of them at once) made the
    whole app intermittently and unpredictably hang, including for completely unrelated,
    read-only, lock-free requests, until the *whole backend* looked dead (even /api/health
    stopped responding, and Docker's own healthcheck started failing). SQLite's WAL mode
    is specifically designed for multiple independent connections to the same file
    coexisting safely -- this leverages that directly instead of serializing everything
    through one shared connection object, which doesn't reliably work across threads
    regardless of `check_same_thread=False` (that flag only disables Python's own
    same-thread assertion; it doesn't retroactively make concurrent multi-thread use of
    one connection's cursors safe).

    Contract note: callers still get back a `.cursor()` and still call `.close()` on it
    exactly as before -- that only closes the cursor, not the thread's underlying
    connection, so no call site anywhere else in the codebase needed to change."""
    con = getattr(_THREAD_LOCAL, "con", None)
    if con is not None:
        try:
            con.execute("SELECT 1")
        except Exception:
            try:
                con.close()
            except Exception:
                pass
            con = None
    if con is None:
        con = _connect()
        _THREAD_LOCAL.con = con
    return con.cursor()


def _execute_with_conflict_retry(con, sql, attempts=3, delay_seconds=1.0):
    """Retries a DDL/DML statement a couple of times on a transient "database is locked"
    error -- e.g. the background sync daemon's summary-table rebuild racing a concurrent
    read. A real (non-lock) error still raises immediately."""
    last_err = None
    for attempt in range(attempts):
        try:
            con.execute(sql)
            return
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower() and "busy" not in str(e).lower():
                raise
            last_err = e
            if attempt < attempts - 1:
                time.sleep(delay_seconds)
    raise last_err


# --- Shared SQLite-dialect helpers -----------------------------------------
# Used by both db/queries.py and amfi_sync.py (both write DataFrames into
# staging tables and read result sets back into DataFrames), so they live
# here rather than being duplicated or cross-imported as "private" helpers.
# See db/queries.py's module docstring for the full DuckDB->SQLite dialect
# notes these exist to bridge.


def fetchdf(cursor: sqlite3.Cursor) -> pd.DataFrame:
    """DuckDB's cursor.fetchdf() equivalent for the stdlib sqlite3 API."""
    cols = [d[0] for d in cursor.description] if cursor.description else []
    return pd.DataFrame(cursor.fetchall(), columns=cols)


def pyval(v: Any) -> Any:
    """Normalizes a pandas/numpy scalar to a type sqlite3's parameter binder
    accepts natively (it only knows None/int/float/str/bytes) -- mirrors
    core/serialize.py's sanitize_floats(), but at the DB-write boundary
    instead of the JSON-response boundary."""
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
        return v.date().isoformat()
    if isinstance(v, datetime.datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, datetime.date):
        return v.isoformat()
    if isinstance(v, np.bool_):
        return bool(v)
    return v


def write_staging_table(con: sqlite3.Cursor, table_name: str, df: pd.DataFrame) -> None:
    """Writes a DataFrame to a connection-scoped TEMP TABLE for use in
    UPDATE...FROM / INSERT...SELECT / DELETE...WHERE EXISTS patterns --
    SQLite has no DuckDB-style register()-a-DataFrame-directly. Caller is
    responsible for dropping it when done (mirrors the old unregister() step)."""
    con.execute(f"DROP TABLE IF EXISTS {table_name}")
    cols = list(df.columns)
    col_defs = ", ".join(f'"{c}"' for c in cols)
    con.execute(f"CREATE TEMP TABLE {table_name} ({col_defs})")
    placeholders = ", ".join(["?"] * len(cols))
    rows = [tuple(pyval(v) for v in row) for row in df.itertuples(index=False, name=None)]
    if rows:
        con.executemany(f"INSERT INTO {table_name} VALUES ({placeholders})", rows)
