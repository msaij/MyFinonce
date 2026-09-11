"""App-wide settings, read from environment variables.

DB_PATH points at this backend's own SQLite file -- a clean-slate database,
entirely separate from fetcher/'s old DuckDB file (no data was migrated; the
AMFI sync repopulates it from scratch). Because it's a file this backend
alone owns, ENABLE_SYNC_DAEMON defaults to True: unlike the old DuckDB setup,
there is no second process that could ever contend for this specific file.

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
    enable_sync_daemon: bool = True


settings = Settings()
