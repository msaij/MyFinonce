"""App-wide settings, read from environment variables.

DATABASE_URL points at this backend's own PostgreSQL database -- the
application's sole database. It replaced DB_PATH (a local SQLite file) on
2026-09-12; see docker-compose.yml's postgres service for the measured reasons
that switch happened, and db/connection.py for what changed in the code. The
lineage before that was DuckDB, then SQLite; both are gone, see git history.

ENABLE_SYNC_DAEMON defaulted to False through Phase 8 of the migration --
not because of any cross-process contention (this backend has always been
the only writer of its own database) but for a different, practical reason
found the hard way: the daemon's startup catch-up
(amfi_sync.check_and_catchup_sync(), possibly a real, long-running
historical backfill against the live AMFI servers) runs unconditionally on
every app startup -- actively disruptive during iterative development,
where the container gets rebuilt many times an hour: it made the whole
backend unresponsive (even /api/health timing out) for minutes after an
ordinary rebuild.

**Now defaults to True** -- normal usage no longer means "rebuild the
container every few minutes" the way active migration development did, so
the disruption this was guarding against no longer applies day-to-day.
Override to False in docker-compose.yml for a throwaway read-only
exploration session if needed.

AMFI_* URLs and TZ still reuse the same env var NAMES docker-compose.yml
already defines -- pydantic-settings matches field names to env vars
case-insensitively by default, so `tz` already reads `TZ` with no extra
aliasing needed.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    # Default targets the compose service by hostname, so the container needs no
    # env var to work; a host-side run (or a test against a different database)
    # overrides it with DATABASE_URL.
    database_url: str = "postgresql://mf:mf_local_dev@postgres:5432/mutual_funds"
    tz: str = "Asia/Kolkata"
    amfi_download_page: str = "https://www.amfiindia.com/net-asset-value/nav-download"
    amfi_history_url: str = "https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx"
    amfi_daily_url: str = "https://portal.amfiindia.com/spages/NAVAll.txt"
    enable_sync_daemon: bool = True
    holdout_portfolios: bool = False
    bl_ui: bool = False
    amfi_ssl_insecure: bool = False
    admin_token_optional: bool = False
    admin_token: str = ""

    @property
    def admin_auth_required(self) -> bool:
        return not self.admin_token_optional

    @property
    def admin_token_configured(self) -> bool:
        return bool(self.admin_token)


settings = Settings()


def product_flags() -> dict:
    """Public flag map for /api/meta/status. Never includes ADMIN_TOKEN."""
    return {
        "holdout_portfolios": bool(settings.holdout_portfolios),
        "bl_ui": bool(settings.bl_ui),
        "admin_auth_required": bool(settings.admin_auth_required),
        "admin_token_configured": bool(settings.admin_token_configured),
        "amfi_ssl_insecure": bool(settings.amfi_ssl_insecure),
    }


def amfi_ssl_context():
    import ssl

    ctx = ssl.create_default_context()
    if settings.amfi_ssl_insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx
