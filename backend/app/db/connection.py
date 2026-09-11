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

Structural pattern kept identical to the old DuckDB version on purpose --
module-global singleton + threading.RLock write-serialization, self-healing
get_connection(), bump_data_version()/get_data_version() -- so queries.py's
calling convention (`con = get_connection(); ...; con.close()`) needed no
structural change, only the SQL dialect inside each query.
"""

import datetime
import os
import sqlite3
import threading
import time
from typing import Optional

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


_CON: Optional[sqlite3.Connection] = None
_LOCK = threading.Lock()

# Serializes every DB *write* path (daily sync, backfill chunks, cost import, summary-table
# rebuild) so the background sync daemon's scheduled/heartbeat runs can never race a manual
# "Sync Now" / "Recompute" click (or each other) on the same shared connection. Reentrant so
# a writer that itself calls refresh_summary_table() (which also takes this lock) doesn't
# deadlock on itself. Within-process only, same as before -- see connection setup below for
# how cross-process safety is now handled differently than the DuckDB version.
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
    """Returns a cursor on the shared singleton connection, transparently reconnecting if
    the connection has gone bad (mirrors the old DuckDB self-healing check). A cheap
    SELECT 1 health-check catches this before it can take down every page until a human
    intervenes."""
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
            _CON = _connect()
    return _CON.cursor()


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
