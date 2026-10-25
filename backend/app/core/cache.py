"""Replacement for Streamlit's `@st.cache_data`, for a single-process FastAPI
backend. See the migration plan's "cache replacement" section
(../../../.claude/plans/floofy-petting-mountain.md).

`cachetools.TTLCache` directly rather than a framework like fastapi-cache2 --
that brings a Redis-backend abstraction this single-process, single-user app
doesn't need. One 600s TTL convention everywhere, matching what the
Streamlit app already used (_CACHE_TTL in fetcher/db.py).

`db.connection.bump_data_version()` calls `clear_all_caches()` where the
original called `st.cache_data.clear()` -- same blunt "wipe every cache on
any write" behavior, preserved on purpose.

Thread safety: TTLCache is not thread-safe, and sync routes run in a threadpool, so each
cache has its own lock. A lock is held only around the dict operations, never while the
wrapped function computes, so two caches can never wait on each other. A generation
counter keeps a computation that started before a `clear_all_caches()` (a sync landing
mid-request) from storing its pre-sync result afterwards.

Single flight: when several requests miss the same key at once, the first computes and
the others wait for its result instead of each running the same query. After a ledger
write the page refetches several views together; each used to build the same holdings
timeseries in parallel, all CPU-bound pandas under one GIL. A waiter gives up after
_FLIGHT_WAIT_SECONDS and computes on its own, and if the first computation raises, each
waiter computes on its own too -- so a failure is never handed to a request that did not
cause it, and no wait can hang a request.
"""

import functools
import threading
from typing import Any, Callable, Dict

import pandas as pd
from cachetools import TTLCache


_FLIGHT_WAIT_SECONDS = 120


class NoCache:
    """Return `NoCache(result)` from a cached function to hand `result` back without storing
    it: a partial answer computed while something it depends on failed (a peer-return query
    timing out, say), which must not stand for the whole TTL once that has recovered."""

    __slots__ = ("value",)

    def __init__(self, value: Any) -> None:
        self.value = value


def _unwrap(result: Any) -> Any:
    return result.value if isinstance(result, NoCache) else result


class _Flight:
    """One computation of a key in progress, and its outcome for whoever waited on it."""

    __slots__ = ("done", "ok", "result")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.ok = False
        self.result: Any = None


class _LockedTTLCache(TTLCache):
    """A TTLCache carrying the lock that guards it, and its computations in flight."""

    def __init__(self, maxsize: int, ttl: int) -> None:
        super().__init__(maxsize=maxsize, ttl=ttl)
        self.lock = threading.Lock()
        self.inflight: Dict[Any, _Flight] = {}


_registries: list[_LockedTTLCache] = []
#: Bumped by every clear_all_caches(); a result computed under an older value is not stored.
_generation = 0
_generation_lock = threading.Lock()


def _stable_key(args: tuple, kwargs: dict) -> tuple:
    """Cache key from positional/keyword args, skipping any keyword whose name
    starts with "_" -- mirrors the original app's Streamlit-cache convention
    for deliberately excluding an argument that's redundant with other, already-hashed arguments (often
    because it's an unhashable DataFrame) from the cache key."""
    filtered_kwargs = {k: v for k, v in kwargs.items() if not k.startswith("_")}
    return (args, tuple(sorted(filtered_kwargs.items())))


def _is_empty_frame(result: Any) -> bool:
    # Several cached queries in db/queries.py turn a database error into an empty frame.
    # Cached, that hid a single timeout for the whole TTL after Postgres recovered. A
    # legitimately empty result is a cheap early return, so recomputing it costs little.
    return isinstance(result, (pd.DataFrame, pd.Series)) and result.empty


def cached(ttl: int = 600, maxsize: int = 256) -> Callable:
    cache = _LockedTTLCache(maxsize=maxsize, ttl=ttl)
    _registries.append(cache)

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            key = _stable_key(args, kwargs)
            try:
                with cache.lock:
                    try:
                        return cache[key]
                    except KeyError:
                        pass
                    flight = cache.inflight.get(key)
                    leader = flight is None
                    if leader:
                        flight = cache.inflight[key] = _Flight()
            except TypeError:
                # An unhashable positional arg slipped through -- don't cache,
                # just call the function directly rather than erroring the request.
                return _unwrap(fn(*args, **kwargs))
            if not leader:
                if flight.done.wait(_FLIGHT_WAIT_SECONDS) and flight.ok:
                    return flight.result
                return _unwrap(fn(*args, **kwargs))
            gen = _generation
            try:
                result = fn(*args, **kwargs)
            except BaseException:
                with cache.lock:
                    cache.inflight.pop(key, None)
                flight.done.set()
                raise
            keep = not isinstance(result, NoCache)
            result = _unwrap(result)
            with cache.lock:
                # Compared under this cache's lock: clear_all_caches() bumps the generation
                # before it takes the lock to clear, so a store either sees the new
                # generation or is wiped by that clear.
                if keep and gen == _generation and not _is_empty_frame(result):
                    try:
                        cache[key] = result
                    except TypeError:
                        pass
                cache.inflight.pop(key, None)
            flight.result, flight.ok = result, True
            flight.done.set()
            return result

        def cache_clear() -> None:
            with cache.lock:
                cache.clear()

        wrapper.cache_clear = cache_clear  # type: ignore[attr-defined]
        return wrapper

    return decorator


def clear_all_caches() -> None:
    global _generation
    with _generation_lock:
        _generation += 1
    for cache in _registries:
        with cache.lock:
            cache.clear()
