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
