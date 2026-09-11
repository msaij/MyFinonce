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
"""

import functools
from typing import Any, Callable

from cachetools import TTLCache

_registries: list[TTLCache] = []


def _stable_key(args: tuple, kwargs: dict) -> tuple:
    """Cache key from positional/keyword args, skipping any keyword whose name
    starts with "_" -- mirrors the original app's Streamlit-cache convention
    (e.g. `_rules_result` in the portfolio advisor) for deliberately excluding
    an argument that's redundant with other, already-hashed arguments (often
    because it's an unhashable DataFrame) from the cache key."""
    filtered_kwargs = {k: v for k, v in kwargs.items() if not k.startswith("_")}
    return (args, tuple(sorted(filtered_kwargs.items())))


def cached(ttl: int = 600, maxsize: int = 256) -> Callable:
    cache: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl)
    _registries.append(cache)

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            key = _stable_key(args, kwargs)
            try:
                return cache[key]
            except KeyError:
                pass
            except TypeError:
                # An unhashable positional arg slipped through -- don't cache,
                # just call the function directly rather than erroring the request.
                return fn(*args, **kwargs)
            result = fn(*args, **kwargs)
            try:
                cache[key] = result
            except TypeError:
                pass
            return result

        wrapper.cache_clear = cache.clear  # type: ignore[attr-defined]
        return wrapper

    return decorator


def clear_all_caches() -> None:
    for cache in _registries:
        cache.clear()
