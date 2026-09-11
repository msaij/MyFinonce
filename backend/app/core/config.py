"""App-wide settings, read from environment variables.

Deliberately reuses the same env var NAMES docker-compose.yml already defines
for the Streamlit service (DUCKDB_PATH, TZ, AMFI_*) -- pydantic-settings
matches field names to env vars case-insensitively by default, so
`duckdb_path` already reads `DUCKDB_PATH` with no extra aliasing needed. See
the migration plan at ../../.claude/plans/floofy-petting-mountain.md for the
full rationale.

ENABLE_SYNC_DAEMON defaults to False on purpose: through most of the
migration, this backend is a read-only consumer of the same DuckDB file the
still-running Streamlit app owns as sole writer. Two independent sync
daemons writing the same embedded DuckDB file from two processes is exactly
the "write-write conflict" failure mode the existing WRITE_LOCK pattern
guards against *within* one process, and provides zero protection *across*
processes. Only flip this on (and stop the Streamlit container) at the
Data Management/cutover phase, when write-ownership formally transfers.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    duckdb_path: str = "../fetcher/data/mutual_funds.duckdb"
    tz: str = "Asia/Kolkata"
    amfi_download_page: str = "https://www.amfiindia.com/net-asset-value/nav-download"
    amfi_history_url: str = "https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx"
    amfi_daily_url: str = "https://portal.amfiindia.com/spages/NAVAll.txt"
    enable_sync_daemon: bool = False


settings = Settings()
