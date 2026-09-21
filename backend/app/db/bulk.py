"""Bulk data-movement primitives: fast, resumable, idempotent upserts.

The shape of this module survived the SQLite -> Postgres switch; the mechanism
inside it changed. Both versions exist for the same reason, so the history is
worth keeping:

**Why not a staging-table join (the original design).** Every AMFI merge path
used to write a DataFrame into a TEMP TABLE and then UPDATE/DELETE the real
table by joining against it. Under SQLite that was quadratic, for a reason that
took three wrong diagnoses to find: `write_staging_table()` created the temp
columns *untyped*, an untyped SQLite column has no type affinity, and SQLite
will not use an index on a no-affinity column to satisfy a comparison against
an INTEGER/NUMERIC-affinity one. The index was built and never usable, so every
join degraded to a full scan of the staging table *per row of the outer table* --
~3e11 row comparisons for one 555K-row chunk, observed live as a DELETE that ran
81 minutes without finishing. Replacing the join with a primary-key upsert took
the same chunk's merge to 2.4s.

**Why COPY now (the Postgres design).** `INSERT ... ON CONFLICT` is spelled
identically in Postgres, but feeding it one parameterized row at a time wastes
most of the available throughput: every row pays statement overhead and a
round trip. Postgres's bulk-load path is `COPY`, which streams rows in a binary-
ish framing with no per-row statement handling. So each batch here COPYs into a
session-scoped TEMP TABLE and then merges it into the destination with a single
`INSERT INTO ... SELECT ... ON CONFLICT DO UPDATE`. That is one statement per
batch regardless of row count, and the merge itself is an index-driven upsert
against the destination's real primary key.

Note this is a staging table again -- but for the opposite reason to the one
that broke SQLite. Here it exists to enable COPY, its columns are typed from the
destination's own `information_schema` entries, and Postgres has no type-affinity
concept to mis-plan around (a type mismatch is an error, not a silent full scan).

The three properties the AMFI ingest needs, unchanged:

fast
    One COPY plus one merge statement per batch. No per-row round trips, no
    DataFrame round-trip, no correlated subqueries.

resilient
    A failed batch is retried, and batches are *idempotent by construction*
    (an upsert keyed on the destination's primary key), so a retry -- or a full
    re-run of an interrupted job -- can never double-write. That is what makes
    retrying safe to do blindly, which is what makes it reliable.

reliable
    Progress is durable *as it happens*, one committed batch at a time, instead
    of all-or-nothing at the end of a multi-hundred-thousand-row statement. A
    stop request, a crash, or a container restart keeps everything already
    committed, and `checkpoint_*()` records which units of work finished so the
    next run resumes rather than redoing them.
"""

import logging
import time
from dataclasses import dataclass
from itertools import islice
from typing import Any, Callable, Iterable, Iterator, Optional, Sequence

import psycopg
from psycopg import sql as pgsql

from app.db.connection import is_retryable

logger = logging.getLogger(__name__)

# Rows per committed batch. Larger than the SQLite-era 25,000: COPY's per-row
# cost is low enough that the batch size is now chosen for how much work an
# interruption may discard and how large a single merge statement's working set
# should get, not for statement overhead.
DEFAULT_BATCH_SIZE = 50_000

# Sentinel for `update=`: insert new rows, leave existing rows completely alone.
DO_NOTHING = object()

_STAGING_PREFIX = "stg_bulk_"


@dataclass
class BulkResult:
    """What a bulk operation actually did. Returned rather than logged-and-forgotten
    so callers can report real ingest counts instead of input-payload sizes.

    `rows_written` counts rows *submitted*, not rows changed: an upsert whose
    `update_where` suppressed a no-op rewrite still counts here. Callers that
    report this to a user should say "synced", not "added"."""

    rows_written: int = 0
    batches: int = 0
    retries: int = 0
    elapsed_seconds: float = 0.0
    stopped_early: bool = False

    @property
    def rows_per_second(self) -> float:
        return self.rows_written / self.elapsed_seconds if self.elapsed_seconds > 0 else 0.0

    def __str__(self) -> str:
        s = (f"{self.rows_written:,} rows in {self.batches} batch(es), "
             f"{self.elapsed_seconds:.1f}s ({self.rows_per_second:,.0f} rows/s)")
        if self.retries:
            s += f", {self.retries} retry(ies)"
        if self.stopped_early:
            s += ", STOPPED EARLY (committed work retained)"
        return s


def build_merge(
    table: str,
    columns: Sequence[str],
    conflict_columns: Sequence[str],
    staging_table: str,
    update: Optional[Any] = None,
    update_where: Optional[str] = None,
) -> pgsql.Composed:
    """Builds the `INSERT INTO <table> SELECT ... FROM <staging> ON CONFLICT` merge.

    ``conflict_columns`` must be covered by a UNIQUE constraint or PRIMARY KEY on
    ``table`` -- that requirement is the whole point, since it is what makes the
    merge an index probe and what makes re-running it idempotent.

    ``update`` controls what happens to a row that already exists:
      * ``None``       -- overwrite every non-key column from the incoming row.
      * ``DO_NOTHING`` -- insert-only; existing rows are left untouched.
      * ``Mapping[str, str]`` -- explicit SQL expression per column, for
        precedence rules that need to compare old against new. Expressions refer
        to the incoming row as ``excluded.<col>`` and the stored row as
        ``<table>.<col>`` (see amfi_sync.SCHEME_UPDATE, where an 'official' TER
        must not be clobbered by a derived one).

    ``update_where`` adds a predicate to the DO UPDATE arm so a row whose stored
    values already match is left alone rather than rewritten. Worth more than it
    looks: without it, re-running an overlapping chunk over unchanged history
    dirties every page in the range, which means WAL traffic and dead tuples for
    autovacuum to reclaim, for no change in the data.

    ``SELECT DISTINCT ON`` guards against duplicate keys *within one batch*.
    Postgres raises "ON CONFLICT DO UPDATE command cannot affect row a second
    time" if the source contains the same key twice, which would abort the
    batch; this collapses them instead. Which duplicate survives is arbitrary,
    so a caller that needs last-one-wins must dedupe upstream --
    amfi_sync._dedupe_nav() does exactly that.
    """
    if not columns:
        raise ValueError("build_merge() needs at least one column")
    if not conflict_columns:
        raise ValueError("build_merge() needs at least one conflict column")
    missing = [c for c in conflict_columns if c not in columns]
    if missing:
        raise ValueError(f"conflict columns absent from insert column list: {missing}")

    col_idents = pgsql.SQL(", ").join(pgsql.Identifier(c) for c in columns)
    key_idents = pgsql.SQL(", ").join(pgsql.Identifier(c) for c in conflict_columns)

    head = pgsql.SQL(
        "INSERT INTO {table} ({cols})\n"
        "SELECT DISTINCT ON ({keys}) {cols} FROM {staging} ORDER BY {keys}\n"
        "ON CONFLICT ({keys}) "
    ).format(
        table=pgsql.Identifier(table),
        cols=col_idents,
        keys=key_idents,
        staging=pgsql.Identifier(staging_table),
    )

    if update is DO_NOTHING:
        return head + pgsql.SQL("DO NOTHING")

    if update is None:
        assignments = {c: f'excluded."{c}"' for c in columns if c not in conflict_columns}
    else:
        unknown = [c for c in update if c not in columns]
        if unknown:
            raise ValueError(f"update expressions for columns not being inserted: {unknown}")
        keys = [c for c in update if c in conflict_columns]
        if keys:
            raise ValueError(f"cannot update the conflict key itself: {keys}")
        assignments = dict(update)

    if not assignments:
        return head + pgsql.SQL("DO NOTHING")

    sets = pgsql.SQL(",\n    ").join(
        pgsql.SQL("{} = {}").format(pgsql.Identifier(c), pgsql.SQL(expr))
        for c, expr in assignments.items()
    )
    merged = head + pgsql.SQL("DO UPDATE SET\n    ") + sets
    if update_where:
        merged = merged + pgsql.SQL("\nWHERE ") + pgsql.SQL(update_where)
    return merged


def _batched(rows: Iterable[Sequence[Any]], size: int) -> Iterator[list]:
    """Yields lists of at most `size` rows, consuming `rows` lazily so a generator
    source is never fully materialized just to be chunked."""
    it = iter(rows)
    while True:
        batch = list(islice(it, size))
        if not batch:
            return
        yield batch


def _column_types(con, table: str, columns: Sequence[str]) -> list:
    """The destination's own declared type for each column, so the staging table
    matches it exactly. Uses format_type() rather than information_schema's
    data_type because the latter reports 'ARRAY' and loses precision/length
    modifiers, which would silently change what COPY accepts."""
    con.execute(
        """SELECT a.attname, format_type(a.atttypid, a.atttypmod)
           FROM pg_attribute a
           WHERE a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped""",
        (table,),
    )
    found = {name: typ for name, typ in con.fetchall()}
    missing = [c for c in columns if c not in found]
    if missing:
        raise ValueError(f"table {table} has no column(s) {missing}")
    return [found[c] for c in columns]


def upsert(
    con,
    *,
    table: str,
    columns: Sequence[str],
    conflict_columns: Sequence[str],
    rows: Iterable[Sequence[Any]],
    update: Optional[Any] = None,
    update_where: Optional[str] = None,
    # Resolved from the module constant at call time, not bound as a default at
    # definition time, so adjusting DEFAULT_BATCH_SIZE actually takes effect.
    batch_size: Optional[int] = None,
    label: Optional[str] = None,
    should_stop: Optional[Callable[[], bool]] = None,
    max_attempts: int = 3,
    retry_delay_seconds: float = 0.5,
) -> BulkResult:
    """COPYs `rows` into a temp table and merges them into `table`, one committed
    transaction per batch.

    Committing per batch is the deliberate trade at the heart of this module: the
    operation is no longer atomic end-to-end, but it *is* durable incrementally
    and idempotent, which for a re-runnable ingest of public NAV data is strictly
    more useful than atomicity. A stop, crash or restart halfway through leaves a
    prefix of the data committed and correct; re-running the same job re-upserts
    those rows to the same values and continues. The all-or-nothing single
    statement this replaced could burn 81 minutes and then commit nothing.

    `should_stop` is polled between batches for cooperative cancellation --
    between rather than within, so a stop can never tear a batch in half and
    every already-committed row is kept.

    If the caller is already inside a transaction, batch transaction management
    is skipped and everything runs in the caller's transaction instead; the
    caller then owns durability.
    """
    name = label or table
    size = batch_size if batch_size is not None else DEFAULT_BATCH_SIZE
    owns_transaction = not con.in_transaction
    result = BulkResult()
    started = time.time()

    staging = f"{_STAGING_PREFIX}{table}"
    staging_ident = pgsql.Identifier(staging)
    types = _column_types(con, table, columns)
    merge_sql = build_merge(table, columns, conflict_columns, staging, update, update_where)
    copy_sql = pgsql.SQL("COPY {} ({}) FROM STDIN").format(
        staging_ident, pgsql.SQL(", ").join(pgsql.Identifier(c) for c in columns)
    )

    # One staging table per call, truncated between batches. DROP IF EXISTS first
    # because temp tables are scoped to the *session*, and a pooled connection's
    # session outlives any single call -- a previous call that died before its
    # cleanup would otherwise leave this name taken.
    con.execute(pgsql.SQL("DROP TABLE IF EXISTS {}").format(staging_ident))
    con.execute(pgsql.SQL("CREATE TEMP TABLE {} ({})").format(
        staging_ident,
        pgsql.SQL(", ").join(
            pgsql.SQL("{} {}").format(pgsql.Identifier(c), pgsql.SQL(t))
            for c, t in zip(columns, types)
        ),
    ))

    try:
        for batch in _batched(rows, size):
            if should_stop is not None and should_stop():
                result.stopped_early = True
                logger.info(f"bulk upsert {name}: stop requested -- "
                            f"{result.rows_written:,} rows already committed are retained.")
                break

            for attempt in range(1, max_attempts + 1):
                try:
                    if owns_transaction:
                        con.begin()
                    con.execute(pgsql.SQL("TRUNCATE {}").format(staging_ident))
                    with con.copy(copy_sql) as copy:
                        for row in batch:
                            copy.write_row(row)
                    con.execute(merge_sql)
                    if owns_transaction:
                        con.commit()
                    break
                except psycopg.Error as e:
                    if owns_transaction:
                        try:
                            con.rollback()
                        except Exception:
                            pass
                    if not is_retryable(e) or attempt == max_attempts:
                        raise
                    # Safe to retry the identical batch precisely because the
                    # merge is a primary-key upsert: replaying it converges to
                    # the same rows whether or not the failed attempt partially
                    # applied before rolling back.
                    result.retries += 1
                    logger.warning(f"bulk upsert {name}: batch {result.batches + 1} hit "
                                   f"{type(e).__name__} (attempt {attempt}/{max_attempts}), "
                                   f"retrying.")
                    time.sleep(retry_delay_seconds * attempt)

            result.rows_written += len(batch)
            result.batches += 1
    finally:
        try:
            con.execute(pgsql.SQL("DROP TABLE IF EXISTS {}").format(staging_ident))
        except Exception:
            # Never let cleanup mask the real error; the next call's DROP IF
            # EXISTS will deal with it anyway.
            pass

    result.elapsed_seconds = time.time() - started
    logger.info(f"bulk upsert {name}: {result}")
    return result


# --- Job checkpoints -------------------------------------------------------
# Durable "this unit of work is finished" markers, so an interrupted multi-step
# job (the 2008-to-present historical backfill is ~75 downloads) resumes instead
# of restarting. Without this, stopping and restarting the backfill re-downloaded
# and re-merged every chunk it had already completed -- correct, thanks to
# idempotent upserts, but an hour of wasted work every time.


def ensure_checkpoint_table(con) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS sync_checkpoint (
            job          TEXT NOT NULL,
            unit         TEXT NOT NULL,
            state        TEXT NOT NULL,
            rows_written BIGINT DEFAULT 0,
            detail       TEXT,
            updated_at   TIMESTAMPTZ DEFAULT now(),
            PRIMARY KEY (job, unit)
        )
    """)


def checkpoint_mark(
    con,
    job: str,
    unit: str,
    state: str,
    rows_written: int = 0,
    detail: Optional[str] = None,
) -> None:
    """Records a unit of work's outcome. `state` is 'done' or 'failed'; only 'done'
    units are skipped by completed_units(), so a failed unit is retried next run."""
    ensure_checkpoint_table(con)
    con.execute(
        """INSERT INTO sync_checkpoint (job, unit, state, rows_written, detail, updated_at)
           VALUES (%s, %s, %s, %s, %s, now())
           ON CONFLICT (job, unit) DO UPDATE SET
               state = excluded.state,
               rows_written = excluded.rows_written,
               detail = excluded.detail,
               updated_at = excluded.updated_at""",
        (job, unit, state, rows_written, detail),
    )


def completed_units(con, job: str) -> set:
    ensure_checkpoint_table(con)
    con.execute("SELECT unit FROM sync_checkpoint WHERE job = %s AND state = 'done'", (job,))
    return {r[0] for r in con.fetchall()}


def clear_checkpoints(con, job: str) -> int:
    """Forgets a job's progress so the next run redoes everything -- what a
    deliberate "full re-sync" needs, as opposed to "resume where you stopped"."""
    ensure_checkpoint_table(con)
    con.execute("DELETE FROM sync_checkpoint WHERE job = %s", (job,))
    return con.rowcount if con.rowcount and con.rowcount > 0 else 0
