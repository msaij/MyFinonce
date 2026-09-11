import datetime
import html
import os
import streamlit as st
import pandas as pd
import db
import amfi_sync
import app_logging
import date_picker
import theme

st.set_page_config(page_title="Data Management | Indian Mutual Funds", page_icon="⚡", layout="wide")
theme.inject_theme()

date_picker.render_sidebar_status(current_page="data_management")

theme.render_page_header(
    "⚡ Data Management & AMFI Synchronization",
    "Monitor sync health, database storage, and official cost-data coverage — and trigger on-demand sync from official AMFI portals."
)

app_logging.ensure_log_capture_installed()

# --- Shared data, fetched once up top since more than one tab below reads it. ---
stats = db.get_database_stats()
stale, current_max, expected = amfi_sync.is_database_stale()
sync_history = amfi_sync.get_sync_history()
ter_sync_history = amfi_sync.get_ter_sync_history()
coverage = db.get_cost_data_coverage()
ter_b_status = amfi_sync.get_ter_backfill_status()
b_status = amfi_sync.get_backfill_status()
backfill_chunks_total = len(amfi_sync.generate_backfill_chunks(2020))

tab_monitor, tab_sync, tab_coverage, tab_reference = st.tabs([
    "📡 Live Monitor", "🔄 Sync & Backfill", "🧾 Cost Coverage", "💾 Storage & Reference",
])

# ============================================================================
# TAB 1 — LIVE MONITOR: everything about "what is this site doing right now,
# and is it healthy" — the live log tail plus both sync-health scorecards.
# ============================================================================
with tab_monitor:
    st.markdown("### 📜 Live Activity Log")
    st.caption(
        "A live tail of what this app's background processes are doing right now — NAV sync, TER sync, TER "
        "backfill, the multi-year NAV backfill, summary recompute, and anything else that logs. Auto-refreshes "
        "every 5 seconds on its own; only activity emitted since this panel first loaded is captured, nothing "
        "retroactive."
    )

    @st.fragment(run_every="5s")
    def _render_activity_log():
        lc1, lc2, lc3 = st.columns([2, 3, 1])
        with lc1:
            level = st.selectbox("Minimum level", ["INFO", "WARNING", "ERROR"], index=0, key="activity_log_level")
        with lc2:
            needle = st.text_input("Filter (e.g. \"TER\", \"backfill\", \"nav\")", value="", key="activity_log_filter")
        with lc3:
            st.write("")
            st.write("")
            st.button("🔄 Refresh now", key="activity_log_refresh_btn")

        entries = app_logging.get_recent_logs(limit=150, min_level=level, contains=needle)
        total_captured = app_logging.buffer_size()
        if not entries:
            st.caption(
                "No captured entries match the current filter." if total_captured
                else "Nothing captured yet — trigger a sync/backfill action from the Sync & Backfill tab, or "
                     "wait for the next scheduled one, and activity will start appearing here."
            )
            return

        tone_map = {"ERROR": "danger", "CRITICAL": "danger", "WARNING": "warning"}
        rows_html = []
        for e in entries:
            pill = theme.render_status_pill(e["level"], level=tone_map.get(e["level"], "neutral"))
            ts = e["time"].strftime("%H:%M:%S")
            safe_logger = html.escape(e["logger"])
            safe_msg = html.escape(e["message"])
            rows_html.append(
                '<div style="display:flex; gap:10px; padding:5px 2px; border-bottom:1px solid var(--mf-border); '
                'font-size:0.8rem; align-items:baseline;">'
                f'<span style="color:var(--mf-muted); font-family:ui-monospace,monospace; flex:0 0 64px;">{ts}</span>'
                f'<span style="flex:0 0 74px;">{pill}</span>'
                f'<span style="color:var(--mf-muted); flex:0 0 120px; overflow:hidden; text-overflow:ellipsis; '
                f'white-space:nowrap;" title="{safe_logger}">{safe_logger}</span>'
                f'<span style="flex:1; word-break:break-word;">{safe_msg}</span>'
                '</div>'
            )
        st.markdown(
            '<div style="max-height:420px; overflow-y:auto; border:1px solid var(--mf-border); border-radius:10px; '
            f'padding:8px 10px;">{"".join(rows_html)}</div>',
            unsafe_allow_html=True,
        )
        st.caption(f"Showing {len(entries)} of {total_captured} captured entries (newest first) · auto-refreshes every 5s")

    _render_activity_log()

    st.markdown("---")
    st.markdown("### 🩺 NAV Sync Health")

    hc1, hc2, hc3, hc4 = st.columns(4)
    with hc1:
        if stale:
            exp_str = expected.strftime('%d-%b-%Y') if expected else "today"
            theme.render_metric_card("Freshness", "Sync Pending", f"Expected through {exp_str}", tone="mf-warn")
        else:
            latest_str = current_max.strftime('%d-%b-%Y') if current_max else "-"
            theme.render_metric_card("Freshness", "Live", f"Synced through {latest_str}", tone="mf-pos")
    with hc2:
        last_attempt = sync_history.get("last_attempt_at")
        if last_attempt:
            trigger = sync_history.get("last_attempt_trigger") or "manual"
            theme.render_metric_card("Last Sync Attempt", last_attempt.strftime("%d-%b %H:%M IST"), f"Trigger: {trigger}")
        else:
            theme.render_metric_card("Last Sync Attempt", "None yet", "Since this container started")
    with hc3:
        last_success = sync_history.get("last_success_at")
        if last_success:
            theme.render_metric_card("Last Successful Sync", last_success.strftime("%d-%b %H:%M IST"), sync_history.get("last_success_msg") or "", tone="mf-pos")
        else:
            theme.render_metric_card("Last Successful Sync", "None yet", "Since this container started")
    with hc4:
        total_syncs = sync_history.get("total_syncs", 0)
        total_failures = sync_history.get("total_failures", 0)
        fail_tone = "mf-warn" if total_failures > 0 else ""
        theme.render_metric_card(
            "Sync Attempts (This Session)", f"{total_syncs}",
            f"{total_failures} failed — self-retried hourly" if total_failures else "0 failed",
            tone=fail_tone,
        )

    if sync_history.get("last_error") and sync_history.get("last_attempt_at") == sync_history.get("last_failure_at"):
        theme.render_banner(
            f"⚠️ <b>Most recent sync attempt failed:</b> {sync_history['last_error']}. "
            "The hourly heartbeat and the next scheduled sync (00:05 / 23:30 IST) will retry automatically — "
            "no action needed unless this keeps failing.",
            level="warning",
        )

    st.caption(
        "Automated schedule: every night at **00:05 IST** and **23:30 IST**, plus an **hourly heartbeat** that "
        "catches up automatically if any run is missed or fails — including auto-filling multi-day gaps."
    )

    st.markdown("---")
    st.markdown("### 🧾 Official TER Sync Health")
    st.caption("A separate, daily sync against AMFI's own TER-disclosure portal — different source, different schedule, tracked independently of the NAV sync above.")

    tc1, tc2, tc3 = st.columns(3)
    with tc1:
        ter_last_attempt = ter_sync_history.get("last_attempt_at")
        if ter_last_attempt:
            ter_trigger = ter_sync_history.get("last_attempt_trigger") or "manual"
            theme.render_metric_card("Last TER Sync Attempt", ter_last_attempt.strftime("%d-%b %H:%M IST"), f"Trigger: {ter_trigger}")
        else:
            theme.render_metric_card("Last TER Sync Attempt", "None yet", "Since this container started")
    with tc2:
        ter_last_success = ter_sync_history.get("last_success_at")
        if ter_last_success:
            theme.render_metric_card("Last Successful TER Sync", ter_last_success.strftime("%d-%b %H:%M IST"), ter_sync_history.get("last_success_msg") or "", tone="mf-pos")
        else:
            theme.render_metric_card("Last Successful TER Sync", "None yet", "Since this container started")
    with tc3:
        ter_total = ter_sync_history.get("total_syncs", 0)
        ter_failures = ter_sync_history.get("total_failures", 0)
        theme.render_metric_card(
            "TER Sync Attempts (This Session)", f"{ter_total}",
            f"{ter_failures} failed" if ter_failures else "0 failed",
            tone="mf-warn" if ter_failures else "",
        )

    if ter_sync_history.get("last_error") and ter_sync_history.get("last_attempt_at") == ter_sync_history.get("last_failure_at"):
        theme.render_banner(
            f"⚠️ <b>Most recent official TER sync failed:</b> {ter_sync_history['last_error']}. "
            "It retries automatically at the next scheduled run (00:20 IST) or on the next container start.",
            level="warning",
        )

    st.caption("Automated schedule: on every container start, plus every night at **00:20 IST** — covers the current calendar month only; see the Sync & Backfill tab for older months.")

# ============================================================================
# TAB 2 — SYNC & BACKFILL: everything that TRIGGERS a background job, from a
# one-shot manual sync to a many-month/many-chunk background backfill.
# ============================================================================
with tab_sync:
    st.markdown("### 🔄 Manual Actions")
    col_act1, col_act2, col_act3 = st.columns(3)

    with col_act1:
        st.markdown("#### ⚡ Trigger Live AMFI Sync")
        st.caption("Downloads the latest official closing NAV file from AMFI, updates DuckDB, and re-materializes all performance metrics.")
        if st.button("⚡ Start Daily NAV Sync Now", type="primary"):
            with st.spinner("Connecting to official AMFI portal and ingesting latest closing NAVs..."):
                success, msg = amfi_sync.sync_daily_nav(_trigger="manual")
                if success:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(f"Sync failed: {msg}")

    with col_act2:
        st.markdown("#### 🧾 Trigger Official TER Sync")
        st.caption("Fetches this month's official TER disclosure from AMFI's TER portal and promotes matched schemes to 'official' status.")
        if st.button("🧾 Sync Official TER Now", type="primary"):
            with st.spinner("Connecting to AMFI's TER-disclosure portal and matching schemes..."):
                success, msg = amfi_sync.sync_official_ter(_trigger="manual")
                if success:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(f"TER sync failed: {msg}")

    with col_act3:
        st.markdown("#### 🛠️ Re-materialize Performance Table")
        st.caption(f"Recomputes all 1D/7D/30D/90D/1Y returns, 52-week High/Low, and asset-class summaries across all {stats['schemes_count']:,} schemes.")
        if st.button("🔨 Recompute Summary Table"):
            with st.spinner(f"Recomputing summary metrics across {stats['schemes_count']:,} schemes..."):
                db.refresh_summary_table()
                st.success("Summary table recomputed and indexed successfully!")
                st.rerun()

    st.markdown("---")
    st.markdown("### 📅 Historical TER Backfill")
    st.write(
        "The daily/startup TER sync above only ever covers the **current calendar month** (AMFI's TER API has no "
        "'since date' filter, so covering older months means re-requesting them explicitly). AMFI's portal reaches "
        "back to **FY2018-19** — use this to pull in older months on demand, in the background."
    )

    with st.expander("⏹️ Stop a running TER backfill", expanded=ter_b_status["is_running"]):
        st.caption(
            "Always available, even if the panel below says idle — a page-file edit can reset this display "
            "without killing the actual background worker, so this button doesn't trust that flag either."
        )
        if st.button("⏹️ Stop TER Backfill"):
            amfi_sync.stop_ter_backfill()
            st.warning("Stop signal sent. If a backfill is active, it will finish the current month and halt.")
            st.rerun()

    if ter_b_status["is_running"]:
        pct = int((ter_b_status["current_month_idx"] / max(1, ter_b_status["total_months"])) * 100) if ter_b_status["total_months"] else 0
        st.info(f"🔄 **TER Backfill In Progress**: Processing month {ter_b_status['current_month_idx']} of {ter_b_status['total_months']}: `{ter_b_status['current_month_str']}`")
        st.progress(pct / 100.0)
        if ter_b_status["last_error"]:
            st.warning(f"Note: {ter_b_status['last_error']}")
        if st.button("🔄 Refresh TER Backfill Progress"):
            st.rerun()
    else:
        if ter_b_status["finished_at"]:
            st.success("✅ TER backfill session completed.")
        col_tb1, col_tb2 = st.columns(2)
        with col_tb1:
            if st.button("📅 Backfill Last 12 Months of TER"):
                ok = amfi_sync.start_ter_backfill(n_months=12)
                if ok:
                    st.success("Started 12-month TER backfill in background!")
                    st.rerun()
                else:
                    st.warning("A TER backfill job is already running.")
        with col_tb2:
            _months_since_2018 = (datetime.date.today().year - 2018) * 12 + datetime.date.today().month + 9
            if st.button(f"🏛️ Backfill Since FY2018-19 (~{_months_since_2018} months)"):
                ok = amfi_sync.start_ter_backfill(n_months=_months_since_2018)
                if ok:
                    st.success("Started full historical TER backfill in background — this can take a while.")
                    st.rerun()
                else:
                    st.warning("A TER backfill job is already running.")

    st.markdown("---")
    st.markdown("### 🏛️ Multi-Year Historical Backfill Engine (2020 – Present)")
    st.write(
        "AMFI limits each individual HTTP download to **90 days at a time**. This automated pipeline divides the "
        f"entire timeline from **January 1, 2020 to present** into **{backfill_chunks_total} chunks of up to 89 "
        "days each**, downloads them in reverse-chronological order (most recent first), and bulk-ingests them "
        "directly into DuckDB in the background."
    )

    if b_status["is_running"]:
        pct = int((b_status["current_chunk_idx"] / max(1, b_status["total_chunks"])) * 100) if b_status["total_chunks"] else 0
        st.info(f"🔄 **Backfill In Progress**: Processing chunk {b_status['current_chunk_idx']} of {b_status['total_chunks']}: `{b_status['current_chunk_str']}`")
        st.progress(pct / 100.0)
        st.write(f"• Total NAV records added this session: **{b_status['records_added']:,}**")
        if b_status["last_error"]:
            st.warning(f"Note: {b_status['last_error']}")

        col_stop, col_refresh = st.columns([1, 4])
        with col_stop:
            if st.button("⏹️ Pause / Stop Backfill"):
                amfi_sync.stop_historical_backfill()
                st.warning("Stop signal sent. Worker will finish current chunk and halt.")
                st.rerun()
        with col_refresh:
            if st.button("🔄 Refresh Progress"):
                st.rerun()
    else:
        if b_status["finished_at"]:
            st.success(f"✅ Backfill session completed! Ingested {b_status['records_added']:,} historical NAV records.")

        col_b1, col_b2 = st.columns(2)
        with col_b1:
            if st.button("🚀 Backfill Past 1 Year (4 Quarters)", type="secondary"):
                ok = amfi_sync.start_historical_backfill(start_year=2025, max_chunks=4)
                if ok:
                    st.success("Started 1-Year historical backfill in background!")
                    st.rerun()
                else:
                    st.warning("A backfill job is already running.")
        with col_b2:
            if st.button(f"🏛️ Start Full Backfill Since 2020 ({backfill_chunks_total} Chunks)", type="primary"):
                ok = amfi_sync.start_historical_backfill(start_year=2020)
                if ok:
                    st.success(f"Started 2020–{datetime.date.today().year} full historical backfill in background!")
                    st.rerun()
                else:
                    st.warning("A backfill job is already running.")

# ============================================================================
# TAB 3 — COST COVERAGE: TER/exit-load status breakdown and the manual CSV
# import path for whatever the automated match can't cover.
# ============================================================================
with tab_coverage:
    st.markdown("### 🧾 Official TER & Exit-Rule Coverage")
    theme.render_banner(
        "<b>Does sync update TER or exit-load data?</b> Exit-load: no — no AMFI feed of any kind discloses scheme-specific exit-load "
        "terms, so it stays unavailable until you import an official, dated CSV row for it below. <b>TER: yes, automatically, as of "
        "this build.</b> AMFI's NAV feeds still never carry TER — but a <i>separate</i> AMFI portal "
        f"(<a href='{amfi_sync.TER_PORTAL_PAGE_URL}' target='_blank'>ter-of-mf-schemes</a>) publishes a dated, SEBI Regulation 66 "
        "expense-ratio disclosure every day, and this dashboard now syncs it on its own daily schedule (see the Live Monitor tab), "
        "matching schemes by an unambiguous normalized-name check and promoting them straight to <i>official</i> "
        "status — no manual import needed. A scheme only stays on the bundled <i>legacy, unverified</i> fallback if that automated "
        "match can't be made confidently. Manual CSV import still exists for exit-load and for anything the automated match misses.",
        level="info",
    )

    total = max(1, coverage["total_schemes"])

    cov1, cov2 = st.columns(2)
    with cov1:
        st.markdown("**Expense Ratio (TER) Status**")
        ter_official_manual = max(0, coverage["ter_official"] - coverage["ter_auto_synced"])
        df_ter = pd.DataFrame([
            {"Status": "Official — auto-synced (AMFI TER portal)", "Schemes": coverage["ter_auto_synced"], "Share %": round(coverage["ter_auto_synced"] / total * 100, 1)},
            {"Status": "Official — manually imported", "Schemes": ter_official_manual, "Share %": round(ter_official_manual / total * 100, 1)},
            {"Status": "Legacy (unverified name match)", "Schemes": coverage["ter_legacy"], "Share %": round(coverage["ter_legacy"] / total * 100, 1)},
            {"Status": "Unknown", "Schemes": coverage["ter_unknown"], "Share %": round(coverage["ter_unknown"] / total * 100, 1)},
        ])
        st.dataframe(
            df_ter,
            column_config={"Share %": st.column_config.NumberColumn(format="%.1f %%"), "Schemes": st.column_config.NumberColumn(format="%d")},
            hide_index=True, width="stretch",
        )
    with cov2:
        st.markdown("**Exit-Load Rule Status**")
        df_exit = pd.DataFrame([
            {"Status": "Official (dated, sourced)", "Schemes": coverage["exit_official"], "Share %": round(coverage["exit_official"] / total * 100, 1)},
            {"Status": "Unknown", "Schemes": coverage["exit_unknown"], "Share %": round(coverage["exit_unknown"] / total * 100, 1)},
        ])
        st.dataframe(
            df_exit,
            column_config={"Share %": st.column_config.NumberColumn(format="%.1f %%"), "Schemes": st.column_config.NumberColumn(format="%d")},
            hide_index=True, width="stretch",
        )

    st.markdown("---")
    with st.expander("📥 Import Official TER / Exit-Rule CSV"):
        st.write(
            "Import only records keyed by AMFI scheme code and backed by a dated official AMFI/AMC source. "
            "Unverified rows are rejected and never converted into estimated costs."
        )
        st.code(
            'scheme_code,expense_ratio,ter_as_of_date,ter_source_url,ter_source,exit_rule_json,exit_rule_as_of_date,exit_rule_source_url,exit_rule_source,exit_load_description,lock_in_years\n'
            '123456,0.42,2026-09-01,https://example-amc.in/ter,AMC TER disclosure,"{\"rules\":[{\"start_day\":0,\"end_day\":365,\"rate_pct\":1.0},{\"start_day\":366,\"rate_pct\":0.0}]}",2026-09-01,https://example-amc.in/sid,Scheme SID,"1% through day 365",0',
            language="csv",
        )
        st.caption("Each TER or exit-rule value needs its own as-of date and source URL. The exit-rule JSON supports tiered rates and optional free-unit allowances.")

        cost_upload = st.file_uploader("Upload dated official cost CSV", type=["csv"], key="official_cost_upload")
        if cost_upload is not None:
            if st.button("Import Official Cost Records", type="primary"):
                try:
                    import_result = db.import_official_cost_records(pd.read_csv(cost_upload))
                    if import_result["imported"]:
                        st.success(f"Imported {import_result['imported']:,} scheme cost records.")
                    if import_result["rejected"]:
                        st.warning(f"Rejected {import_result['rejected']:,} rows. Review the source/date requirements.")
                    if import_result["errors"]:
                        st.code("\n".join(import_result["errors"][:20]))
                    if import_result["imported"]:
                        st.rerun()
                except Exception as exc:
                    st.error(f"Cost import failed: {exc}")

# ============================================================================
# TAB 4 — STORAGE & REFERENCE: mostly-static facts, checked far less often
# than the tabs above.
# ============================================================================
with tab_reference:
    st.markdown("### 💾 Database Storage")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        theme.render_metric_card("DuckDB Storage Size", f"{stats['file_size_mb']} MB", "")
    with c2:
        theme.render_metric_card("Total Mutual Funds", f"{stats['schemes_count']:,}", f"Across {stats['amc_count']} fund houses")
    with c3:
        theme.render_metric_card("Historical NAV Records", f"{stats['nav_count']:,}", "")
    with c4:
        date_range_str = f"{stats['min_date']} to {stats['max_date']}" if stats['max_date'] else "N/A"
        theme.render_metric_card("Historical Data Span", date_range_str, "")

    st.markdown("---")
    with st.expander("🏛 Official AMFI Endpoints & Architecture Reference", expanded=True):
        col_ref1, col_ref2 = st.columns(2)
        with col_ref1:
            st.markdown("""
                #### Data Sources
                - **Daily Master NAV Feed**:
                  `https://portal.amfiindia.com/spages/NAVAll.txt`
                - **90-Day Historical Reports**:
                  `https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx`
                - **AMFI Official Download Portal**:
                  `https://www.amfiindia.com/net-asset-value/nav-download`
                - **Official TER Disclosure Portal** (Regulation 66):
                  `https://www.amfiindia.com/ter-of-mf-schemes`
            """)
        with col_ref2:
            st.markdown(f"""
                #### Storage & Scheduler Details
                - **Database**: Embedded DuckDB (`{stats['db_path']}`)
                - **Automated NAV Schedule**: Every night at `00:05 IST` and `23:30 IST`, plus an hourly heartbeat catch-up.
                - **Automated TER Schedule**: On every container start, plus every night at `00:20 IST` (current month only).
                - **Concurrency**: All sync/backfill/import writes are serialized behind a shared lock so a manual action can never race the background daemon.
                - **Precision**: Strict 4-decimal precision across all NAV records (`DECIMAL(14,4)`) and return percentages.
                - **Zero Third-Party Dependency**: Operates directly with official regulatory data feeds.
            """)
