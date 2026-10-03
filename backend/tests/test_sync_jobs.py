"""The sync job tracker: one job at a time, every trigger visible, the last run of each kind
kept across restarts, and a missed nightly refresh caught up with the full chain."""

import datetime

from app import amfi_sync

IST_MIDNIGHT = datetime.datetime(2026, 9, 28, 0, 0)


def _at(ist: datetime.datetime):
    """(epoch, IST datetime) pair for full_refresh_due, anchored on a fixed epoch."""
    base_epoch = 1_800_000_000.0
    return base_epoch + (ist - IST_MIDNIGHT).total_seconds(), ist


def test_a_refresh_is_due_until_one_succeeds_after_the_nightly_slot():
    now_epoch, now_ist = _at(datetime.datetime(2026, 9, 28, 9, 0))
    slot_epoch = now_epoch - (now_ist - datetime.datetime(2026, 9, 28, 0, 5)).total_seconds()
    assert amfi_sync.full_refresh_due(None, now_epoch, now_ist) is True
    # Yesterday's success is not enough once today's 00:05 has passed (the machine was off).
    assert amfi_sync.full_refresh_due({"ok": True, "finished_at": slot_epoch - 3600}, now_epoch, now_ist) is True
    assert amfi_sync.full_refresh_due({"ok": True, "finished_at": slot_epoch + 60}, now_epoch, now_ist) is False


def test_a_failed_refresh_is_retried_but_not_hammered():
    now_epoch, now_ist = _at(datetime.datetime(2026, 9, 28, 9, 0))
    assert amfi_sync.full_refresh_due({"ok": False, "finished_at": now_epoch - 600}, now_epoch, now_ist) is False
    assert amfi_sync.full_refresh_due({"ok": False, "finished_at": now_epoch - 4 * 3600}, now_epoch, now_ist) is True


def test_before_five_past_midnight_yesterdays_slot_counts():
    now_epoch, now_ist = _at(datetime.datetime(2026, 9, 28, 0, 2))
    yesterday_slot = now_epoch - (now_ist - datetime.datetime(2026, 9, 27, 0, 5)).total_seconds()
    assert amfi_sync.full_refresh_due({"ok": True, "finished_at": yesterday_slot + 10}, now_epoch, now_ist) is False


def test_only_one_job_runs_and_its_outcome_is_kept(pg_db):
    seen = {}

    def body(trigger, step):
        step("Working")
        seen["activity"] = amfi_sync.current_sync_activity()
        # A second job while this one runs is skipped, not stacked.
        seen["second"] = amfi_sync.run_sync_job("nav", "scheduled_23:30", lambda t, s: {"ok": True})
        return {"ok": True, "message": "done"}

    assert amfi_sync.run_sync_job("ter", "startup", body) == {"ok": True, "message": "done"}
    assert seen["activity"]["kind"] == "ter" and seen["activity"]["step"] == "Working"
    assert seen["second"] is None
    assert amfi_sync.current_sync_activity() is None
    last = amfi_sync.get_sync_job_status()["last"]
    assert last["ter"]["ok"] is True and last["ter"]["trigger"] == "startup" and last["ter"]["message"] == "done"
    assert last["nav"] is None


def test_a_job_that_raises_is_recorded_and_releases_the_tracker(pg_db):
    def boom(trigger, step):
        raise RuntimeError("AMFI down")

    assert amfi_sync.run_sync_job("full", "scheduled_00:05", boom) == {"ok": False, "message": "AMFI down"}
    assert amfi_sync.current_sync_activity() is None
    assert amfi_sync.get_sync_job_status()["last"]["full"]["ok"] is False


def test_catch_up_runs_the_full_chain_when_the_nightly_refresh_was_missed(pg_db, monkeypatch):
    ran = []
    monkeypatch.setattr(amfi_sync, "is_database_stale", lambda: (False, datetime.date(2026, 9, 25), datetime.date(2026, 9, 25)))
    monkeypatch.setattr(amfi_sync, "sync_all", lambda _trigger="manual", progress=None: (ran.append(("full", _trigger)), {"ok": True})[1])
    assert amfi_sync.catch_up("startup") == "full"
    assert ran == [("full", "startup")]
    # Once it has succeeded, the next heartbeat has nothing to do.
    assert amfi_sync.catch_up("heartbeat") is None


def test_catch_up_does_only_navs_when_the_refresh_is_current(pg_db, monkeypatch):
    amfi_sync.run_sync_job("full", "scheduled_00:05", lambda t, s: {"ok": True})
    monkeypatch.setattr(amfi_sync, "is_database_stale", lambda: (True, datetime.date(2026, 9, 25), datetime.date(2026, 9, 26)))
    monkeypatch.setattr(amfi_sync, "sync_daily_nav", lambda _trigger="manual": (True, "NAVs in"))
    assert amfi_sync.catch_up("heartbeat") == "catchup"
    assert amfi_sync.get_sync_job_status()["last"]["catchup"]["message"] == "NAVs in"


def test_tonights_nav_gap_is_pending_until_the_evening_sync_has_had_its_slot(monkeypatch):
    """1 Oct 2026, 23:01: AMFI had published the day's NAVs and the top bar told the owner
    to catch up in Data Management -- half an hour before the scheduled 23:30 sync would."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "enable_sync_daemon", True)
    at = lambda day, h, m: datetime.datetime(2026, 10, day, h, m)
    pending = amfi_sync.evening_sync_pending
    assert not pending(at(1, 22, 59))        # nothing published yet: not stale at all
    assert pending(at(1, 23, 1)) and pending(at(1, 23, 49))
    assert not pending(at(1, 23, 51))        # slot + grace gone: behind means overdue
    assert not pending(at(3, 23, 10))        # Saturday: nothing new is expected tonight
    monkeypatch.setattr(settings, "enable_sync_daemon", False)
    assert not pending(at(1, 23, 10))        # no daemon, no scheduled sync to wait for
