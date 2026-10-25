"""Tests for db/bulk.py -- the bulk data-movement layer the AMFI ingest runs on.

These run against a real PostgreSQL database (see conftest.py), not a stub.
That is the point: the whole module exists because of behaviour that only shows
up in a real engine -- COPY framing, ON CONFLICT arbitration against a real
primary key, what a transaction actually does to visibility, and the "cannot
affect row a second time" error a duplicate key in one batch would otherwise
raise. A fake connection would pass every one of these tests while telling us
nothing.

The SQLite-era version of this file pinned a type-affinity trap that no longer
exists (an untyped TEMP TABLE column could not be indexed for a join against a
typed one, which made every staging merge quadratic). PostgreSQL has no affinity
concept and rejects a type mismatch outright instead of silently mis-planning,
so that specific test is gone. What replaced it is
test_upsert_merge_is_index_driven_not_a_sequential_scan, which pins the property
that actually matters -- the merge resolves through an index, not a table scan --
using EXPLAIN rather than a stopwatch, for the same reason as before: the bug
class produces correct results and only shows up as cost.
"""

import psycopg
import pandas as pd
import pytest

from app.db import bulk
from app.db.connection import get_connection, write_staging_table


@pytest.fixture()
def con(pg_db):
    """A connection to the per-test database, with two extra tables in the
    production shape: BIGINT primary keys, a composite key on nav_history."""
    c = get_connection()
    c.execute("DROP TABLE IF EXISTS bulk_nav")
    c.execute("DROP TABLE IF EXISTS bulk_schemes")
    c.execute("""
        CREATE TABLE bulk_schemes (
            scheme_code BIGINT PRIMARY KEY,
            scheme_name TEXT,
            fund_house TEXT,
            ter_status TEXT,
            expense_ratio DOUBLE PRECISION
        )""")
    c.execute("""
        CREATE TABLE bulk_nav (
            scheme_code BIGINT,
            nav_date DATE,
            nav DOUBLE PRECISION,
            PRIMARY KEY (scheme_code, nav_date)
        )""")
    yield c
    c.close()


def _nav(con):
    con.execute("SELECT scheme_code, nav_date::text, nav FROM bulk_nav ORDER BY scheme_code, nav_date")
    return con.fetchall()


NAV_COLS = ["scheme_code", "nav_date", "nav"]
NAV_KEYS = ["scheme_code", "nav_date"]


def _upsert(con, rows, **kw):
    return bulk.upsert(con, table="bulk_nav", columns=NAV_COLS,
                       conflict_columns=NAV_KEYS, rows=rows, **kw)


# --- build_merge -----------------------------------------------------------
# Composed SQL objects, so they are compared as the strings Postgres will see.


def _sql(con, composed):
    return composed.as_string(con.raw_connection)


def test_build_merge_defaults_to_overwriting_every_non_key_column(con):
    s = _sql(con, bulk.build_merge("bulk_nav", NAV_COLS, NAV_KEYS, "stg"))
    assert 'INSERT INTO "bulk_nav"' in s
    assert 'ON CONFLICT ("scheme_code", "nav_date")' in s
    assert '"nav" = excluded."nav"' in s
    # The key is never in the SET list -- it is what was matched on.
    assert '"scheme_code" = excluded' not in s


def test_build_merge_dedupes_within_the_batch(con):
    """DISTINCT ON is what stops a duplicate key in one batch from aborting it
    with "ON CONFLICT DO UPDATE command cannot affect row a second time"."""
    s = _sql(con, bulk.build_merge("bulk_nav", NAV_COLS, NAV_KEYS, "stg"))
    assert 'SELECT DISTINCT ON ("scheme_code", "nav_date")' in s


def test_build_merge_do_nothing_is_insert_only(con):
    s = _sql(con, bulk.build_merge("bulk_nav", NAV_COLS, NAV_KEYS, "stg", update=bulk.DO_NOTHING))
    assert s.rstrip().endswith("DO NOTHING")


def test_build_merge_with_only_key_columns_degrades_to_do_nothing(con):
    """Nothing is left to update once the keys are excluded, and DO UPDATE with
    an empty SET list is a syntax error rather than a no-op."""
    s = _sql(con, bulk.build_merge("bulk_nav", NAV_KEYS, NAV_KEYS, "stg"))
    assert s.rstrip().endswith("DO NOTHING")


def test_build_merge_honors_explicit_update_expressions(con):
    s = _sql(con, bulk.build_merge(
        "bulk_schemes", ["scheme_code", "expense_ratio"], ["scheme_code"], "stg",
        update={"expense_ratio": "CASE WHEN bulk_schemes.ter_status = 'official' "
                                 "THEN bulk_schemes.expense_ratio ELSE excluded.expense_ratio END"}))
    assert "CASE WHEN bulk_schemes.ter_status = 'official'" in s


def test_build_merge_appends_update_where(con):
    s = _sql(con, bulk.build_merge("bulk_nav", NAV_COLS, NAV_KEYS, "stg",
                                   update={"nav": "excluded.nav"},
                                   update_where="bulk_nav.nav IS DISTINCT FROM excluded.nav"))
    assert s.rstrip().endswith("WHERE bulk_nav.nav IS DISTINCT FROM excluded.nav")


@pytest.mark.parametrize("kwargs, match", [
    (dict(columns=[], conflict_columns=["a"]), "at least one column"),
    (dict(columns=["a"], conflict_columns=[]), "at least one conflict column"),
    (dict(columns=["a"], conflict_columns=["b"]), "absent from insert column list"),
])
def test_build_merge_rejects_malformed_arguments(kwargs, match):
    with pytest.raises(ValueError, match=match):
        bulk.build_merge("t", staging_table="stg", **kwargs)


def test_build_merge_refuses_to_update_the_conflict_key():
    """Assigning the key inside its own DO UPDATE arm is always a mistake -- it
    would rewrite the very column the conflict was matched on."""
    with pytest.raises(ValueError, match="cannot update the conflict key"):
        bulk.build_merge("t", ["a", "b"], ["a"], "stg", update={"a": "excluded.a"})


def test_build_merge_rejects_update_of_a_column_not_being_inserted():
    with pytest.raises(ValueError, match="not being inserted"):
        bulk.build_merge("t", ["a", "b"], ["a"], "stg", update={"zz": "1"})


# --- upsert ----------------------------------------------------------------


def test_upsert_inserts_new_rows(con):
    result = _upsert(con, [(1, "2025-01-01", 10.0), (2, "2025-01-01", 20.0)])
    assert result.rows_written == 2
    assert result.batches == 1
    assert _nav(con) == [(1, "2025-01-01", 10.0), (2, "2025-01-01", 20.0)]


def test_upsert_updates_a_conflicting_row_instead_of_raising(con):
    _upsert(con, [(1, "2025-01-01", 10.0)])
    _upsert(con, [(1, "2025-01-01", 99.0)], update={"nav": "excluded.nav"})
    assert _nav(con) == [(1, "2025-01-01", 99.0)]


def test_upsert_is_idempotent(con):
    """The property the whole retry/resume design rests on: running the identical
    payload again must converge to the same rows, never duplicate or error."""
    rows = [(1, "2025-01-01", 10.0), (1, "2025-01-02", 11.0), (2, "2025-01-01", 20.0)]
    _upsert(con, rows, update={"nav": "excluded.nav"})
    first = _nav(con)
    for _ in range(3):
        _upsert(con, rows, update={"nav": "excluded.nav"})
    assert _nav(con) == first


def test_upsert_do_nothing_leaves_the_stored_row_untouched(con):
    _upsert(con, [(1, "2025-01-01", 10.0)])
    _upsert(con, [(1, "2025-01-01", 99.0)], update=bulk.DO_NOTHING)
    assert _nav(con) == [(1, "2025-01-01", 10.0)]


def test_upsert_tolerates_a_duplicate_key_inside_one_batch(con):
    """Without DISTINCT ON in the merge, Postgres aborts the whole batch with
    "cannot affect row a second time". One duplicate must not fail an ingest."""
    result = _upsert(con, [(1, "2025-01-01", 10.0), (1, "2025-01-01", 11.0)])
    assert result.rows_written == 2  # both submitted
    assert len(_nav(con)) == 1       # one row stored


def test_upsert_update_where_skips_the_no_op_write(con):
    """`update_where` must suppress a rewrite when the value is unchanged, while
    still correcting one that did change -- both halves, since a predicate that
    suppressed everything would also pass a test checking only the first."""
    _upsert(con, [(1, "2025-01-01", 10.0), (2, "2025-01-01", 20.0)])
    kw = dict(update={"nav": "excluded.nav"},
              update_where="bulk_nav.nav IS DISTINCT FROM excluded.nav")

    # xmin is the inserting/updating transaction id, so it changes if and only if
    # the row was actually rewritten -- a direct check that the write was skipped
    # rather than an inference from the value being equal.
    con.execute("SELECT xmin::text FROM bulk_nav WHERE scheme_code = 1")
    xmin_before = con.fetchone()[0]

    _upsert(con, [(1, "2025-01-01", 10.0)], **kw)  # identical value
    con.execute("SELECT xmin::text FROM bulk_nav WHERE scheme_code = 1")
    assert con.fetchone()[0] == xmin_before, "unchanged row should not be rewritten"

    _upsert(con, [(2, "2025-01-01", 22.0)], **kw)  # changed value
    assert _nav(con) == [(1, "2025-01-01", 10.0), (2, "2025-01-01", 22.0)]


def test_upsert_null_stored_value_is_corrected(con):
    """`IS DISTINCT FROM`, not `!=`: with `!=` a stored NULL yields NULL, the
    WHERE reads it as false, and the row would silently never be filled in."""
    con.execute("INSERT INTO bulk_nav (scheme_code, nav_date, nav) VALUES (1, '2025-01-01', NULL)")
    _upsert(con, [(1, "2025-01-01", 10.0)],
            update={"nav": "excluded.nav"},
            update_where="bulk_nav.nav IS DISTINCT FROM excluded.nav")
    assert _nav(con) == [(1, "2025-01-01", 10.0)]


def test_upsert_batches_and_commits_each_batch(con):
    rows = [(i, "2025-01-01", float(i)) for i in range(1, 251)]
    result = _upsert(con, rows, batch_size=100)
    assert result.batches == 3
    assert result.rows_written == 250
    con.execute("SELECT COUNT(*) FROM bulk_nav")
    assert con.fetchone()[0] == 250


def test_upsert_accepts_a_generator_without_materializing_it(con):
    result = _upsert(con, ((i, "2025-01-01", float(i)) for i in range(1, 51)), batch_size=10)
    assert (result.batches, result.rows_written) == (5, 50)


def test_upsert_handles_an_empty_payload(con):
    result = _upsert(con, [])
    assert (result.batches, result.rows_written) == (0, 0)
    assert _nav(con) == []


def test_upsert_stop_request_retains_every_committed_batch(con):
    """Durable partial progress -- the reason batches commit individually. A stop
    must keep what is already written rather than rolling the ingest back."""
    calls = {"n": 0}

    def should_stop():
        calls["n"] += 1
        return calls["n"] > 2  # allow two batches, then stop

    result = _upsert(con, [(i, "2025-01-01", float(i)) for i in range(1, 101)],
                     batch_size=10, should_stop=should_stop)
    assert result.stopped_early is True
    assert result.rows_written == 20
    con.execute("SELECT COUNT(*) FROM bulk_nav")
    assert con.fetchone()[0] == 20, "committed batches must survive a stop"


def test_upsert_inside_a_caller_transaction_joins_it(con):
    """When the caller already owns a transaction the batches must join it rather
    than committing on their own -- proven by rolling back and seeing nothing."""
    con.begin()
    result = _upsert(con, [(i, "2025-01-01", float(i)) for i in range(1, 31)], batch_size=10)
    assert result.rows_written == 30
    con.rollback()
    con.execute("SELECT COUNT(*) FROM bulk_nav")
    assert con.fetchone()[0] == 0


def test_upsert_rejects_an_unknown_column_before_touching_the_database(con):
    with pytest.raises(ValueError, match="no column"):
        bulk.upsert(con, table="bulk_nav", columns=["scheme_code", "not_a_column"],
                    conflict_columns=["scheme_code"], rows=[(1, 2)])


def test_upsert_surfaces_a_real_error_rather_than_retrying_it(con):
    """A genuine data error must fail fast. Retrying it would turn a clear
    failure into a multi-second stall and then the same failure."""
    with pytest.raises(psycopg.Error):
        _upsert(con, [(1, "not-a-date", 10.0)], retry_delay_seconds=0)


def test_upsert_cleans_up_its_staging_table_even_on_failure(con):
    """The staging table is session-scoped and pooled connections outlive a call,
    so a leaked one would collide with the next caller's CREATE."""
    with pytest.raises(psycopg.Error):
        _upsert(con, [(1, "not-a-date", 10.0)], retry_delay_seconds=0)
    con.execute("SELECT to_regclass('stg_bulk_bulk_nav')")
    assert con.fetchone()[0] is None
    # And the next call still works.
    assert _upsert(con, [(1, "2025-01-01", 10.0)]).rows_written == 1


def test_bulk_result_reports_a_rate_without_dividing_by_zero():
    assert bulk.BulkResult().rows_per_second == 0.0
    assert bulk.BulkResult(rows_written=100, elapsed_seconds=2.0).rows_per_second == 50.0


# --- checkpoints -----------------------------------------------------------


def test_checkpoints_round_trip(con):
    bulk.checkpoint_mark(con, "job", "unit-a", "done", rows_written=5)
    bulk.checkpoint_mark(con, "job", "unit-b", "done", rows_written=7)
    assert bulk.completed_units(con, "job") == {"unit-a", "unit-b"}


def test_failed_units_are_not_treated_as_complete(con):
    """A failed unit must be retried on the next run, not skipped as though done."""
    bulk.checkpoint_mark(con, "job", "unit-a", "done")
    bulk.checkpoint_mark(con, "job", "unit-b", "failed", detail="boom")
    assert bulk.completed_units(con, "job") == {"unit-a"}


def test_remarking_a_unit_replaces_its_state(con):
    bulk.checkpoint_mark(con, "job", "unit-a", "failed", detail="boom")
    assert bulk.completed_units(con, "job") == set()
    bulk.checkpoint_mark(con, "job", "unit-a", "done", rows_written=3)
    assert bulk.completed_units(con, "job") == {"unit-a"}


def test_checkpoints_are_scoped_per_job(con):
    bulk.checkpoint_mark(con, "job-1", "shared-unit", "done")
    assert bulk.completed_units(con, "job-2") == set()


def test_clear_checkpoints_only_clears_the_named_job(con):
    bulk.checkpoint_mark(con, "job-1", "u1", "done")
    bulk.checkpoint_mark(con, "job-1", "u2", "done")
    bulk.checkpoint_mark(con, "job-2", "u1", "done")
    assert bulk.clear_checkpoints(con, "job-1") == 2
    assert bulk.completed_units(con, "job-1") == set()
    assert bulk.completed_units(con, "job-2") == {"u1"}


# --- the property that made this module necessary --------------------------


def test_upsert_merge_is_index_driven_not_a_sequential_scan(con):
    """Pins the cost property, not just the result.

    The predecessor of this module joined a staging table against nav_history
    per row and took 81 minutes on a 555K-row chunk without finishing, while
    returning entirely correct data. Nothing about the output could have caught
    that, and a timing assertion on a test-sized fixture could not either -- so
    assert on the plan: the merge must resolve conflicts through the primary
    key's index.
    """
    _upsert(con, [(i, "2025-01-01", float(i)) for i in range(1, 501)])
    con.execute("ANALYZE bulk_nav")
    con.execute("CREATE TEMP TABLE stg_plan (scheme_code BIGINT, nav_date DATE, nav DOUBLE PRECISION)")
    con.execute("INSERT INTO stg_plan VALUES (1, '2025-01-01', 1.0)")
    merge = bulk.build_merge("bulk_nav", NAV_COLS, NAV_KEYS, "stg_plan",
                             update={"nav": "excluded.nav"})
    con.execute(b"EXPLAIN " + merge.as_bytes(con.raw_connection))
    plan = "\n".join(r[0] for r in con.fetchall())
    assert "Conflict Resolution: UPDATE" in plan, plan
    assert "bulk_nav_pkey" in plan, plan
    assert "Seq Scan on bulk_nav" not in plan, plan


def test_write_staging_table_mirrors_destination_column_types(con):
    """The smaller TER/cost merges still stage a DataFrame; the staging table's
    types must match the destination so join keys stay index-eligible."""
    df = pd.DataFrame({"scheme_code": [1], "nav_date": [pd.Timestamp("2025-01-01")], "nav": [10.0]})
    write_staging_table(con, "stg_types", df, like_table="bulk_nav",
                        key_columns=["scheme_code", "nav_date"])
    con.execute("""SELECT column_name, data_type FROM information_schema.columns
                   WHERE table_name = 'stg_types'""")
    types = {r[0]: r[1] for r in con.fetchall()}
    assert types == {"scheme_code": "bigint", "nav_date": "date",
                     "nav": "double precision"}


def test_write_staging_table_loads_the_rows(con):
    df = pd.DataFrame({"scheme_code": [1, 2], "nav_date": [pd.Timestamp("2025-01-01")] * 2,
                       "nav": [10.0, 20.0]})
    write_staging_table(con, "stg_rows", df, like_table="bulk_nav",
                        key_columns=["scheme_code"])
    con.execute("SELECT scheme_code, nav FROM stg_rows ORDER BY scheme_code")
    assert con.fetchall() == [(1, 10.0), (2, 20.0)]
