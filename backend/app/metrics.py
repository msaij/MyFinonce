"""In-process counters for ops health (log-derived; single worker)."""

from __future__ import annotations

import threading

_lock = threading.Lock()
_counters: dict[str, int] = {
    "quant_factors_unavailable_total": 0,
    "suggest_holdout_fallback_is_total": 0,
    "amfi_sync_success_nav": 0,
    "amfi_sync_success_ter": 0,
}


def incr(name: str, n: int = 1) -> None:
    with _lock:
        _counters[name] = _counters.get(name, 0) + n


def get_all() -> dict[str, int]:
    with _lock:
        return dict(_counters)
