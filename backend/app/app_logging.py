"""In-memory activity-log capture, powering the Data Management page's live
"what is this site doing right now" monitor -- ported from fetcher/app_logging.py.

Design notes (read before touching this file):

- This module attaches ONE bounded ring-buffer `logging.Handler` to the ROOT
  logger. Every named logger in the app (amfi_sync, amfi_ter_client, db, ...)
  propagates up to root by default, so this captures all of them automatically
  -- nothing in amfi_sync.py/amfi_ter_client.py/db.py needs to change.

- It also raises the root logger's level to INFO, same as the original, for
  the same reason (otherwise `logger.info(...)` calls are silently dropped
  before reaching any handler).

- `ensure_log_capture_installed()` is idempotent (double-checked locking on a
  module-level flag) -- called once from main.py's startup event.

Migration addition over the original (which only ever polled
get_recent_logs() from a Streamlit st.fragment(run_every="5s")): a small
thread-safe pub/sub broadcaster so `GET /api/admin/logs/stream` (routers/admin.py)
can genuinely PUSH each new entry to connected clients via Server-Sent Events
instead of every client polling on a timer -- see the migration plan's
"faster/more efficient" callout. The ring-buffer handler's emit() runs
synchronously on WHATEVER thread produced the log line (the sync daemon
thread, a backfill worker thread, or a request-handling thread) -- asyncio
Queues are not safe to .put_nowait() from a thread other than the one running
their event loop, so the broadcast hands off via
`loop.call_soon_threadsafe(...)`, using the main event loop captured once at
FastAPI startup (see set_main_loop()).
"""

import asyncio
import collections
import datetime
import logging
import threading
from typing import Any, Deque, Dict, List, Optional, Set

_MAX_ENTRIES = 500
_buffer: Deque[Dict[str, Any]] = collections.deque(maxlen=_MAX_ENTRIES)
_buffer_lock = threading.Lock()

_installed = False
_install_lock = threading.Lock()

_LEVEL_NUMS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}

# --- SSE broadcast plumbing (new in the migration -- see module docstring) ---
_subscribers: Set["asyncio.Queue[Dict[str, Any]]"] = set()
_subscribers_lock = threading.Lock()
_main_loop: Optional[asyncio.AbstractEventLoop] = None


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Call once from main.py's startup event (`asyncio.get_running_loop()`).
    Without this, _broadcast() below silently no-ops -- the buffer still
    fills normally (unaffected), just nothing gets pushed live; the polling
    GET /api/admin/logs endpoint always works as a fallback regardless."""
    global _main_loop
    _main_loop = loop


def _broadcast(entry: Dict[str, Any]) -> None:
    if _main_loop is None:
        return
    with _subscribers_lock:
        queues = list(_subscribers)
    for q in queues:
        try:
            _main_loop.call_soon_threadsafe(q.put_nowait, entry)
        except Exception:
            pass


async def subscribe() -> "asyncio.Queue[Dict[str, Any]]":
    """Registers a new SSE client. Bounded so one slow/stuck consumer can't
    grow memory unboundedly -- a full queue just drops the oldest-pending
    push for that one client (they still have the buffer replay on connect,
    and get_recent_logs() as an always-available fallback)."""
    q: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue(maxsize=200)
    with _subscribers_lock:
        _subscribers.add(q)
    return q


def unsubscribe(q: "asyncio.Queue[Dict[str, Any]]") -> None:
    with _subscribers_lock:
        _subscribers.discard(q)


def entry_matches(entry: Dict[str, Any], min_level: Optional[str], contains: Optional[str]) -> bool:
    min_num = _LEVEL_NUMS.get((min_level or "").upper(), 0)
    if _LEVEL_NUMS.get(entry["level"], 0) < min_num:
        return False
    needle = (contains or "").strip().lower()
    if needle and needle not in entry["message"].lower() and needle not in entry["logger"].lower():
        return False
    return True


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
        _broadcast(entry)


def ensure_log_capture_installed() -> None:
    """Attaches the ring-buffer handler to the root logger, once per process."""
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
    with _buffer_lock:
        snapshot = list(_buffer)
    out: List[Dict[str, Any]] = []
    for entry in reversed(snapshot):
        if not entry_matches(entry, min_level, contains):
            continue
        out.append(entry)
        if len(out) >= limit:
            break
    return out


def buffer_size() -> int:
    with _buffer_lock:
        return len(_buffer)
