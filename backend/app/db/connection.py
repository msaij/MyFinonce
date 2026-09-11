"""Shared DuckDB connection, write-serialization, and change-version tracking.

Ported near-verbatim from fetcher/db.py lines 1-90. This is the one file in
the whole migration where correctness of the concurrency story matters most
-- see the migration plan's "DuckDB concurrency decision"
(../../../.claude/plans/floofy-petting-mountain.md) before changing anything
here. Summary of that decision: uvicorn runs with --workers 1 (see
backend/Dockerfile), so this module-global singleton + in-process lock
pattern is sufficient, exactly as it already is for the still-running
Streamlit app -- it does NOT protect against a second OS process also
opening this file read-write, which is why ENABLE_SYNC_DAEMON
(app.core.config.settings) stays False until the deliberate write-ownership
handoff phase.
"""

import os
import threading
import time
from typing import Optional

import duckdb

from app.core.cache import clear_all_caches
from app.core.config import settings

DB_PATH = settings.duckdb_path

_CON: Optional[duckdb.DuckDBPyConnection] = None
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
    (see /api/meta/status), and clears every cached query result app-wide so
    the next query re-reads DuckDB instead of serving pre-sync results."""
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
            _CON = duckdb.connect(DB_PATH, read_only=not settings.enable_sync_daemon)
    return _CON.cursor()


def _execute_with_conflict_retry(con, sql, attempts=3, delay_seconds=1.0):
    """Retries a DDL statement once or twice on a transient DuckDB catalog write-write
    conflict — e.g. the background sync daemon's CREATE OR REPLACE racing a concurrent
    read from an open Streamlit session on the same table. A real (non-conflict) error
    still raises immediately."""
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
