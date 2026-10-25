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
so running the suite never touches real NAV data. Each pytest process gets its own
(mutual_funds_test_<pid>): two runs at once used to share one, and one run's per-test
DROP SCHEMA deadlocked against the other's seeding and reads. It is dropped when the
session ends; set KEEP_TEST_DB=1 to keep it for a look with psql after a failure.
"""

import os
import threading

# Before ANY app import, so Settings() is built with the news daemon off. Every
# TestClient(app) runs the real startup event, and the daemon it would start fetches
# live feeds and then writes and prunes whichever database the pool points at --
# after a pg_db teardown that used to be the live one. Only test_news.py turned it
# off; the eight other TestClient files did not.
os.environ["ENABLE_NEWS_DAEMON"] = "false"

import psycopg  # noqa: E402
import pytest  # noqa: E402

# Config only: importing it opens no connection and starts no thread.
from app.core.config import settings  # noqa: E402

# Covers the case where something imported app.core.config before this file ran.
settings.enable_news_daemon = False
# TestClient's default Host. Only here, so the running app never answers to it.
settings.allowed_hosts += ",testserver"

# Derived from the app's own DSN so this works unchanged in the container (where
# the host is `postgres`) and against any other server via DATABASE_URL.
_APP_DSN = os.environ.get("DATABASE_URL", settings.database_url)
TEST_DB_NAME = f'{os.environ.get("TEST_DB_NAME", "mutual_funds_test")}_{os.getpid()}'
_NEWS_DAEMON_THREAD_NAME = "News_Ingestion_Daemon"


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


# The test database becomes the app's database BEFORE any module that can open the
# pool is imported. A fixture is too late: app.factor_model and app.services.plan_matcher
# start warm-up threads at import that read nav_history through the pool, and under
# pytest that import happens right here, while this file loads. Repointed only in a
# fixture, those threads read the live `mutual_funds` database on every test run (and
# test_quant_tail_risk.py's REST tests passed on the live factor panel they cached).
_TEST_DSN = _ensure_test_database()
assert _TEST_DSN.rpartition("/")[2] != _APP_DSN.rpartition("/")[2], (
    "the test database must not be the application database"
)
os.environ["DATABASE_URL"] = _TEST_DSN
settings.database_url = _TEST_DSN

from app import factor_model  # noqa: E402
from app.core.cache import clear_all_caches  # noqa: E402
from app.db import connection  # noqa: E402  (reads settings.database_url at import)
from app.db import queries as db  # noqa: E402
from app.services import news_service  # noqa: E402

assert connection.DATABASE_URL == _TEST_DSN


@pytest.fixture(scope="session")
def test_database_url() -> str:
    return _TEST_DSN


@pytest.fixture(scope="session", autouse=True)
def _never_the_live_database(test_database_url):
    """Keeps the app's default DSN on the test database for the whole session.

    pg_db repoints it per test, but its monkeypatch used to restore the LIVE DSN on
    teardown, so any later code that opened a connection outside pg_db (a daemon
    thread, a test that forgot the fixture) reached `mutual_funds`. The default is
    already the test database (set above, before any app import); this re-asserts it
    in case a test reassigned it, and closes the pool at the end."""
    assert test_database_url.rpartition("/")[2] != _APP_DSN.rpartition("/")[2], (
        "the test database must not be the application database"
    )
    # The import-time warm-up threads (factor_model, plan_matcher) now read the test
    # database. Let them finish before the first pg_db drops its schema: run together,
    # DROP SCHEMA ... CASCADE and a warm-up read deadlocked once.
    for thread in threading.enumerate():
        if thread.name.endswith("_warmup"):
            thread.join(timeout=60)
    connection.DATABASE_URL = test_database_url
    connection.reset_pool()
    yield
    news_service.stop_news_daemon()
    connection.reset_pool()
    if os.environ.get("KEEP_TEST_DB") != "1":
        with psycopg.connect(_dsn_for("postgres"), autocommit=True) as con:
            con.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)')


@pytest.fixture(autouse=True)
def _no_news_daemon():
    """Fails the test that started the news daemon, rather than letting it run on."""
    yield
    alive = [t for t in threading.enumerate() if t.name == _NEWS_DAEMON_THREAD_NAME and t.is_alive()]
    if alive:
        news_service.stop_news_daemon()
        pytest.fail("a test started the news ingestion daemon; it must stay off under pytest")


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
    # Every test starts with no cached results. init_db's summary rebuild happens to clear
    # the @cached functions today; the holdings caches are keyed on versions that restart
    # with each fresh schema, so a test must never depend on that side effect.
    clear_all_caches()
    factor_model._attr_cache.clear()
    yield test_database_url
    connection.reset_pool()
