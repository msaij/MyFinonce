"""Tests for amfi_sync.py's backfill start/stop control-flow state machine --
NOT the actual chunk/month fetch-and-merge logic (see test_amfi_sync_write_path.py
for that). These call the plain state-dict functions (stop_historical_backfill,
stop_ter_backfill, get_*_status) directly rather than through
start_historical_backfill()/start_ter_backfill(), which spawn a real background
thread that would make real AMFI network calls -- see test_admin_router.py's
fixture comment for the concrete incident that class of mistake caused.
"""

import pytest

from app import amfi_sync
from app.amfi_ter_client import AmfiTerClient


@pytest.fixture(autouse=True)
def _reset_backfill_state():
    """BACKFILL_STATE/TER_BACKFILL_STATE are module-level globals shared across the
    whole test session -- save and restore them around every test here so one test's
    should_stop=True can't leak into another test (in this file or another) that
    happens to run afterward."""
    saved_backfill = dict(amfi_sync.BACKFILL_STATE)
    saved_ter_backfill = dict(amfi_sync.TER_BACKFILL_STATE)
    yield
    amfi_sync.BACKFILL_STATE.clear()
    amfi_sync.BACKFILL_STATE.update(saved_backfill)
    amfi_sync.TER_BACKFILL_STATE.clear()
    amfi_sync.TER_BACKFILL_STATE.update(saved_ter_backfill)


class TestStopHistoricalBackfill:
    def test_stop_sets_should_stop_when_running(self):
        amfi_sync.BACKFILL_STATE["is_running"] = True
        amfi_sync.BACKFILL_STATE["should_stop"] = False
        amfi_sync.stop_historical_backfill()
        assert amfi_sync.get_backfill_status()["should_stop"] is True

    def test_stop_sets_should_stop_even_when_not_currently_running(self):
        """Regression test for the reported "Pause/Stop Backfill doesn't work" bug:
        a previous version of stop_historical_backfill() only set should_stop under
        an `if BACKFILL_STATE["is_running"]:` guard, which could silently no-op a
        stop request that landed in the narrow window around a status read/write
        race, with zero feedback that anything had gone wrong. It must be
        unconditional, exactly like stop_ter_backfill() below -- and harmless, since
        _historical_backfill_worker() resets should_stop to False itself the moment
        a new run actually starts."""
        amfi_sync.BACKFILL_STATE["is_running"] = False
        amfi_sync.BACKFILL_STATE["should_stop"] = False
        amfi_sync.stop_historical_backfill()
        assert amfi_sync.get_backfill_status()["should_stop"] is True


class TestStopTerBackfill:
    def test_stop_sets_should_stop_even_when_not_currently_running(self):
        """Pinning down the behavior stop_historical_backfill() above was just made
        to match -- already correct here, kept as a regression guard for parity."""
        amfi_sync.TER_BACKFILL_STATE["is_running"] = False
        amfi_sync.TER_BACKFILL_STATE["should_stop"] = False
        amfi_sync.stop_ter_backfill()
        assert amfi_sync.get_ter_backfill_status()["should_stop"] is True


class _FakeCon:
    def close(self):
        pass


class TestTerBackfillRowAccounting:
    """The worker drove its progress card and its checkpoint ledger off a counter nothing
    ever incremented: a run that wrote 1.4M rows reported "0 records added" throughout, and
    every checkpoint row recorded rows_written = 0. Driven here with the network and the
    database stubbed out -- see this module's docstring on why a real run must never start."""

    @pytest.fixture()
    def marks(self, monkeypatch):
        recorded = []
        monkeypatch.setattr(amfi_sync.db, "get_connection", lambda *a, **k: _FakeCon())
        monkeypatch.setattr(amfi_sync.bulk, "completed_units", lambda con, job: set())
        monkeypatch.setattr(
            amfi_sync.bulk, "checkpoint_mark",
            lambda con, job, unit, state, rows_written=0, detail=None:
                recorded.append({"unit": unit, "state": state, "rows_written": rows_written}),
        )
        return recorded

    def test_rows_written_reaches_the_state_and_the_checkpoints(self, monkeypatch, marks):
        # The two months the worker will actually walk, taken from the same source it uses --
        # hard-coding "09-2026"/"08-2026" broke the day the calendar rolled into October.
        latest, previous = AmfiTerClient.recent_months(2)
        per_month = {latest: 1000, previous: 2500}

        def fake_sync(_trigger="manual", months=None, stats=None):
            if stats is not None:
                stats["rows_written"] = per_month[months[0]]
            return True, "ok"

        monkeypatch.setattr(amfi_sync, "sync_official_ter", fake_sync)
        amfi_sync._ter_backfill_worker(2, resume=True)

        assert amfi_sync.get_ter_backfill_status()["records_added"] == 3500
        assert {m["unit"]: m["rows_written"] for m in marks} == per_month
        assert all(m["state"] == "done" for m in marks)

    def test_a_month_that_wrote_nothing_is_still_checkpointed_at_zero(self, monkeypatch, marks):
        """A month AMFI has no disclosures for is legitimately empty; it must not be
        mistaken for an unwritten counter, and must not be retried forever."""
        monkeypatch.setattr(amfi_sync, "sync_official_ter",
                            lambda _trigger="manual", months=None, stats=None: (True, "ok"))
        amfi_sync._ter_backfill_worker(1, resume=True)

        assert amfi_sync.get_ter_backfill_status()["records_added"] == 0
        assert marks and marks[0]["rows_written"] == 0 and marks[0]["state"] == "done"

    def test_a_failed_month_is_not_marked_done(self, monkeypatch, marks):
        """completed_units() only skips 'done', so a rate-limited month has to stay 'failed'
        for the next run to pick it up again."""
        monkeypatch.setattr(amfi_sync, "sync_official_ter",
                            lambda _trigger="manual", months=None, stats=None: (False, "rate limited"))
        amfi_sync._ter_backfill_worker(1, resume=True)

        assert marks and marks[0]["state"] == "failed"
