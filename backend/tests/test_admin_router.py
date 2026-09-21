"""Tests for app/routers/admin.py (Phase 9 -- Data Management page) and
app/app_logging.py's ring-buffer + SSE pub/sub mechanics.

The live SSE endpoint itself (GET /api/admin/logs/stream) is deliberately NOT
exercised through TestClient here: its generator loops forever (by design --
it's a long-lived push connection), and a plain client.get() on a streaming
response consumes it to completion, which would hang the test suite. Instead,
app_logging's subscribe()/broadcast()/entry_matches() are tested directly
(the actual logic the endpoint is a thin wrapper over), and the endpoint's
mere presence/registration is covered indirectly by every other test in this
file importing app.main successfully.
"""

import asyncio
import datetime

import pytest
from fastapi.testclient import TestClient

from app import amfi_sync, app_logging
from app.core.config import settings
from app.db import connection
from app.db import queries as db
from app.main import app


@pytest.fixture()
def client(pg_db, monkeypatch):
    # TestClient(app) triggers the real startup event, including
    # amfi_sync.ensure_sync_daemon_running() -- force this off regardless of the
    # container's real ENABLE_SYNC_DAEMON env var (defaults True as of Phase 9), or
    # every test run spins up a real background daemon thread making real network
    # calls to live AMFI servers. See test_api_quant.py's matching fixture comment
    # for the concrete incident this guards against.
    monkeypatch.setattr(settings, "enable_sync_daemon", False)
    monkeypatch.setattr(settings, "admin_token_optional", True)
    with TestClient(app) as c:
        con = connection.get_connection()
        con.execute(
            "INSERT INTO schemes (scheme_code, scheme_name, fund_house, category, plan_type, option_type, ter_status) VALUES "
            "(111, 'Fund A', 'AMC1', 'Equity Scheme - Large Cap Fund', 'Direct', 'Growth', 'official'),"
            "(222, 'Fund B', 'AMC2', 'Debt Scheme - Liquid Fund', 'Direct', 'Growth', 'legacy_unverified')"
        )
        con.execute("INSERT INTO nav_history (scheme_code, nav_date, nav) VALUES (111, '2026-01-01', 100.0), (222, '2026-01-01', 50.0)")
        con.close()
        db.refresh_summary_table()
        yield c


class TestStatus:
    def test_status_returns_all_expected_sections(self, client):
        resp = client.get("/api/admin/status")
        assert resp.status_code == 200
        body = resp.json()
        for key in ("stats", "staleness", "sync_history", "ter_sync_history", "cost_coverage", "ter_backfill_status", "backfill_status", "ter_portal_url"):
            assert key in body
        assert body["stats"]["schemes_count"] == 2
        assert body["cost_coverage"]["total_schemes"] == 2


class TestManualActions:
    def test_trigger_daily_sync_success(self, client, monkeypatch):
        monkeypatch.setattr(amfi_sync, "sync_daily_nav", lambda _trigger="manual": (True, "Synced 5 schemes"))
        resp = client.post("/api/admin/sync/daily")
        assert resp.status_code == 200
        assert resp.json() == {"success": True, "message": "Synced 5 schemes"}

    def test_trigger_daily_sync_failure_is_502_not_500(self, client, monkeypatch):
        monkeypatch.setattr(amfi_sync, "sync_daily_nav", lambda _trigger="manual": (False, "AMFI portal unreachable"))
        resp = client.post("/api/admin/sync/daily")
        assert resp.status_code == 502
        assert "AMFI portal unreachable" in resp.json()["detail"]

    def test_trigger_ter_sync_success(self, client, monkeypatch):
        monkeypatch.setattr(amfi_sync, "sync_official_ter", lambda _trigger="manual": (True, "TER sync ok"))
        resp = client.post("/api/admin/sync/ter")
        assert resp.status_code == 200

    def test_recompute_summary(self, client):
        resp = client.post("/api/admin/recompute-summary")
        assert resp.status_code == 200
        assert resp.json()["success"] is True


class TestBackfillControls:
    def test_start_ter_backfill(self, client, monkeypatch):
        monkeypatch.setattr(amfi_sync, "start_ter_backfill", lambda n_months: True)
        resp = client.post("/api/admin/backfill/ter/start", json={"n_months": 6})
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_start_ter_backfill_conflict_is_409(self, client, monkeypatch):
        monkeypatch.setattr(amfi_sync, "start_ter_backfill", lambda n_months: False)
        resp = client.post("/api/admin/backfill/ter/start", json={"n_months": 6})
        assert resp.status_code == 409

    def test_stop_ter_backfill(self, client, monkeypatch):
        called = {}
        monkeypatch.setattr(amfi_sync, "stop_ter_backfill", lambda: called.setdefault("stopped", True))
        resp = client.post("/api/admin/backfill/ter/stop")
        assert resp.status_code == 200
        assert called.get("stopped") is True

    def test_start_historical_backfill_defaults(self, client, monkeypatch):
        captured = {}

        def fake_start(start_year, max_chunks, resume):
            captured["start_year"] = start_year
            captured["max_chunks"] = max_chunks
            captured["resume"] = resume
            return True

        monkeypatch.setattr(amfi_sync, "start_historical_backfill", fake_start)
        resp = client.post("/api/admin/backfill/historical/start", json={})
        assert resp.status_code == 200
        # resume defaults to True: an omitted flag must never silently trigger a
        # full re-download of every date range the user already has.
        assert captured == {"start_year": 2020, "max_chunks": None, "resume": True}

    def test_start_historical_backfill_forwards_resume_false(self, client, monkeypatch):
        captured = {}
        monkeypatch.setattr(amfi_sync, "start_historical_backfill",
                            lambda start_year, max_chunks, resume: captured.setdefault("resume", resume) or True)
        resp = client.post("/api/admin/backfill/historical/start", json={"resume": False})
        assert resp.status_code == 200
        assert captured["resume"] is False

    def test_start_historical_backfill_conflict_is_409(self, client, monkeypatch):
        monkeypatch.setattr(amfi_sync, "start_historical_backfill",
                            lambda start_year, max_chunks, resume: False)
        resp = client.post("/api/admin/backfill/historical/start", json={"start_year": 2025, "max_chunks": 4})
        assert resp.status_code == 409


class TestLogsEndpoint:
    def test_get_logs_returns_captured_entries(self, client):
        import logging

        app_logging.ensure_log_capture_installed()
        logging.getLogger("test_admin_router").info("a distinctive test log line")
        resp = client.get("/api/admin/logs", params={"contains": "distinctive test log line"})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["entries"]) >= 1
        assert body["entries"][0]["message"] == "a distinctive test log line"
        assert body["total_captured"] >= 1


class TestAppLoggingCore:
    """Direct tests of app_logging.py's buffer/filter/SSE-broadcast mechanics --
    see this file's module docstring for why the SSE HTTP endpoint itself isn't
    exercised via TestClient."""

    def test_entry_matches_level_filter(self):
        entry = {"time": datetime.datetime.now(), "level": "INFO", "logger": "x", "message": "hello"}
        assert app_logging.entry_matches(entry, "INFO", "") is True
        assert app_logging.entry_matches(entry, "WARNING", "") is False

    def test_entry_matches_text_filter_checks_message_and_logger(self):
        entry = {"time": datetime.datetime.now(), "level": "INFO", "logger": "amfi_sync", "message": "hello world"}
        assert app_logging.entry_matches(entry, "INFO", "world") is True
        assert app_logging.entry_matches(entry, "INFO", "amfi") is True
        assert app_logging.entry_matches(entry, "INFO", "nomatch") is False

    def test_broadcast_delivers_to_subscribed_queue(self):
        async def run():
            loop = asyncio.get_running_loop()
            app_logging.set_main_loop(loop)
            queue = await app_logging.subscribe()
            try:
                app_logging.ensure_log_capture_installed()
                import logging

                logging.getLogger("test_admin_router").warning("broadcast probe message")
                entry = await asyncio.wait_for(queue.get(), timeout=2.0)
                assert entry["message"] == "broadcast probe message"
                assert entry["level"] == "WARNING"
            finally:
                app_logging.unsubscribe(queue)

        asyncio.run(run())

    def test_unsubscribed_queue_receives_nothing_further(self):
        async def run():
            loop = asyncio.get_running_loop()
            app_logging.set_main_loop(loop)
            queue = await app_logging.subscribe()
            app_logging.unsubscribe(queue)
            app_logging.ensure_log_capture_installed()
            import logging

            logging.getLogger("test_admin_router").info("post-unsubscribe message")
            await asyncio.sleep(0.05)
            assert queue.empty()

        asyncio.run(run())
