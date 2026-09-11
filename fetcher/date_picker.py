import datetime
import textwrap
import streamlit as st
import pandas as pd
import db
import filter_state
import theme

PRESET_OPTIONS = [
    "Past 90 Days (3M)",
    "Past 30 Days (1M)",
    "Past 7 Days (1W)",
    "Past 180 Days (6M)",
    "Past 1 Year (12M)",
    "Since 2020",
    "All Available",
    "Custom Range"
]

def _get_db_bounds():
    try:
        stats = db.get_database_stats()
        db_max = stats.get('max_date') or datetime.date.today()
        db_min = stats.get('min_date') or datetime.date(2000, 1, 1)
        if isinstance(db_max, str):
            db_max = pd.to_datetime(db_max).date()
        elif isinstance(db_max, datetime.datetime):
            db_max = db_max.date()
        if isinstance(db_min, str):
            db_min = pd.to_datetime(db_min).date()
        elif isinstance(db_min, datetime.datetime):
            db_min = db_min.date()
        return db_min, db_max
    except Exception:
        return datetime.date(2000, 1, 1), datetime.date.today()

def _range_for_preset(choice: str, db_min: datetime.date, db_max: datetime.date):
    """Computes (start, end) for a relative preset against the given bounds. Returns None for Custom Range."""
    if choice == "Past 7 Days (1W)":
        return max(db_min, db_max - datetime.timedelta(days=7)), db_max
    elif choice == "Past 30 Days (1M)":
        return max(db_min, db_max - datetime.timedelta(days=30)), db_max
    elif choice == "Past 90 Days (3M)":
        return max(db_min, db_max - datetime.timedelta(days=90)), db_max
    elif choice == "Past 180 Days (6M)":
        return max(db_min, db_max - datetime.timedelta(days=180)), db_max
    elif choice == "Past 1 Year (12M)":
        return max(db_min, db_max - datetime.timedelta(days=365)), db_max
    elif choice == "Since 2020":
        return max(db_min, datetime.date(2020, 1, 1)), db_max
    elif choice == "All Available":
        return db_min, db_max
    return None


def init_date_state():
    current_version = db.get_data_version()
    seen_version = st.session_state.get("_data_version")
    bounds_missing = "db_max_date" not in st.session_state or "db_min_date" not in st.session_state

    if bounds_missing or seen_version != current_version:
        prev_db_max = st.session_state.get("db_max_date")
        db_min, db_max = _get_db_bounds()
        st.session_state["db_max_date"] = db_max
        st.session_state["db_min_date"] = db_min
        st.session_state["_data_version"] = current_version

        # If new data arrived (db_max moved forward) and the active window was still tracking
        # the live edge on a relative preset, seamlessly slide it forward to include the new data.
        was_tracking_live = prev_db_max is not None and st.session_state.get("active_end") == prev_db_max
        preset_in_use = st.session_state.get("time_preset", "Past 90 Days (3M)")
        if not bounds_missing and was_tracking_live and db_max != prev_db_max:
            recomputed = _range_for_preset(preset_in_use, db_min, db_max)
            if recomputed:
                new_start, new_end = recomputed
                st.session_state["active_start"] = new_start
                st.session_state["active_end"] = new_end
                st.session_state["cal_range_widget"] = (new_start, new_end)
                filter_state.set_filter("global", "active_start", str(new_start))
                filter_state.set_filter("global", "active_end", str(new_end))
    else:
        db_min = st.session_state["db_min_date"]
        db_max = st.session_state["db_max_date"]

    saved_preset = filter_state.get_filter("global", "time_preset", "Past 90 Days (3M)")
    saved_start_str = filter_state.get_filter("global", "active_start", None)
    saved_end_str = filter_state.get_filter("global", "active_end", None)

    saved_start = None
    saved_end = None
    if saved_start_str and saved_end_str:
        try:
            saved_start = pd.to_datetime(saved_start_str).date()
            saved_end = pd.to_datetime(saved_end_str).date()
        except Exception:
            pass

    if "active_end" not in st.session_state:
        st.session_state["active_end"] = saved_end if saved_end else db_max
    if "active_start" not in st.session_state:
        st.session_state["active_start"] = saved_start if saved_start else max(db_min, db_max - datetime.timedelta(days=90))
    if "time_preset" not in st.session_state:
        st.session_state["time_preset"] = saved_preset

    if "cal_range_widget" not in st.session_state:
        st.session_state["cal_range_widget"] = (st.session_state["active_start"], st.session_state["active_end"])
    if "time_preset_widget" not in st.session_state:
        st.session_state["time_preset_widget"] = st.session_state["time_preset"]


def _on_preset_change():
    choice = st.session_state.get("time_preset_widget", "Past 90 Days (3M)")
    db_max = st.session_state.get("db_max_date", datetime.date.today())
    db_min = st.session_state.get("db_min_date", datetime.date(2000, 1, 1))

    st.session_state["time_preset"] = choice
    filter_state.set_filter("global", "time_preset", choice)

    recomputed = _range_for_preset(choice, db_min, db_max)
    if not recomputed:
        return
    new_start, new_end = recomputed

    st.session_state["active_start"] = new_start
    st.session_state["active_end"] = new_end
    st.session_state["cal_range_widget"] = (new_start, new_end)
    filter_state.set_filter("global", "active_start", str(new_start))
    filter_state.set_filter("global", "active_end", str(new_end))


def _on_calendar_change():
    cal = st.session_state.get("cal_range_widget")
    if isinstance(cal, (tuple, list)) and len(cal) == 2:
        d_start = min(cal[0], cal[1])
        d_end = max(cal[0], cal[1])
        st.session_state["active_start"] = d_start
        st.session_state["active_end"] = d_end
        st.session_state["time_preset"] = "Custom Range"
        st.session_state["time_preset_widget"] = "Custom Range"
        filter_state.set_filter("global", "time_preset", "Custom Range")
        filter_state.set_filter("global", "active_start", str(d_start))
        filter_state.set_filter("global", "active_end", str(d_end))


def get_active_date_range():
    init_date_state()
    return st.session_state["active_start"], st.session_state["active_end"]


def _watch_for_background_sync():
    """
    Polls the shared data-version counter every 60s so an already-open tab notices when the
    background daemon (or another session) syncs new NAVs, and reruns to pick it up automatically.
    """
    # Uses its own bookkeeping key (separate from init_date_state's "_data_version") so it works
    # even on pages that never call init_date_state, and never fights over who "consumes" a change.
    latest_version = db.get_data_version()
    watcher_seen = st.session_state.get("_watcher_seen_version")
    if watcher_seen != latest_version:
        st.session_state["_watcher_seen_version"] = latest_version
        if watcher_seen is not None:
            try:
                st.rerun(scope="app")
            except TypeError:
                st.rerun()


if hasattr(st, "fragment"):
    _watch_for_background_sync = st.fragment(run_every=60)(_watch_for_background_sync)


def render_sidebar_status(current_page: str = None, page_context: dict = None):
    """
    Renders a modern, institutional sidebar with progressive disclosure and dynamic page awareness.
    Completely eliminates cross-page duplications, redundant navigation links, and screen clutter.
    """
    import amfi_sync

    # Ensure background sync daemon is active regardless of page landing
    amfi_sync.ensure_sync_daemon_running()

    try:
        _watch_for_background_sync()
    except Exception:
        pass

    # 1. Clean Brand Header & Live Feed Status Pill
    st.sidebar.markdown("##### 🇮🇳 AMFI Analytics")
    try:
        stale, cur_max, expected = amfi_sync.is_database_stale()
        if stale:
            exp_str = expected.strftime('%d-%b-%Y') if expected else 'Today'
            pill_html = theme.render_status_pill(f"Sync Pending — expected {exp_str}", level="warning")
        else:
            latest_str = cur_max.strftime('%d-%b-%Y') if cur_max else 'Live'
            pill_html = theme.render_status_pill(f"Live Feed Active — synced to {latest_str}", level="success")
    except Exception:
        pill_html = theme.render_status_pill("DuckDB Engine Active", level="neutral")
    st.sidebar.markdown(pill_html, unsafe_allow_html=True)

    # 2. Active Workspace Context — one compact card instead of a stack of captions/dividers
    active_start = st.session_state.get("active_start")
    active_end = st.session_state.get("active_end")
    time_preset = st.session_state.get("time_preset", "Past 90 Days (3M)")
    range_line = ""
    if active_start and active_end:
        range_line = f"<div style='font-size:0.78rem; color:var(--mf-muted); margin-top:2px;'>{active_start.strftime('%d-%b-%Y')} → {active_end.strftime('%d-%b-%Y')}</div>"
    st.sidebar.markdown(
        f"""
        <div class="filter-box" style="padding:10px 12px; margin:10px 0 8px 0;">
            <div style="font-size:0.68rem; text-transform:uppercase; letter-spacing:0.06em; color:var(--mf-muted); font-weight:700;">Active Time Horizon</div>
            <div style="font-weight:700; margin-top:2px;">{time_preset}</div>
            {range_line}
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Page-Specific Contextual Details (one card, page-dependent label/value)
    ctx_label, ctx_value, ctx_sub = None, None, None
    if page_context:
        if current_page == "quant" and "scheme_name" in page_context:
            ctx_label, ctx_value = "🎯 Inspected Scheme", page_context["scheme_name"]
            if "benchmark" in page_context:
                ctx_sub = f"Benchmark: {page_context['benchmark']}"
        elif current_page == "screener" and "category" in page_context:
            ctx_label = "🔍 Active Filter"
            count_str = f" ({page_context['count']:,} funds)" if "count" in page_context else ""
            ctx_value = f"{page_context['category']}{count_str}"
        elif current_page == "compare_simulate" and "count" in page_context:
            _n = page_context['count']
            ctx_label, ctx_value = "⚖️ Selected Funds", f"{_n} fund{'s' if _n != 1 else ''} selected"

    if ctx_label:
        sub_html = f"<div style='font-size:0.75rem; color:var(--mf-muted); margin-top:2px;'>{ctx_sub}</div>" if ctx_sub else ""
        st.sidebar.markdown(
            f"""
            <div class="filter-box" style="padding:10px 12px; margin-bottom:8px;">
                <div style="font-size:0.68rem; text-transform:uppercase; letter-spacing:0.06em; color:var(--mf-muted); font-weight:700;">{ctx_label}</div>
                <div style="font-weight:600; margin-top:2px;">{ctx_value}</div>
                {sub_html}
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.sidebar.divider()

    # 3. Quick Sync Action (Intelligently suppressed on Data Management to avoid duplication)
    if current_page != "data_management":
        if st.sidebar.button("🔄 Sync Closing NAVs Now", key="sidebar_quick_sync_btn", use_container_width=True):
            with st.spinner("Fetching closing NAVs from official AMFI portal..."):
                success, msg = amfi_sync.sync_daily_nav(_trigger="manual")
                if success:
                    st.sidebar.success(msg)
                    st.rerun()
                else:
                    st.sidebar.error(msg)

    # 4. Progressive Disclosure: Collapsible Telemetry & Storage Drawer
    with st.sidebar.expander("💾 Database Telemetry", expanded=False):
        try:
            stats = db.get_database_stats()
            st.write(f"• **Tracked Schemes**: `{stats['schemes_count']:,}`")
            st.write(f"• **Historical NAVs**: `{stats['nav_count']:,}`")
            st.write(f"• **AMCs (Fund Houses)**: `{stats['amc_count']:,}`")
            st.write(f"• **Data Coverage**: `{stats['min_date']}` to `{stats['max_date']}`")
            st.write(f"• **Storage Footprint**: `{stats['file_size_mb']} MB`")
            st.write(f"• **Engine**: `DuckDB (Columnar OLAP)`")
        except Exception:
            st.info("• Database: DuckDB Connected")


def render_top_date_picker(current_page: str = None, page_context: dict = None):
    """
    Renders the unified single time window picker on the top-right of the page (beside Deploy).
    Strictly the ONLY place to configure time horizon and date ranges in the entire application.
    """
    init_date_state()
    
    # Render persistent sidebar status on every page
    render_sidebar_status(current_page=current_page, page_context=page_context)
    
    if "time_preset_widget" not in st.session_state or st.session_state["time_preset_widget"] not in PRESET_OPTIONS:
        st.session_state["time_preset_widget"] = st.session_state.get("time_preset", "Past 90 Days (3M)")
        
    cal_val = st.session_state.get("cal_range_widget")
    if not isinstance(cal_val, (tuple, list)) or len(cal_val) != 2:
        cal_val = (st.session_state["active_start"], st.session_state["active_end"])
        st.session_state["cal_range_widget"] = cal_val
        
    c_preset, c_cal = st.columns([1.1, 1.4])
    
    with c_preset:
        st.selectbox(
            "Time Horizon",
            options=PRESET_OPTIONS,
            key="time_preset_widget",
            on_change=_on_preset_change,
            label_visibility="collapsed"
        )
        
    db_min = st.session_state.get("db_min_date", datetime.date(2000, 1, 1))
    db_max = st.session_state.get("db_max_date", datetime.date.today())
    act_start = st.session_state.get("active_start", db_min)
    act_end = st.session_state.get("active_end", db_max)

    cal_min = min(datetime.date(2000, 1, 1), db_min, act_start)
    if "cal_range_widget" in st.session_state and isinstance(st.session_state["cal_range_widget"], (tuple, list)) and len(st.session_state["cal_range_widget"]) > 0:
        c_min = min(st.session_state["cal_range_widget"])
        if c_min < cal_min:
            cal_min = c_min

    cal_max = max(datetime.date.today(), db_max, act_end)
    if "cal_range_widget" in st.session_state and isinstance(st.session_state["cal_range_widget"], (tuple, list)) and len(st.session_state["cal_range_widget"]) > 0:
        c_max = max(st.session_state["cal_range_widget"])
        if c_max > cal_max:
            cal_max = c_max

    with c_cal:
        st.date_input(
            "Calendar Range",
            min_value=cal_min,
            max_value=cal_max,
            key="cal_range_widget",
            on_change=_on_calendar_change,
            label_visibility="collapsed"
        )
        
    db_min_str = db_min.strftime('%d-%b-%Y') if hasattr(db_min, 'strftime') else str(db_min)
    db_max_str = db_max.strftime('%d-%b-%Y') if hasattr(db_max, 'strftime') else str(db_max)
    act_start_str = st.session_state['active_start'].strftime('%d-%b-%Y')
    act_end_str = st.session_state['active_end'].strftime('%d-%b-%Y')
    span = (st.session_state["active_end"] - st.session_state["active_start"]).days
    
    if st.session_state['active_start'] < db_min:
        st.caption(f"⚠️ **Selected**: {act_start_str} to {act_end_str} ({span}D) | **Local data begins**: `{db_min_str}` (Backfill needed for earlier)")
    else:
        st.caption(f"🟢 **Synced Through**: `{db_max_str}` ({db_min_str} to {db_max_str}) | **Active**: {act_start_str} to {act_end_str} ({span}D)")

