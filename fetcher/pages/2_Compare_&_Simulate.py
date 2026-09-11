import datetime
import html
import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import db
import date_picker
import filter_state
import theme
import portfolio_sim
import costs_data
from streamlit_searchbox import st_searchbox

st.set_page_config(page_title="Compare & Simulate | Indian Mutual Funds", page_icon="📊", layout="wide")
theme.inject_theme()

DEFAULT_SCHEME_CODES = [122639, 118989]
MAX_SCHEMES = 6

col_h1, col_h2 = st.columns([3, 2])
with col_h1:
    theme.render_page_header(
        "Compare & Simulate",
        "Compare mutual fund schemes side-by-side, then backtest the same funds together as a weighted portfolio."
    )
with col_h2:
    _hint_codes = st.session_state.get("cs_schemes_widget")
    shared_ctx = {"count": len(_hint_codes)} if _hint_codes else None
    date_picker.render_top_date_picker(current_page="compare_simulate", page_context=shared_ctx)

active_start, active_end = date_picker.get_active_date_range()

# --- SHARED FUND SELECTOR (feeds both tabs below) ---
saved_schemes = filter_state.get_filter("compare_simulate", "selected_scheme_codes", default=DEFAULT_SCHEME_CODES)

with st.expander("🔍 Select Funds to Analyze (Search, Multi-Select — shared by both tabs below)", expanded=True):
    col_cs1, col_cs2 = st.columns([3.5, 1.5])

    def search_schemes_live(searchterm: str):
        if not searchterm or not searchterm.strip():
            return []
        schemes = db.get_schemes_for_dropdown(search_term=searchterm, limit=25)
        return [(s["display_label"], s["scheme_code"]) for s in schemes]

    with col_cs1:
        st.markdown("<div style='font-size: 0.88rem; font-weight: 600; margin-bottom: 2px;'>Search & Add Fund (Live Suggestions As You Type):</div>", unsafe_allow_html=True)
        live_pick = st_searchbox(
            search_schemes_live,
            placeholder="Type keywords in any order (e.g. motilal arbitrage, small cap, 118989)...",
            key="cs_live_searchbox"
        )
        if live_pick:
            code_int = int(live_pick)
            curr = list(st.session_state.get("cs_schemes_widget", saved_schemes))
            if code_int not in curr and len(curr) < MAX_SCHEMES:
                curr.append(code_int)
                st.session_state["cs_schemes_widget"] = curr
                filter_state.set_filter("compare_simulate", "selected_scheme_codes", curr)
            elif code_int not in curr:
                st.warning(f"Already have {MAX_SCHEMES} funds (the maximum). Remove one before adding another.")

    with col_cs2:
        st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
        if st.button("🔄 Reset Selection", key="cs_reset_btn", use_container_width=True):
            filter_state.set_filter("compare_simulate", "selected_scheme_codes", DEFAULT_SCHEME_CODES)
            filter_state.set_filter("compare_simulate", "investment_amount", 10000)
            filter_state.reset_section("portfolio_weights")
            filter_state.reset_section("portfolio_config")
            for k in list(st.session_state.keys()):
                if k in ("cs_schemes_widget", "cs_live_searchbox", "cs_investment_widget",
                          "cs_mode_widget", "cs_lump_widget", "cs_sip_widget", "cs_rebal_widget") or k.startswith("cs_weight_"):
                    st.session_state.pop(k, None)
            st.rerun()

    # Lightweight dropdown-label lookup (not the heavy Screener query) for whatever is
    # currently searchable/selected, plus a targeted lookup for any saved/selected code
    # that search doesn't happen to surface (e.g. an obscure fund typed by exact AMFI code).
    scheme_map = {
        s["scheme_code"]: s["display_label"]
        for s in db.get_schemes_for_dropdown(limit=5000)
    }
    active_needed = list(set((saved_schemes or []) + (st.session_state.get("cs_schemes_widget") or [])))
    for code in active_needed:
        if code not in scheme_map:
            prof, _ = db.get_scheme_profile(code)
            if prof:
                scheme_map[code] = db.format_scheme_display_name(
                    prof["scheme_name"], prof.get("plan_type"), prof.get("option_type"), code
                )

    valid_options = list(scheme_map.keys())

    # Seed the widget's state only once we know which codes are actually valid, so a
    # database that lacks the two hardcoded defaults can never crash the multiselect
    # by pre-setting a value outside its own options list.
    filter_state.sanitize_widget_state("cs_schemes_widget", valid_options)
    if "cs_schemes_widget" not in st.session_state:
        valid_saved = [c for c in saved_schemes if c in scheme_map]
        if not valid_saved and valid_options:
            valid_saved = valid_options[:2]
        st.session_state["cs_schemes_widget"] = valid_saved

    selected_scheme_codes = st.multiselect(
        f"Active Funds (Up to {MAX_SCHEMES}) — used by both the Comparison and Portfolio Backtest tabs below:",
        options=valid_options,
        format_func=lambda x: scheme_map.get(x, str(x)),
        max_selections=MAX_SCHEMES,
        key="cs_schemes_widget"
    )
    filter_state.set_filter("compare_simulate", "selected_scheme_codes", selected_scheme_codes)

if len(selected_scheme_codes) < 1:
    st.info("Please select at least 1 fund from the dropdown above to compare or simulate.")
    st.stop()

# --- SHARED NAV HISTORY FETCH ---
df_hist_raw = db.get_nav_history_dataframe(selected_scheme_codes, start_date=active_start, end_date=active_end)

if df_hist_raw.empty:
    st.warning(
        "No historical NAV records for any selected fund in this time window. "
        "Widen the time horizon at the top of the page, or pick different funds."
    )
    st.stop()

df_hist_raw["nav_date"] = pd.to_datetime(df_hist_raw["nav_date"])
df_hist_raw["nav"] = df_hist_raw["nav"].astype(float)
df_hist_raw = df_hist_raw.sort_values(["scheme_code", "nav_date"]).reset_index(drop=True)

present_codes = set(df_hist_raw["scheme_code"].unique())
missing_codes = [c for c in selected_scheme_codes if c not in present_codes]
if missing_codes:
    missing_names = [html.escape(scheme_map.get(c, str(c))) for c in missing_codes]
    theme.render_banner(
        f"⚠️ <b>No NAV data in this window:</b> {', '.join(missing_names)}. "
        f"Pick a wider time horizon, or check the scheme on the Scheme Screener page.",
        level="warning",
    )

tab_compare, tab_portfolio = st.tabs(["📋 Head-to-Head Comparison", "🧮 Portfolio Backtest"])

# ============================================================
# TAB 1 — HEAD-TO-HEAD COMPARISON
# ============================================================
with tab_compare:
    saved_investment = filter_state.get_filter("compare_simulate", "investment_amount", default=10000)
    if "cs_investment_widget" not in st.session_state:
        st.session_state["cs_investment_widget"] = int(saved_investment)
    investment_amount = st.number_input(
        "Hypothetical Investment (₹) — used for the growth chart & cost illustration below",
        min_value=1000,
        max_value=10000000,
        step=5000,
        key="cs_investment_widget"
    )
    filter_state.set_filter("compare_simulate", "investment_amount", investment_amount)

    span = (active_end - active_start).days
    win_col_title = f"Window Return % ({span}D)"

    df_cmp = df_hist_raw.copy()
    win_returns = {}
    has_partial_coverage = False
    max_actual_days = 0
    partial_coverage_names = []

    for c, g in df_cmp.groupby("scheme_code"):
        if len(g) >= 1:
            f_nav = g.iloc[0]["nav"]
            l_nav = g.iloc[-1]["nav"]
            f_date = g.iloc[0]["nav_date"].date()
            l_date = g.iloc[-1]["nav_date"].date()
            actual_days = (l_date - f_date).days
            max_actual_days = max(max_actual_days, actual_days)
            ret_val = round(((l_nav - f_nav) / f_nav) * 100.0, 4)

            if span > 100 and actual_days < int(span * 0.75):
                has_partial_coverage = True
                win_returns[c] = f"{ret_val:+.4f}% ({actual_days}D)*"
                partial_coverage_names.append(str(g.iloc[0].get("scheme_name") or c))
            else:
                win_returns[c] = f"{ret_val:+.4f}%"

    # --- 1. SIDE-BY-SIDE METRICS COMPARISON TABLE ---
    st.markdown("### 📋 Side-by-Side Metrics Comparison")
    if has_partial_coverage:
        st.warning(
            f"⚠️ **Partial Data Coverage**: You selected a **{span}-day** window, but local database records for this period currently span **{max_actual_days} days** (since {active_start.strftime('%d-%b-%Y')} is before downloaded history). "
            f"The **'{win_col_title}'** column reflects the return over the available {max_actual_days} days. "
            f"To calculate true 1-year/multi-year returns, run **'Backfill Past 1 Year'** in **⚡ Data Management**."
        )

    comp_metrics = []
    for code in selected_scheme_codes:
        prof, _ = db.get_scheme_profile(code)
        if prof:
            ter_raw = prof.get("expense_ratio")
            ter_val = float(ter_raw) if ter_raw is not None and not pd.isna(ter_raw) else None
            ter_status = prof.get("ter_status") or "unknown"

            def _ter_component(field: str) -> str:
                raw = prof.get(field)
                return f"{float(raw):.4f}%" if raw is not None and not pd.isna(raw) else "-"

            exit_status_source = prof.get("exit_rule_status") or "unknown"
            exit_desc = prof.get("exit_load_description") or "Exit-load rule unavailable."
            lock_in_raw = prof.get("lock_in_years")
            lock_in = int(lock_in_raw) if lock_in_raw is not None and not pd.isna(lock_in_raw) else None

            exit_status = "Rule available — enter transaction lots to calculate" if exit_status_source == "official" else "Rule source required"
            exit_win_str = "Transaction-lot based"

            comp_metrics.append({
                "Scheme Name": prof["scheme_name"],
                "AMFI Code": prof["scheme_code"],
                "Fund House": prof["fund_house"],
                "Category": prof["category"],
                "Plan": prof["plan_type"],
                "TER %": f"{ter_val:.4f}%" if ter_val is not None else "Unavailable",
                "TER Confidence": ter_status,
                "Base Expense Ratio %": _ter_component("ter_base_expense_ratio"),
                "Brokerage Cost %": _ter_component("ter_brokerage_cost_pct"),
                "Transaction Cost %": _ter_component("ter_transaction_cost_pct"),
                "Statutory Levies %": _ter_component("ter_statutory_levies_pct"),
                "Exit Rule": exit_desc,
                "Exit Window": exit_win_str,
                "Exit Status": exit_status,
                "Lock-in": f"{lock_in} Years" if lock_in is not None else "Not verified",
                "Latest NAV (₹)": f"₹ {prof['latest_nav']:.4f}" if prof['latest_nav'] else "-",
                win_col_title: win_returns.get(code, "-"),
                "1D Chg %": f"{prof['change_1d_pct']:+.4f}%" if prof['change_1d_pct'] is not None else "-",
                "7D Return %": f"{prof['return_7d_pct']:+.4f}%" if prof['return_7d_pct'] is not None else "-",
                "30D Return %": f"{prof['return_30d_pct']:+.4f}%" if prof['return_30d_pct'] is not None else "-",
                "90D Return %": f"{prof['return_90d_pct']:+.4f}%" if prof['return_90d_pct'] is not None else "-",
                "1Y Return %": f"{prof['return_1y_pct']:+.4f}%" if prof['return_1y_pct'] is not None else "-",
                "52W High (₹)": f"₹ {prof['high_52w']:.4f}" if prof['high_52w'] else "-",
                "52W Low (₹)": f"₹ {prof['low_52w']:.4f}" if prof['low_52w'] else "-",
                "From 52W High %": f"{prof['dist_from_52w_high_pct']:+.4f}%" if prof['dist_from_52w_high_pct'] is not None else "-",
                "ISIN": prof["isin"] or "-"
            })

    df_metrics = pd.DataFrame(comp_metrics)
    st.dataframe(df_metrics, hide_index=True, width="stretch")

    # --- 2. HISTORICAL CHARTS ---
    st.markdown("---")
    st.markdown("### 📊 Visual Performance Comparison")

    if partial_coverage_names:
        escaped_names = [html.escape(n) for n in sorted(set(partial_coverage_names))]
        theme.render_banner(
            "⚠️ <b>Different starting dates in this window:</b> "
            f"{', '.join(escaped_names)} "
            "have less history inside the selected window than the others, so their 0% baseline below starts on a "
            "later real calendar date. Compare the underlying dates (hover the chart), not just the shape of the lines.",
            level="warning",
        )

    # Calculate Base 0% Return and Growth of ₹10,000
    start_navs = df_cmp.groupby("scheme_code")["nav"].transform("first")
    df_cmp["growth_pct"] = ((df_cmp["nav"] - start_navs) / start_navs) * 100.0
    df_cmp["portfolio_value"] = investment_amount * (1 + df_cmp["growth_pct"] / 100.0)

    tab_c1, tab_c2, tab_c3 = st.tabs([
        "📈 Growth of ₹10,000 Invested",
        "📊 Normalized % Return (Base 0%)",
        "📉 Nominal NAV Trajectory (₹)"
    ])

    with tab_c1:
        st.markdown(f"#### Value of ₹{investment_amount:,} Invested Over Selected Timeframe")
        st.caption("Demonstrates the real rupee return earned by each scheme if you invested on day 1 of the available history.")

        fig_port = px.line(
            df_cmp,
            x="nav_date",
            y="portfolio_value",
            color="scheme_name",
            markers=True,
            labels={"nav_date": "Date", "portfolio_value": "Portfolio Value (₹)", "scheme_name": "Scheme"},
            title=f"Growth of ₹{investment_amount:,} Initial Investment"
        )
        fig_port.add_hline(y=investment_amount, line_dash="dash", line_color="gray", opacity=0.7)
        fig_port.update_traces(
            hovertemplate="%{fullData.name}: <b>₹ %{y:,.2f}</b><extra></extra>"
        )
        fig_port.update_layout(
            hovermode="x unified",
            hoversort="value descending",
            hoverlabel=dict(namelength=-1),
            xaxis=dict(showgrid=True, hoverformat="%d-%b-%Y"),
            yaxis=dict(showgrid=True, tickprefix="₹ ", tickformat=","),
            legend=dict(orientation="h", yanchor="bottom", y=-0.35, xanchor="left", x=0),
            height=460
        )
        st.plotly_chart(fig_port, width="stretch")

    with tab_c2:
        st.markdown("#### Normalized Return % Comparison (Indexed to 0.0000%)")
        st.caption("Removes the price effect of different nominal NAVs to compare true percentage performance.")

        fig_norm = px.line(
            df_cmp,
            x="nav_date",
            y="growth_pct",
            color="scheme_name",
            markers=True,
            labels={"nav_date": "Date", "growth_pct": "Return (%)", "scheme_name": "Scheme"},
            title="Normalized % Gain / Loss from Day 1"
        )
        fig_norm.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.7)
        fig_norm.update_traces(
            hovertemplate="%{fullData.name}: <b>%{y:.4f}%</b><extra></extra>"
        )
        fig_norm.update_layout(
            hovermode="x unified",
            hoversort="value descending",
            hoverlabel=dict(namelength=-1),
            xaxis=dict(showgrid=True, hoverformat="%d-%b-%Y"),
            yaxis=dict(showgrid=True, ticksuffix="%", tickformat="+.2f"),
            legend=dict(orientation="h", yanchor="bottom", y=-0.35, xanchor="left", x=0),
            height=460
        )
        st.plotly_chart(fig_norm, width="stretch")

    with tab_c3:
        st.markdown("#### Nominal Net Asset Value (NAV) Trajectory")
        st.caption(f"Raw historical NAVs per unit ({active_start.strftime('%d-%b-%Y')} to {active_end.strftime('%d-%b-%Y')}).")

        fig_nav = px.line(
            df_cmp,
            x="nav_date",
            y="nav",
            color="scheme_name",
            markers=True,
            labels={"nav_date": "Date", "nav": "NAV (INR)", "scheme_name": "Scheme"},
            title="Historical NAV (₹) per Unit"
        )
        fig_nav.update_traces(
            hovertemplate="%{fullData.name}: <b>₹ %{y:.4f}</b><extra></extra>"
        )
        fig_nav.update_layout(
            hovermode="x unified",
            hoversort="value descending",
            hoverlabel=dict(namelength=-1),
            xaxis=dict(showgrid=True, rangeslider=dict(visible=False), hoverformat="%d-%b-%Y"),
            yaxis=dict(showgrid=True, tickformat=".4f", tickprefix="₹ "),
            legend=dict(orientation="h", yanchor="bottom", y=-0.35, xanchor="left", x=0),
            height=460
        )
        st.plotly_chart(fig_nav, width="stretch")

    # --- 3. COST DATA AVAILABILITY ---
    st.markdown("---")
    st.markdown("### 💰 Cost Data and Redemption Readiness")
    st.caption("NAV returns are net of TER. This page does not infer a redemption payout from the chart period.")

    st.info(
        "ℹ️ **Regulatory Framework on Mutual Fund Fees & Exit Penalties**:\n\n"
        "• **TER**: Published NAV and the return figures above are already net of TER. For schemes matched by the automated AMFI "
        "TER-portal sync (see the Base/Brokerage/Transaction/Statutory columns above), a dated daily series is now tracked going "
        "forward from when that scheme was first synced — but it still can't reconstruct fees paid before that point.\n\n"
        "• **Exit load**: A payout requires the actual purchase lots, redemption units, redemption date, and the rule that applied to each lot. The selected chart window is not a holding period.\n\n"
        "• **Direct vs Regular**: The applicable difference must come from dated, matched direct and regular TER records; this dashboard does not assume a fixed distributor commission."
    )

    exit_econ_rows = []
    bar_chart_data = []

    for code in selected_scheme_codes:
        prof, _ = db.get_scheme_profile(code)
        if prof:
            g = df_cmp[df_cmp["scheme_code"] == code].sort_values("nav_date")
            if not g.empty:
                s_nav = float(g.iloc[0]["nav"])
                e_nav = float(g.iloc[-1]["nav"])
                actual_days = max(1, (g.iloc[-1]["nav_date"].date() - g.iloc[0]["nav_date"].date()).days)

                ter_raw = prof.get("expense_ratio")
                ter_val = float(ter_raw) if ter_raw is not None and not pd.isna(ter_raw) else None
                ter_status = prof.get("ter_status") or "unknown"
                plan_str = prof.get("plan_type", "Direct")
                nominal_value = float(investment_amount) / s_nav * e_nav
                ter_estimate = costs_data.estimate_current_ter_drag(
                    float(investment_amount), nominal_value, actual_days, ter_val
                ) if ter_status == "official" else None
                exit_status_str = "Transaction lots and an official exit rule are required."

                exit_econ_rows.append({
                    "Scheme Name": prof["scheme_name"],
                    "Plan": plan_str,
                    "TER %": f"{ter_val:.4f}%" if ter_val is not None else "Unavailable",
                    "TER Confidence": ter_status,
                    "Exit Rule": prof.get("exit_load_description") or "Unavailable",
                    "Exit Status": exit_status_str,
                    "Initial Capital": f"₹ {investment_amount:,.2f}",
                    "NAV Value (not redemption payout)": f"₹ {nominal_value:,.2f}",
                    "Current-TER Illustration": f"₹ {ter_estimate:,.2f}" if ter_estimate is not None else "Unavailable",
                    "NAV Return %": f"{((e_nav - s_nav) / s_nav * 100.0):+.4f}%",
                })

                short_title = prof["scheme_name"][:35] + ("..." if len(prof["scheme_name"]) > 35 else "")
                bar_chart_data.append({
                    "Scheme": short_title,
                    "Value Type": "Initial Capital",
                    "Amount (₹)": float(investment_amount)
                })
                bar_chart_data.append({
                    "Scheme": short_title,
                    "Value Type": "Nominal Value (from NAV)",
                    "Amount (₹)": round(nominal_value, 2)
                })

    if exit_econ_rows:
        df_exit_econ = pd.DataFrame(exit_econ_rows)
        st.dataframe(df_exit_econ, hide_index=True, width="stretch")

        if bar_chart_data:
            df_bar = pd.DataFrame(bar_chart_data)
            fig_bar = px.bar(
                df_bar,
                x="Scheme",
                y="Amount (₹)",
                color="Value Type",
                barmode="group",
                color_discrete_map={
                    "Initial Capital": "#94A3B8",
                    "Nominal Value (from NAV)": "#2563EB"
                },
                title=f"Initial Capital vs NAV Value (not a redemption payout) — ₹{investment_amount:,} Initial Investment"
            )
            fig_bar.update_traces(texttemplate="₹ %{y:,.0f}", textposition="outside")
            fig_bar.update_layout(
                yaxis=dict(showgrid=True, tickprefix="₹ ", tickformat=","),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                height=450,
                margin=dict(l=10, r=10, t=40, b=10)
            )
            st.plotly_chart(fig_bar, width="stretch")

# ============================================================
# TAB 2 — PORTFOLIO BACKTEST
# ============================================================
with tab_portfolio:
    theme.render_banner(
        "This is a <b>historical backtest of official AMFI NAVs</b> over the time horizon selected above — not a forward-looking "
        "projection or a guarantee of future performance. NAVs are already net of TER. Rebalancing is simulated frictionlessly "
        "(no transaction costs, exit loads, or capital-gains tax), consistent with this dashboard's policy of never inventing a "
        "fee or tax figure it cannot verify.",
        level="info",
    )

    raw_weights: dict = {}
    st.markdown("<div style='font-size: 0.85rem; font-weight: 600; margin: 4px 0 2px 0;'>Target Allocation Weights (auto-normalized to 100%):</div>", unsafe_allow_html=True)
    default_w = round(100.0 / len(selected_scheme_codes), 2)
    w_cols = st.columns(len(selected_scheme_codes))
    for i, code in enumerate(selected_scheme_codes):
        with w_cols[i]:
            wkey = f"cs_weight_{code}"
            saved_w = filter_state.get_filter("portfolio_weights", str(code), default=default_w)
            if wkey not in st.session_state:
                st.session_state[wkey] = float(saved_w)
            w_val = st.number_input(
                scheme_map.get(code, str(code))[:28],
                min_value=0.0, max_value=100.0, step=1.0,
                key=wkey, help=scheme_map.get(code, str(code))
            )
            filter_state.set_filter("portfolio_weights", str(code), w_val)
            raw_weights[code] = w_val

    weight_sum = sum(raw_weights.values())
    if weight_sum <= 0:
        st.error("At least one fund needs a positive weight.")
    elif abs(weight_sum - 100.0) > 0.5:
        st.caption(f"ℹ️ Weights sum to {weight_sum:.1f}% — normalized proportionally to 100% for the simulation.")

    col_m1, col_m2, col_m3 = st.columns(3)
    with col_m1:
        mode_options = ["Lump Sum", "SIP (Monthly)"]
        saved_mode = filter_state.get_filter("portfolio_config", "mode", default=mode_options[0])
        if "cs_mode_widget" not in st.session_state or st.session_state["cs_mode_widget"] not in mode_options:
            st.session_state["cs_mode_widget"] = saved_mode if saved_mode in mode_options else mode_options[0]
        invest_mode = st.selectbox("Investment Mode", options=mode_options, key="cs_mode_widget")
        filter_state.set_filter("portfolio_config", "mode", invest_mode)

    with col_m2:
        if invest_mode == "Lump Sum":
            saved_amt = filter_state.get_filter("portfolio_config", "lump_sum_amount", default=100000)
            if "cs_lump_widget" not in st.session_state:
                st.session_state["cs_lump_widget"] = int(saved_amt)
            lump_sum_amount = st.number_input("Lump Sum Amount (₹)", min_value=1000, max_value=100000000, step=5000, key="cs_lump_widget")
            filter_state.set_filter("portfolio_config", "lump_sum_amount", lump_sum_amount)
            sip_amount = 0.0
        else:
            saved_sip = filter_state.get_filter("portfolio_config", "sip_amount", default=5000)
            if "cs_sip_widget" not in st.session_state:
                st.session_state["cs_sip_widget"] = int(saved_sip)
            sip_amount = st.number_input("Monthly SIP Amount (₹)", min_value=500, max_value=1000000, step=500, key="cs_sip_widget")
            filter_state.set_filter("portfolio_config", "sip_amount", sip_amount)
            lump_sum_amount = 0.0

    with col_m3:
        rebal_options = [portfolio_sim.REBALANCE_NONE, portfolio_sim.REBALANCE_MONTHLY, portfolio_sim.REBALANCE_QUARTERLY, portfolio_sim.REBALANCE_ANNUALLY]
        saved_rebal = filter_state.get_filter("portfolio_config", "rebalance", default=portfolio_sim.REBALANCE_NONE)
        if "cs_rebal_widget" not in st.session_state or st.session_state["cs_rebal_widget"] not in rebal_options:
            st.session_state["cs_rebal_widget"] = saved_rebal if saved_rebal in rebal_options else portfolio_sim.REBALANCE_NONE
        rebalance_freq = st.selectbox("Rebalancing Frequency", options=rebal_options, key="cs_rebal_widget", help="How often the portfolio is sold down and bought back up to its target weights.")
        filter_state.set_filter("portfolio_config", "rebalance", rebalance_freq)

    st.markdown("---")

    if weight_sum <= 0:
        st.stop()

    normalized_weights = {c: w / weight_sum for c, w in raw_weights.items()}

    # --- ALIGN NAV HISTORY FOR THE BACKTEST ---
    nav_wide = df_hist_raw.pivot_table(index="nav_date", columns="scheme_code", values="nav", aggfunc="last").sort_index()
    nav_wide = nav_wide.ffill().dropna()

    pf_missing_codes = [c for c in selected_scheme_codes if c not in nav_wide.columns]
    if pf_missing_codes:
        pf_missing_names = [html.escape(scheme_map.get(c, str(c))) for c in pf_missing_codes]
        theme.render_banner(
            f"⚠️ <b>Excluded from the backtest — no usable overlapping NAV data:</b> {', '.join(pf_missing_names)}.",
            level="warning",
        )
        normalized_weights = {c: w for c, w in normalized_weights.items() if c not in pf_missing_codes}
        w_total = sum(normalized_weights.values())
        if w_total <= 0:
            st.stop()
        normalized_weights = {c: w / w_total for c, w in normalized_weights.items()}

    if nav_wide.empty or len(nav_wide) < 2:
        st.warning("Not enough overlapping NAV history across the selected funds in this window. Widen the time horizon or pick funds with more common history.")
        st.stop()

    actual_start = nav_wide.index[0].date()
    if actual_start > active_start:
        theme.render_banner(
            f"ℹ️ The portfolio simulation starts on <b>{actual_start.strftime('%d-%b-%Y')}</b> (later than the selected "
            f"{active_start.strftime('%d-%b-%Y')}) — that's the earliest date every selected fund has NAV data, since a portfolio "
            "can't hold a fund before it existed.",
            level="info",
        )

    # One extra trading day just before the window starts, purely so the Time-Weighted return
    # series has a valid first-day return instead of losing that day — otherwise TWR CAGR can
    # disagree with XIRR even for a plain lump-sum with no rebalancing.
    lookback_row = None
    df_lookback = db.get_nav_history_dataframe(
        list(normalized_weights.keys()),
        start_date=actual_start - datetime.timedelta(days=10),
        end_date=actual_start - datetime.timedelta(days=1),
    )
    if not df_lookback.empty:
        df_lookback["nav_date"] = pd.to_datetime(df_lookback["nav_date"])
        lb_wide = (
            df_lookback.pivot_table(index="nav_date", columns="scheme_code", values="nav", aggfunc="last")
            .sort_index().ffill().reindex(columns=list(normalized_weights.keys()))
        )
        if not lb_wide.empty and not lb_wide.iloc[-1].isna().any():
            lookback_row = lb_wide.iloc[-1]

    # --- RUN BACKTEST ---
    result = portfolio_sim.run_backtest(
        nav_wide=nav_wide[list(normalized_weights.keys())],
        weights=normalized_weights,
        mode="SIP" if invest_mode.startswith("SIP") else "Lump Sum",
        lookback_row=lookback_row,
        lump_sum_amount=float(lump_sum_amount),
        sip_amount=float(sip_amount),
        rebalance_freq=rebalance_freq,
    )

    if "error" in result:
        st.warning(result["error"])
        st.stop()

    df_result = result["df_result"]
    twr_metrics = result["twr_metrics"]

    # --- KPI CARDS ---
    k1, k2, k3, k4 = st.columns(4)
    with k1:
        theme.render_metric_card("Final Portfolio Value", f"₹ {result['final_value']:,.2f}", f"Across {len(normalized_weights)} fund(s)")
    with k2:
        theme.render_metric_card("Total Invested", f"₹ {result['total_invested']:,.2f}", f"{result['n_contributions']} contribution(s)")
    with k3:
        gain = result["absolute_gain"]
        theme.render_metric_card(
            "Absolute Gain", f"₹ {gain:,.2f}",
            theme.format_signed_pct(result["absolute_return_pct"]) if result["absolute_return_pct"] is not None else "-",
            tone=theme.tone_class(gain),
        )
    with k4:
        xirr_pct = result["money_weighted_xirr_pct"]
        theme.render_metric_card(
            "XIRR (Money-Weighted)", theme.format_signed_pct(xirr_pct) if xirr_pct is not None else "N/A",
            "Your actual annualized return given contribution timing",
            tone=theme.tone_class(xirr_pct) if xirr_pct is not None else "",
        )

    k5, k6, k7, k8 = st.columns(4)
    with k5:
        twr_cagr = twr_metrics.get("cagr_pct")
        theme.render_metric_card(
            "Time-Weighted CAGR", theme.format_signed_pct(twr_cagr) if twr_cagr is not None else "N/A",
            "Strategy return, excludes contribution timing",
            tone=theme.tone_class(twr_cagr) if twr_cagr is not None else "",
        )
    with k6:
        sharpe = twr_metrics.get("sharpe_ratio")
        theme.render_metric_card("Sharpe Ratio (TWR)", f"{sharpe:.4f}" if isinstance(sharpe, (int, float)) else "N/A", "Risk-adjusted return of the strategy")
    with k7:
        mdd = twr_metrics.get("max_drawdown_pct")
        theme.render_metric_card("Max Drawdown (TWR)", f"{mdd:.4f}%" if isinstance(mdd, (int, float)) else "N/A", "Deepest peak-to-trough decline")
    with k8:
        vol = twr_metrics.get("vol_annualized_pct")
        theme.render_metric_card("Volatility (TWR, Ann.)", f"{vol:.4f}%" if isinstance(vol, (int, float)) else "N/A", "Annualized standard deviation")

    st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)

    # --- CHARTS ---
    tab_growth, tab_alloc, tab_breakdown = st.tabs(["📈 Portfolio Growth", "🥧 Allocation Drift", "📋 Fund Breakdown"])

    with tab_growth:
        st.markdown("#### Portfolio Value vs. Capital Invested")
        st.caption("The invested-capital line only rises with new contributions (flat for Lump Sum); the portfolio-value line reflects actual market performance.")
        fig_growth = go.Figure()
        fig_growth.add_trace(go.Scatter(
            x=df_result["nav_date"], y=df_result["portfolio_value"],
            mode="lines", name="Portfolio Value", line=dict(color="#2563EB", width=2.5),
        ))
        fig_growth.add_trace(go.Scatter(
            x=df_result["nav_date"], y=df_result["total_invested"],
            mode="lines", name="Capital Invested", line=dict(color="#94A3B8", width=1.5, dash="dash"),
        ))
        fig_growth.update_layout(
            hovermode="x unified",
            xaxis=dict(showgrid=True, hoverformat="%d-%b-%Y"),
            yaxis=dict(showgrid=True, tickprefix="₹ ", tickformat=","),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            height=460,
            margin=dict(l=10, r=10, t=20, b=10),
        )
        st.plotly_chart(fig_growth, width="stretch")

        csv_bytes = df_result.rename(columns={f"holding_{c}": scheme_map.get(c, str(c)) for c in normalized_weights}).to_csv(index=False).encode("utf-8")
        st.download_button("📥 Download Backtest Data (CSV)", data=csv_bytes, file_name=f"portfolio_backtest_{datetime.date.today()}.csv", mime="text/csv")

    with tab_alloc:
        st.markdown("#### Allocation Drift Over Time")
        st.caption(
            "How the portfolio's real weights drift away from target between rebalances (or forever, if Rebalancing Frequency is 'None')."
            if rebalance_freq == portfolio_sim.REBALANCE_NONE else
            f"Weights are reset to target every {rebalance_freq.lower()} rebalance; drift shows how far they wander between resets."
        )
        holding_cols = [f"holding_{c}" for c in normalized_weights]
        df_alloc = df_result[["nav_date"] + holding_cols].copy()
        total_row = df_alloc[holding_cols].sum(axis=1).replace(0, np.nan)
        for c in normalized_weights:
            df_alloc[f"pct_{c}"] = (df_alloc[f"holding_{c}"] / total_row) * 100.0
        df_alloc_long = df_alloc.melt(
            id_vars="nav_date",
            value_vars=[f"pct_{c}" for c in normalized_weights],
            var_name="fund_key", value_name="Weight %"
        )
        df_alloc_long["Fund"] = df_alloc_long["fund_key"].apply(lambda k: scheme_map.get(int(k.replace("pct_", "")), k))

        fig_alloc = px.area(
            df_alloc_long, x="nav_date", y="Weight %", color="Fund",
            labels={"nav_date": "Date"},
        )
        fig_alloc.update_layout(
            hovermode="x unified",
            yaxis=dict(ticksuffix="%", range=[0, 100]),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            height=440,
            margin=dict(l=10, r=10, t=20, b=10),
        )
        st.plotly_chart(fig_alloc, width="stretch")

    with tab_breakdown:
        st.markdown("#### Per-Fund Final Holdings")
        breakdown_rows = []
        final_row = df_result.iloc[-1]
        final_total = sum(final_row[f"holding_{c}"] for c in normalized_weights) or 1.0
        for c in normalized_weights:
            val = final_row[f"holding_{c}"]
            breakdown_rows.append({
                "Fund": scheme_map.get(c, str(c)),
                "Target Weight %": round(normalized_weights[c] * 100.0, 2),
                "Actual Weight % (Today)": round((val / final_total) * 100.0, 2),
                "Current Value (₹)": round(val, 2),
            })
        df_breakdown = pd.DataFrame(breakdown_rows)
        st.dataframe(
            df_breakdown,
            column_config={
                "Target Weight %": st.column_config.NumberColumn(format="%.2f %%"),
                "Actual Weight % (Today)": st.column_config.NumberColumn(format="%.2f %%"),
                "Current Value (₹)": st.column_config.NumberColumn(format="₹ %.2f"),
            },
            hide_index=True,
            width="stretch",
        )
