"""In-memory activity-log capture, powering the Data Management page's live
"what is this site doing right now" monitor.

Design notes (read before touching this file):

- This module attaches ONE bounded ring-buffer `logging.Handler` to the ROOT
  logger. Every named logger in the app (amfi_sync, amfi_ter_client, db, ...)
  propagates up to root by default, so this captures all of them automatically
  -- nothing in amfi_sync.py/amfi_ter_client.py/db.py needs to change.

- It also raises the root logger's level to INFO. Before this module existed,
  no logging.basicConfig()/handler existed anywhere in the app, so the root
  logger sat at Python's default WARNING and every `logger.info(...)` call
  (e.g. "AMFI TER portal: fetched N valid rows for MM-YYYY") was silently
  dropped before it ever reached a handler -- invisible both here and in
  `docker compose logs`. This fixes that as a side effect.

- Deliberately a standalone module that nothing in the sync/backfill layer
  imports. Streamlit's local-module hot-reload only re-executes files that
  actually changed (and their importers); keeping this one-directional
  (pages -> app_logging, never amfi_sync -> app_logging) means editing or
  adding this file can never touch amfi_sync.py's already-imported module
  object -- so it's safe to deploy this while a background sync/backfill
  thread from that module is actively running.

- `ensure_log_capture_installed()` is idempotent (double-checked-locking on a
  module-level flag), since Streamlit re-executes page scripts on every rerun
  and would otherwise attach a duplicate handler -> duplicate log lines.
"""

import collections
import datetime
import logging
import threading
from typing import Any, Deque, Dict, List, Optional

_MAX_ENTRIES = 500
_buffer: Deque[Dict[str, Any]] = collections.deque(maxlen=_MAX_ENTRIES)
_buffer_lock = threading.Lock()

_installed = False
_install_lock = threading.Lock()

_LEVEL_NUMS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}


class _RingBufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "time": datetime.datetime.fromtimestamp(record.created),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
        except Exception:
            return
        with _buffer_lock:
            _buffer.append(entry)


def ensure_log_capture_installed() -> None:
    """Attaches the ring-buffer handler to the root logger, once per process.
    Safe to call on every page load/rerun."""
    global _installed
    if _installed:
        return
    with _install_lock:
        if _installed:
            return
        logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        root = logging.getLogger()
        if root.level == logging.NOTSET or root.level > logging.INFO:
            root.setLevel(logging.INFO)
        root.addHandler(_RingBufferHandler())
        _installed = True
        logging.getLogger("app_logging").info("Live activity log capture installed.")


def get_recent_logs(limit: int = 150, min_level: Optional[str] = None, contains: Optional[str] = None) -> List[Dict[str, Any]]:
    """Most recent captured entries first. `contains` matches the message or logger name, case-insensitive."""
    min_num = _LEVEL_NUMS.get((min_level or "").upper(), 0)
    needle = (contains or "").strip().lower()
    with _buffer_lock:
        snapshot = list(_buffer)
    out: List[Dict[str, Any]] = []
    for entry in reversed(snapshot):
        if _LEVEL_NUMS.get(entry["level"], 0) < min_num:
            continue
        if needle and needle not in entry["message"].lower() and needle not in entry["logger"].lower():
            continue
        out.append(entry)
        if len(out) >= limit:
            break
    return out


def buffer_size() -> int:
    with _buffer_lock:
        return len(_buffer)
