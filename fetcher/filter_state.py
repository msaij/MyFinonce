import os
import json
import datetime
from typing import Any, Iterable, Optional

import streamlit as st

FILTER_FILE_PATH = os.path.join(os.path.dirname(__file__), "data", "user_filters.json")

# The on-disk file is only ever used as the *seed* default for a brand-new
# browser session (so "remember my last filters" survives an app restart).
# Once a session has read a key, live reads/writes go through st.session_state
# so one open session can never have its widgets silently rewritten by
# whatever another concurrently-open session (or a later visit) saves to disk.
_DISK_CACHE: Optional[dict] = None
_SESSION_PREFIX = "_fs_"


def _load_disk_store() -> dict:
    global _DISK_CACHE
    if _DISK_CACHE is not None:
        return _DISK_CACHE
    if os.path.exists(FILTER_FILE_PATH):
        try:
            with open(FILTER_FILE_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    _DISK_CACHE = loaded
                    return _DISK_CACHE
        except Exception:
            pass
    _DISK_CACHE = {}
    return _DISK_CACHE


def _save_disk_store(store: dict) -> None:
    try:
        os.makedirs(os.path.dirname(FILTER_FILE_PATH), exist_ok=True)
        # Write atomically via temp file to avoid race conditions or half-writes
        temp_path = FILTER_FILE_PATH + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(store, f, indent=2, default=str)
        if os.path.exists(temp_path):
            os.replace(temp_path, FILTER_FILE_PATH)
    except Exception:
        pass


def get_filter(section: str, key: str, default: Any = None) -> Any:
    """
    Retrieve this session's filter value for a page/section, seeded on first
    read from the last value saved to disk (by any session).
    """
    session_key = f"{_SESSION_PREFIX}{section}.{key}"
    if session_key in st.session_state:
        return st.session_state[session_key]
    seed = _load_disk_store().get(section, {}).get(key, default)
    st.session_state[session_key] = seed
    return seed


def set_filter(section: str, key: str, value: Any) -> None:
    """
    Persist a filter value for a page/section: immediately authoritative for
    this session via st.session_state, and written to disk as the seed for
    the next new session.
    """
    session_key = f"{_SESSION_PREFIX}{section}.{key}"
    st.session_state[session_key] = value

    store = _load_disk_store()
    if section not in store:
        store[section] = {}
    if store[section].get(key) != value:
        store[section][key] = value
        _save_disk_store(store)


def reset_section(section: str) -> None:
    """
    Reset this session's saved filters for a given section, and clear them
    from the on-disk seed too.
    """
    prefix = f"{_SESSION_PREFIX}{section}."
    for k in [k for k in st.session_state.keys() if k.startswith(prefix)]:
        del st.session_state[k]

    store = _load_disk_store()
    if section in store:
        store[section] = {}
        _save_disk_store(store)


def reset_all() -> None:
    """
    Reset all saved filter preferences for this session and on disk.
    """
    for k in [k for k in st.session_state.keys() if k.startswith(_SESSION_PREFIX)]:
        del st.session_state[k]
    global _DISK_CACHE
    _DISK_CACHE = {}
    _save_disk_store(_DISK_CACHE)


def sanitize_widget_state(key: str, valid_options: Iterable[Any]) -> None:
    """
    Clears a widget's session_state value when it no longer appears in that
    widget's current `options` list — call this BEFORE instantiating a
    key'd selectbox/multiselect whose options can shrink or change between
    reruns (e.g. after a filter narrows the universe). Without this, Streamlit
    raises (selectbox) or silently keeps an invalid selection (multiselect)
    when a persisted session_state value falls outside the widget's options.

    For a multiselect (list-valued) state, only the still-valid entries are kept.
    For a scalar (selectbox) state, the whole key is cleared if invalid so the
    widget falls back to its own default/index.
    """
    if key not in st.session_state:
        return
    current = st.session_state[key]
    valid_set = set(valid_options)
    if isinstance(current, list):
        cleaned = [v for v in current if v in valid_set]
        if cleaned != current:
            st.session_state[key] = cleaned
    else:
        if current not in valid_set:
            del st.session_state[key]
