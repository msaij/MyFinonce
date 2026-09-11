"""App-wide settings, read from environment variables.

DB_PATH points at this backend's own SQLite file -- a clean-slate database,
entirely separate from fetcher/'s old DuckDB file (no data was migrated; the
AMFI sync repopulates it from scratch).

ENABLE_SYNC_DAEMON defaults to False, NOT because of the old cross-process
DuckDB safety concern (this file has no second process that could ever
contend for it) but for a different, practical reason found the hard way:
the daemon's startup catch-up (amfi_sync.check_and_catchup_sync(), possibly
a real, long-running historical backfill against the live AMFI servers) runs
unconditionally on every app startup -- fine for the deliberate write-
ownership handoff (Phase 9 of the migration plan, where this flips true on
purpose) but actively disruptive during iterative development, where the
container gets rebuilt many times an hour: it made the whole backend
unresponsive (even /api/health timing out) for minutes after an ordinary
rebuild. Flip to true explicitly (docker-compose.yml or this default) only
when you actually want the daemon auto-starting.

AMFI_* URLs and TZ still reuse the same env var NAMES docker-compose.yml
already defines -- pydantic-settings matches field names to env vars
case-insensitively by default, so `tz` already reads `TZ` with no extra
aliasing needed.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    db_path: str = "data/mutual_funds.sqlite3"
    tz: str = "Asia/Kolkata"
    amfi_download_page: str = "https://www.amfiindia.com/net-asset-value/nav-download"
    amfi_history_url: str = "https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx"
    amfi_daily_url: str = "https://portal.amfiindia.com/spages/NAVAll.txt"
    enable_sync_daemon: bool = False


settings = Settings()
