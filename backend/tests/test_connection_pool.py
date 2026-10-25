"""The connection pool hands every borrower a clean session."""

from app.db import connection


def test_a_session_setting_does_not_outlive_its_borrower(pg_db):
    """queries._configure_parallel_planner SETs planner costs for one query. Without a reset
    on return, every later query on that pooled connection ran with them."""
    pool = connection.get_pool()
    with pool.connection() as conn:
        default = conn.execute("SHOW parallel_setup_cost").fetchone()[0]
        conn.execute("SET parallel_setup_cost = 10")
        pid = conn.info.backend_pid
    # The pool hands the most recently returned connection out first; make sure it is this one.
    for _ in range(connection.POOL_MAX_SIZE):
        with pool.connection() as conn:
            if conn.info.backend_pid != pid:
                continue
            assert conn.execute("SHOW parallel_setup_cost").fetchone()[0] == default
            # Settings given at connect time survive the reset.
            assert conn.execute("SHOW statement_timeout").fetchone()[0] == "1min"
            return
    raise AssertionError("the pool never handed the same connection back")
