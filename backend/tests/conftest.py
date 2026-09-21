"""Shared test database setup.

Under SQLite, isolating a test was free: point `connection.DB_PATH` at a
`tmp_path` file and every test got its own private database. PostgreSQL is a
server, so isolation has to be arranged explicitly -- that is all this file
does.

The approach is a single dedicated test database, with **every table dropped and
recreated between tests** (`DROP SCHEMA public CASCADE`). That is a bigger
hammer than TRUNCATE, and deliberately so: these tests exercise `init_db()`
itself, schema migrations, and `refresh_summary_table()`'s DROP/CREATE cycle, so
leftover *schema* -- not just leftover rows -- is exactly what would make one
test's failure depend on which tests ran before it. Dropping the schema costs a
few milliseconds and removes that whole class of flake.

The test database is separate from the application one and is created on demand,
so running the suite never touches real NAV data. It is NOT dropped at the end:
keeping it lets a failed run be inspected with psql, and the next run recreates
its contents anyway.
"""

import os

import psycopg
import pytest

from app.db import connection
from app.db import queries as db

# Derived from the app's own DSN so this works unchanged in the container (where
# the host is `postgres`) and against any other server via DATABASE_URL.
_APP_DSN = os.environ.get("DATABASE_URL", connection.DATABASE_URL)
TEST_DB_NAME = os.environ.get("TEST_DB_NAME", "mutual_funds_test")


def _dsn_for(database: str) -> str:
    base, _, _old = _APP_DSN.rpartition("/")
    return f"{base}/{database}"


def _ensure_test_database() -> str:
    """Creates the test database if it does not exist, and returns its DSN.

    CREATE DATABASE cannot run inside a transaction block, hence autocommit on
    the maintenance connection. Connecting to 'postgres' rather than the app
    database keeps this from depending on the app database existing yet."""
    test_dsn = _dsn_for(TEST_DB_NAME)
    with psycopg.connect(_dsn_for("postgres"), autocommit=True) as con:
        exists = con.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB_NAME,)
        ).fetchone()
        if not exists:
            con.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
    return test_dsn


@pytest.fixture(scope="session")
def test_database_url() -> str:
    return _ensure_test_database()


@pytest.fixture()
def pg_db(test_database_url, monkeypatch):
    """An empty, initialized database, private to one test.

    Repoints the module-level DSN and rebuilds the pool, because the pool caches
    connections to whatever DSN it was opened with -- without reset_pool() the
    first test would pin every later one to the wrong database."""
    monkeypatch.setattr(connection, "DATABASE_URL", test_database_url)
    connection.reset_pool()

    con = connection.get_connection()
    try:
        # CASCADE also drops the summary_table/sync_meta/sync_checkpoint objects
        # and every index, so each test starts from genuinely nothing.
        con.execute("DROP SCHEMA IF EXISTS public CASCADE")
        con.execute("CREATE SCHEMA IF NOT EXISTS public")
        con.execute("GRANT ALL ON SCHEMA public TO public")
        con.execute("SET search_path TO public")
    finally:
        con.close()

    db.invalidate_database_stats_cache()
    db.init_db()
    yield test_database_url
    connection.reset_pool()
