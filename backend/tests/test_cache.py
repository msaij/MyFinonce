"""Tests for app/core/cache.py: pure, no database."""

import random
import threading
import time

import pandas as pd

from app.core import cache


def test_a_result_is_served_from_the_cache():
    calls = []

    @cache.cached(ttl=60)
    def f(x):
        calls.append(x)
        return pd.DataFrame({"x": [x]})

    assert f(1).equals(f(1))
    assert calls == [1]


def test_an_empty_frame_is_recomputed_not_cached():
    """Several queries turn a database error into an empty frame; cached, one timeout
    blanked a panel for the whole TTL after Postgres recovered."""
    calls = []

    @cache.cached(ttl=60)
    def frame():
        calls.append(1)
        return pd.DataFrame()

    @cache.cached(ttl=60)
    def series():
        calls.append(2)
        return pd.Series(dtype=float)

    frame(), frame(), series(), series()
    assert calls == [1, 1, 2, 2]


def test_a_result_computed_across_a_clear_is_not_stored():
    """A sync that lands mid-compute must not leave the pre-sync answer cached for 600 s."""
    calls = []

    @cache.cached(ttl=60)
    def f():
        calls.append(1)
        if len(calls) == 1:
            cache.clear_all_caches()  # a sync finishing while this computes
        return {"n": len(calls)}

    assert f() == {"n": 1}
    assert f() == {"n": 2}, "the first result was computed before the clear, so it was not kept"
    assert f() == {"n": 2}
    assert calls == [1, 1]


def test_cache_clear_empties_one_cache():
    calls = []

    @cache.cached(ttl=60)
    def f():
        calls.append(1)
        return "v"

    f()
    f.cache_clear()
    f()
    assert calls == [1, 1]


def test_many_threads_on_a_small_cache_raise_nothing():
    """TTLCache is not thread-safe; unlocked, concurrent evictions raised KeyError."""

    @cache.cached(ttl=60, maxsize=4)
    def f(x):
        return x * 2

    errors = []

    def hammer(seed):
        rng = random.Random(seed)
        try:
            for _ in range(2000):
                x = rng.randrange(50)
                assert f(x) == x * 2
                if rng.random() < 0.01:
                    f.cache_clear()
        except Exception as exc:  # noqa: BLE001 -- any failure fails the test below
            errors.append(exc)

    threads = [threading.Thread(target=hammer, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


def test_an_unhashable_argument_is_computed_not_an_error():
    @cache.cached(ttl=60)
    def f(x):
        return len(x)

    assert f([1, 2, 3]) == 3


def _run_together(target, n):
    results, threads = [], []
    start = threading.Barrier(n)

    def run():
        start.wait()
        try:
            results.append(target())
        except Exception as e:  # noqa: BLE001 -- recorded for the assertion
            results.append(e)

    threads = [threading.Thread(target=run) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    return results


def test_requests_missing_the_same_key_at_once_compute_it_once():
    """After a ledger write the page refetches several views at once, and each used to
    build the same timeseries in parallel."""
    calls = []

    @cache.cached(ttl=60)
    def slow(key):
        calls.append(key)
        time.sleep(0.2)
        return {"key": key}

    results = _run_together(lambda: slow("view"), 5)
    assert calls == ["view"]
    assert results == [{"key": "view"}] * 5


def test_a_failed_computation_is_not_handed_to_the_requests_that_waited():
    calls = []

    @cache.cached(ttl=60)
    def flaky():
        calls.append(1)
        time.sleep(0.2)
        if len(calls) == 1:
            raise RuntimeError("database blip")
        return "ok"

    results = _run_together(flaky, 3)
    # One request saw the failure it caused; the others each computed for themselves.
    assert sum(isinstance(r, RuntimeError) for r in results) == 1
    assert results.count("ok") == 2
