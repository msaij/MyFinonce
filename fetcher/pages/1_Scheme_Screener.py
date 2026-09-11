import datetime
import streamlit as st
import pandas as pd
import plotly.express as px
import db
import date_picker
import filter_state
import theme
from streamlit_searchbox import st_searchbox

st.set_page_config(page_title="Scheme Screener | Indian Mutual Funds", page_icon="🔍", layout="wide")
theme.inject_theme()

# --- Header Section (Title on left, Calendar Range Picker on right beside Deploy) ---
col_h1, col_h2 = st.columns([3, 2])
with col_h1:
    theme.render_page_header(
        "Mutual Fund Scheme Screener",
        "Institutional screener for all 14,000+ mutual fund schemes using official AMFI 4-decimal data."
    )
with col_h2:
    _hint_sub = st.session_state.get("screener_sub_widget")
    _hint_broad = st.session_state.get("screener_broad_widget")
    _active_cat_hint = None
    if _hint_sub and _hint_sub != "All Sub-Categories":
        _active_cat_hint = _hint_sub
    elif _hint_broad and _hint_broad != "All Categories":
        _active_cat_hint = _hint_broad
    screener_ctx = {"category": _active_cat_hint} if _active_cat_hint else None
    date_picker.render_top_date_picker(current_page="screener", page_context=screener_ctx)

active_start, active_end = date_picker.get_active_date_range()

# --- TOP KPI SUMMARY PLACEHOLDER (POSITIONED AT THE TOP OF THE PAGE) ---
kpi_placeholder = st.container()

st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)

# --- FILTER SECTION (Organized into Clean Dropdowns) ---
with st.expander("🎛️ Screener & Universe Filters (Fund House, Asset Class, Category, Plan, Option, Search)", expanded=True):
    col_f_head1, col_f_head2 = st.columns([4.2, 1.2])
    with col_f_head1:
        st.markdown("<div style='color: #64748B; font-size: 0.92rem; padding-top: 6px;'>Narrow down the mutual fund universe by AMC, category, plan, sort criteria, or live keyword search.</div>", unsafe_allow_html=True)
    with col_f_head2:
        if st.button("🔄 Reset Filters", key="reset_screener_btn", use_container_width=True):
            filter_state.reset_section("screener")
            for k in [
                "screener_amc_widget", "screener_broad_widget", "screener_sub_widget",
                "screener_plan_widget", "screener_option_widget", "screener_ter_widget", "screener_sort_widget",
                "screener_order_widget", "screener_scheme_code_widget",
                "screener_limit_widget", "screener_live_searchbox", "_screener_prev_search_selection",
                "screener_plot_mode_widget", "screener_chart_metric_widget", "screener_custom_schemes_widget"
            ]:
                st.session_state.pop(k, None)
            st.rerun()
    
    col_f1, col_f2, col_f3 = st.columns(3)
    
    with col_f1:
        # AMC Dropdown
        all_amcs = ["All Fund Houses"] + db.get_amcs()
        saved_amc = filter_state.get_filter("screener", "amc", default="All Fund Houses")
        if "screener_amc_widget" not in st.session_state or st.session_state["screener_amc_widget"] not in all_amcs:
            st.session_state["screener_amc_widget"] = saved_amc if saved_amc in all_amcs else all_amcs[0]
        selected_amc = st.selectbox("Fund House (AMC)", options=all_amcs, key="screener_amc_widget")
        filter_state.set_filter("screener", "amc", selected_amc)
        
    with col_f2:
        # Broad Category Dropdown
        broad_cats = ["All Categories"] + db.get_broad_categories()
        saved_broad = filter_state.get_filter("screener", "broad_cat", default="All Categories")
        if "screener_broad_widget" not in st.session_state or st.session_state["screener_broad_widget"] not in broad_cats:
            st.session_state["screener_broad_widget"] = saved_broad if saved_broad in broad_cats else broad_cats[0]
        selected_broad_cat = st.selectbox("Asset Class", options=broad_cats, key="screener_broad_widget")
        filter_state.set_filter("screener", "broad_cat", selected_broad_cat)
        
    with col_f3:
        # Sub-Category Dropdown (Cascades based on Asset Class!)
        sub_cats = ["All Sub-Categories"] + db.get_subcategories(
            selected_broad_cat if selected_broad_cat != "All Categories" else None
        )
        saved_sub = filter_state.get_filter("screener", "sub_cat", default="All Sub-Categories")
        if "screener_sub_widget" not in st.session_state or st.session_state["screener_sub_widget"] not in sub_cats:
            st.session_state["screener_sub_widget"] = saved_sub if saved_sub in sub_cats else sub_cats[0]
        selected_sub_cat = st.selectbox("Category", options=sub_cats, key="screener_sub_widget")
        filter_state.set_filter("screener", "sub_cat", selected_sub_cat)

    col_f4, col_f5, col_f_ter, col_f6, col_f7 = st.columns([1.2, 1.2, 1.4, 1.6, 1.4])
    
    with col_f4:
        # Plan Type Dropdown
        plan_options = ["All Plans", "Direct", "Regular"]
        saved_plan = filter_state.get_filter("screener", "plan", default="All Plans")
        if "screener_plan_widget" not in st.session_state or st.session_state["screener_plan_widget"] not in plan_options:
            st.session_state["screener_plan_widget"] = saved_plan if saved_plan in plan_options else plan_options[0]
        selected_plan = st.selectbox("Plan Type", options=plan_options, key="screener_plan_widget")
        filter_state.set_filter("screener", "plan", selected_plan)
        
    with col_f5:
        # Option Type Dropdown
        option_options = ["All Options", "Growth", "IDCW"]
        saved_option = filter_state.get_filter("screener", "option", default="All Options")
        if "screener_option_widget" not in st.session_state or st.session_state["screener_option_widget"] not in option_options:
            st.session_state["screener_option_widget"] = saved_option if saved_option in option_options else option_options[0]
        selected_option = st.selectbox("Option Type", options=option_options, key="screener_option_widget")
        filter_state.set_filter("screener", "option", selected_option)

    with col_f_ter:
        # Max TER Dropdown Filter
        ter_options = ["All Expense Ratios", "≤ 0.50% (Ultra-Low)", "≤ 1.00%", "≤ 1.50%", "≤ 2.00%"]
        ter_map = {
            "All Expense Ratios": None,
            "≤ 0.50% (Ultra-Low)": 0.50,
            "≤ 1.00%": 1.00,
            "≤ 1.50%": 1.50,
            "≤ 2.00%": 2.00
        }
        saved_ter_label = filter_state.get_filter("screener", "ter_label", default=ter_options[0])
        if "screener_ter_widget" not in st.session_state or st.session_state["screener_ter_widget"] not in ter_options:
            st.session_state["screener_ter_widget"] = saved_ter_label if saved_ter_label in ter_options else ter_options[0]
        selected_ter_label = st.selectbox("Max TER %", options=ter_options, key="screener_ter_widget")
        selected_max_ter = ter_map[selected_ter_label]
        filter_state.set_filter("screener", "ter_label", selected_ter_label)
        
    with col_f6:
        # Sort Field Dropdown
        sort_options = {
            "Selected Period Return %": "period_return_pct",
            "30-Day Return %": "return_30d_pct",
            "1-Year Return %": "return_1y_pct",
            "7-Day Return %": "return_7d_pct",
            "90-Day Return %": "return_90d_pct",
            "1-Day Change %": "change_1d_pct",
            "Lowest Expense Ratio (TER)": "expense_ratio",
            "Lowest Exit Load %": "exit_load_pct",
            "Latest NAV (₹)": "latest_nav",
            "Scheme Name": "scheme_name",
            "Distance from 52W High %": "dist_from_52w_high_pct"
        }
        sort_labels = list(sort_options.keys())
        saved_sort = filter_state.get_filter("screener", "sort_label", default=sort_labels[0])
        if "screener_sort_widget" not in st.session_state or st.session_state["screener_sort_widget"] not in sort_labels:
            st.session_state["screener_sort_widget"] = saved_sort if saved_sort in sort_labels else sort_labels[0]
        selected_sort_label = st.selectbox("Sort By", options=sort_labels, key="screener_sort_widget")
        selected_sort_col = sort_options[selected_sort_label]
        filter_state.set_filter("screener", "sort_label", selected_sort_label)
        
    with col_f7:
        # Sort Order Dropdown
        sort_orders = {"Highest to Lowest (DESC)": False, "Lowest to Highest (ASC)": True}
        # If user sorts by Lowest TER or Lowest Exit Load, default to ASC if not already configured
        order_labels = list(sort_orders.keys())
        default_order = order_labels[1] if ("Lowest" in selected_sort_label) else order_labels[0]
        # Direction is remembered per sort-field (not globally sticky): switching from
        # "Lowest TER" (ASC) to "1-Year Return %" should not silently inherit ASC.
        order_filter_key = f"order_label__{selected_sort_col}"
        saved_order = filter_state.get_filter("screener", order_filter_key, default=default_order)
        widget_key = f"screener_order_widget__{selected_sort_col}"
        if widget_key not in st.session_state or st.session_state[widget_key] not in order_labels:
            st.session_state[widget_key] = saved_order if saved_order in order_labels else default_order
        selected_order_label = st.selectbox("Sort Direction", options=order_labels, key=widget_key)
        ascending_val = sort_orders[selected_order_label]
        filter_state.set_filter("screener", order_filter_key, selected_order_label)

    col_s1, col_s2, col_s3 = st.columns([2.8, 2.2, 1.0])

    # Callback function for live suggestions as user types
    def search_schemes_live(searchterm: str):
        if not searchterm or not searchterm.strip():
            return []
        schemes = db.get_schemes_for_dropdown(
            amc=selected_amc,
            broad_cat=selected_broad_cat,
            sub_cat=selected_sub_cat,
            plan_type=selected_plan,
            option_type=selected_option,
            search_term=searchterm,
            limit=25
        )
        opts = [
            (f"🔍 Show all {len(schemes):,} matching funds in table for '{searchterm}'", f"QUERY:{searchterm}")
        ]
        for s in schemes:
            opts.append((s["display_label"], f"CODE:{s['scheme_code']}"))
        return opts

    with col_s1:
        st.markdown("<div style='font-size: 0.88rem; font-weight: 600; margin-bottom: 2px;'>Search Fund Name / AMFI Code (Live Suggestions As You Type):</div>", unsafe_allow_html=True)
        search_selection = st_searchbox(
            search_schemes_live,
            placeholder="Type any words in any order (e.g. motilal arbitrage, 153187)...",
            key="screener_live_searchbox"
        )

    # st_searchbox keeps re-returning the same prior selection on every rerun, not just the
    # rerun where the user actually picked it. Only treat it as a fresh action (and let it
    # drive the isolate-scheme dropdown) the one rerun where it actually changed — otherwise
    # a user's own later choice in that dropdown gets silently reverted every rerun.
    prev_search_selection = st.session_state.get("_screener_prev_search_selection")
    search_selection_is_new = search_selection is not None and search_selection != prev_search_selection
    st.session_state["_screener_prev_search_selection"] = search_selection

    search_query = ""
    fresh_scheme_code = None
    if search_selection_is_new:
        s_val = str(search_selection)
        if s_val.startswith("CODE:"):
            fresh_scheme_code = int(s_val.split(":")[1])
            filter_state.set_filter("screener", "scheme_code", fresh_scheme_code)
            filter_state.set_filter("screener", "search_query", "")
        elif s_val.startswith("QUERY:"):
            search_query = s_val.split(":", 1)[1]
            filter_state.set_filter("screener", "search_query", search_query)
            filter_state.set_filter("screener", "scheme_code", 0)
    elif search_selection and str(search_selection).startswith("QUERY:"):
        # Not a new pick this rerun, but a QUERY search is still the active one — keep filtering by it.
        search_query = str(search_selection).split(":", 1)[1]
    elif not search_selection:
        search_query = filter_state.get_filter("screener", "search_query", default="")

    # Dynamic Scheme / AMFI dropdown options matching active filters and current search query
    matching_schemes = db.get_schemes_for_dropdown(
        amc=selected_amc,
        broad_cat=selected_broad_cat,
        sub_cat=selected_sub_cat,
        plan_type=selected_plan,
        option_type=selected_option,
        search_term=search_query
    )

    scheme_dict = {
        0: f"— All Matching Schemes ({len(matching_schemes):,} available) —"
    }
    for s in matching_schemes:
        scheme_dict[s["scheme_code"]] = s["display_label"]

    scheme_options = list(scheme_dict.keys())

    with col_s2:
        if fresh_scheme_code is not None and fresh_scheme_code in scheme_options:
            # A brand-new search-box pick this rerun overrides whatever the dropdown held.
            st.session_state["screener_scheme_code_widget"] = fresh_scheme_code
        elif search_selection_is_new and fresh_scheme_code is None:
            # A brand-new QUERY search clears any prior scheme isolation.
            st.session_state["screener_scheme_code_widget"] = 0

        filter_state.sanitize_widget_state("screener_scheme_code_widget", scheme_options)
        if "screener_scheme_code_widget" not in st.session_state:
            saved_scheme_code = filter_state.get_filter("screener", "scheme_code", default=0)
            st.session_state["screener_scheme_code_widget"] = saved_scheme_code if saved_scheme_code in scheme_options else 0

        selected_scheme_code = st.selectbox(
            "Or Isolate Specific Scheme:",
            options=scheme_options,
            format_func=lambda code: scheme_dict.get(code, str(code)),
            key="screener_scheme_code_widget",
            help="Select a specific fund from the matching list to isolate its metrics and chart. This dropdown and the search box above each independently isolate a scheme — whichever you touch most recently wins."
        )
        filter_state.set_filter("screener", "scheme_code", selected_scheme_code)
        active_scheme_code = selected_scheme_code if selected_scheme_code != 0 else None

    with col_s3:
        limit_options = [100, 250, 500, 1000, 2500]
        saved_limit = filter_state.get_filter("screener", "limit", default=250)
        if "screener_limit_widget" not in st.session_state or st.session_state["screener_limit_widget"] not in limit_options:
            st.session_state["screener_limit_widget"] = saved_limit if saved_limit in limit_options else 250
        limit_count = st.selectbox("Show Top", options=limit_options, key="screener_limit_widget")
        filter_state.set_filter("screener", "limit", limit_count)

# --- QUERY DATABASE BASED ON ACTIVE FILTERS AND CALENDAR DATE RANGE ---
df_schemes = db.get_screener_dataframe(
    amc=selected_amc,
    broad_cat=selected_broad_cat,
    sub_cat=selected_sub_cat,
    plan_type=selected_plan,
    option_type=selected_option,
    search_term=search_query,
    sort_by=selected_sort_col,
    ascending=ascending_val,
    limit=limit_count,
    start_date=active_start,
    end_date=active_end,
    scheme_code=active_scheme_code,
    max_expense_ratio=selected_max_ter
)

kpi_data = db.get_kpis(
    amc=selected_amc,
    broad_cat=selected_broad_cat,
    sub_cat=selected_sub_cat,
    plan_type=selected_plan,
    option_type=selected_option,
    search_term=search_query,
    start_date=active_start,
    end_date=active_end,
    scheme_code=active_scheme_code,
    max_expense_ratio=selected_max_ter
)

# --- POPULATE THE TOP KPI SECTION (RENDERED AT THE TOP OF THE PAGE) ---
with kpi_placeholder:
    kc1, kc2, kc3, kc4 = st.columns(4)
    
    with kc1:
        theme.render_metric_card(
            "Matching Schemes",
            f"{kpi_data['total_schemes']:,}",
            "Selected Single Fund" if active_scheme_code else "Based on active filters",
        )

    with kc2:
        nav_date_str = pd.to_datetime(kpi_data['latest_date']).strftime('%d-%b-%Y') if kpi_data['latest_date'] else "N/A"
        theme.render_metric_card("Latest NAV Published", nav_date_str, "Official AMFI Published")

    span = (active_end - active_start).days
    with kc3:
        if active_scheme_code and not df_schemes.empty:
            scheme_ret = df_schemes.iloc[0].get("period_return_pct", 0.0)
            ret_val = scheme_ret if pd.notna(scheme_ret) else 0.0
            theme.render_metric_card(
                f"Period Return ({span}D)",
                theme.format_signed_pct(ret_val),
                str(df_schemes.iloc[0]['scheme_name']),
                tone=theme.tone_class(ret_val),
            )
        elif kpi_data["top_performer"]:
            t_name = kpi_data['top_performer']['name']
            t_ret_val = kpi_data['top_performer']['return_pct']
            theme.render_metric_card(
                f"Top Performer ({span}D)",
                theme.format_signed_pct(t_ret_val),
                t_name,
                tone=theme.tone_class(t_ret_val),
            )
        else:
            theme.render_metric_card(f"Top Performer ({span}D)", "-", "No data")

    with kc4:
        if active_scheme_code and not df_schemes.empty:
            row0 = df_schemes.iloc[0]
            cat_val = row0.get("category", "N/A")
            amc_val = row0.get("fund_house", "")
            theme.render_metric_card("Fund Category / AMC", str(cat_val), str(amc_val))
        elif kpi_data["lag_performer"]:
            l_name = kpi_data['lag_performer']['name']
            l_ret_val = kpi_data['lag_performer']['return_pct']
            theme.render_metric_card(
                f"Lagging Performer ({span}D)",
                theme.format_signed_pct(l_ret_val),
                l_name,
                tone=theme.tone_class(l_ret_val),
                sub_tone="neg",
            )
        else:
            theme.render_metric_card(f"Lagging Performer ({span}D)", "-", "No data")

st.markdown("---")

if active_scheme_code and not df_schemes.empty:
    row0 = df_schemes.iloc[0]
    ter_raw = row0.get("expense_ratio")
    ter_known = ter_raw is not None and pd.notna(ter_raw)
    ter_val = float(ter_raw) if ter_known else None
    ter_status = row0.get("ter_status") or "unknown"
    ter_source = row0.get("ter_source") or "No source recorded"
    exit_desc = row0.get("exit_load_description") or "Exit-load rule unavailable."
    exit_status = row0.get("exit_rule_status") or "unknown"
    exit_source = row0.get("exit_rule_source") or "No source recorded"
    lock_raw = row0.get("lock_in_years")
    lock_in = int(lock_raw) if lock_raw is not None and pd.notna(lock_raw) else None
    lock_str = f"Lock-in: {lock_in} years" if lock_in is not None else "Lock-in not verified"
    ter_display = f"{ter_val:.4f}% p.a." if ter_known else "Unavailable"
    
    st.markdown(f"""
    <div class="mf-banner mf-banner-info" style="display: flex; flex-wrap: wrap; gap: 28px; align-items: center; margin-bottom: 20px;">
        <div>
            <span style="font-size: 0.78rem; color: var(--mf-muted); text-transform: uppercase; font-weight: 600; letter-spacing: 0.5px;">Expense Ratio (TER)</span><br>
            <span style="font-size: 1.25rem; font-weight: 700; color: var(--mf-accent);">{ter_display}</span><br>
            <span style="font-size: 0.78rem; color: var(--mf-muted);">Status: {ter_status} · {ter_source}</span>
        </div>
        <div style="border-left: 1px solid var(--mf-border); padding-left: 20px;">
            <span style="font-size: 0.78rem; color: var(--mf-muted); text-transform: uppercase; font-weight: 600; letter-spacing: 0.5px;">Exit-load data</span><br>
            <span style="font-size: 1.05rem; font-weight: 700; color: var(--mf-warning);">{exit_status}</span><br>
            <span style="font-size: 0.78rem; color: var(--mf-muted);">{exit_source}</span>
        </div>
        <div style="flex-grow: 1; border-left: 1px solid var(--mf-border); padding-left: 20px;">
            <span style="font-size: 0.78rem; color: var(--mf-muted); text-transform: uppercase; font-weight: 600; letter-spacing: 0.5px;">Exit rule and lock-in</span><br>
            <span style="font-size: 0.95rem;">{exit_desc} &nbsp;|&nbsp; <b>{lock_str}</b></span>
        </div>
    </div>
    """, unsafe_allow_html=True)

# --- 1. GRAPH SECTION (FIRST GRAPH THEN TABLE) ---
if not df_schemes.empty:
    st.markdown("### 📈 Performance Trajectory Graph (Filtered Schemes)")
    
    total_matching = len(df_schemes)

    col_ctrl1, col_ctrl2 = st.columns([2, 1])

    with col_ctrl1:
        # Determine automatic plotting options
        plot_options = ["Auto: Top 5 from filtered results", "Auto: Top 10 from filtered results"]
        if total_matching <= 15:
            plot_options.insert(0, f"Auto: All {total_matching} filtered schemes")
        plot_options.append("Custom: Select specific schemes from filtered list")

        # Stable key + sanitizer (instead of a per-filter-combo dynamic key) so a value
        # persisted from a previous set of filters can never fall outside today's options
        # list and crash the widget.
        filter_state.sanitize_widget_state("screener_plot_mode_widget", plot_options)
        if "screener_plot_mode_widget" not in st.session_state:
            saved_plot_mode = filter_state.get_filter("screener", "plot_mode", default=plot_options[0])
            st.session_state["screener_plot_mode_widget"] = saved_plot_mode if saved_plot_mode in plot_options else plot_options[0]
        selected_plot_mode = st.selectbox(
            "Plot Selection (Directly from Filtered Results):",
            options=plot_options,
            key="screener_plot_mode_widget"
        )
        filter_state.set_filter("screener", "plot_mode", selected_plot_mode)

    with col_ctrl2:
        chart_options = ["Normalized % Return (Base 0%)", "Nominal NAV (₹)"]
        filter_state.sanitize_widget_state("screener_chart_metric_widget", chart_options)
        if "screener_chart_metric_widget" not in st.session_state:
            saved_metric = filter_state.get_filter("screener", "chart_view", default=chart_options[0])
            st.session_state["screener_chart_metric_widget"] = saved_metric if saved_metric in chart_options else chart_options[0]
        chart_view = st.selectbox(
            "Graph Metric",
            options=chart_options,
            key="screener_chart_metric_widget"
        )
        filter_state.set_filter("screener", "chart_view", chart_view)

    # Determine which scheme codes to plot
    if selected_plot_mode.startswith("Auto: All"):
        plotted_codes = list(df_schemes["scheme_code"].head(15))
    elif selected_plot_mode == "Auto: Top 5 from filtered results":
        plotted_codes = list(df_schemes["scheme_code"].head(5))
    elif selected_plot_mode == "Auto: Top 10 from filtered results":
        plotted_codes = list(df_schemes["scheme_code"].head(10))
    else:
        # Custom selection from filtered results
        scheme_labels = {
            r["scheme_code"]: db.format_scheme_display_name(r["scheme_name"], r.get("plan_type"), r.get("option_type"), r["scheme_code"])
            for _, r in df_schemes.iterrows()
        }
        filter_state.sanitize_widget_state("screener_custom_schemes_widget", scheme_labels.keys())
        if "screener_custom_schemes_widget" not in st.session_state:
            st.session_state["screener_custom_schemes_widget"] = list(df_schemes["scheme_code"].head(min(5, total_matching)))
        plotted_codes = st.multiselect(
            "Choose schemes to display on graph:",
            options=list(scheme_labels.keys()),
            format_func=lambda x: scheme_labels.get(x, str(x)),
            key="screener_custom_schemes_widget",
            max_selections=12
        )


    if plotted_codes:
        df_plot = db.get_nav_history_dataframe(plotted_codes, start_date=active_start, end_date=active_end)
        
        if not df_plot.empty:
            df_plot["nav_date"] = pd.to_datetime(df_plot["nav_date"])
            df_plot["nav"] = df_plot["nav"].astype(float)
            df_plot = df_plot.sort_values(["scheme_code", "nav_date"])
            
            sort_desc = f"{selected_sort_label} ({'Lowest First' if ascending_val else 'Highest First'})"
            st.caption(f"📊 Plotting **{len(plotted_codes)} schemes** directly matching current filters (Ranked by: *{sort_desc}*)")
            
            window_str = f"{active_start.strftime('%d-%b-%Y')} to {active_end.strftime('%d-%b-%Y')}"
            if chart_view == "Normalized % Return (Base 0%)":
                start_navs = df_plot.groupby("scheme_code")["nav"].transform("first")
                df_plot["return_pct"] = ((df_plot["nav"] - start_navs) / start_navs) * 100.0
                df_plot["return_pct"] = df_plot["return_pct"].round(4)
                
                fig = px.line(
                    df_plot,
                    x="nav_date",
                    y="return_pct",
                    color="scheme_name",
                    labels={"nav_date": "Date", "return_pct": "% Return", "scheme_name": "Scheme"},
                    title=f"Normalized % Return (Indexed to 0.0000% at Start) — {window_str}"
                )
                fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.7)
                fig.update_traces(
                    hovertemplate="%{fullData.name}: <b>%{y:.4f}%</b><extra></extra>"
                )
                fig.update_layout(
                    hovermode="x unified",
                    hoversort="value descending",
                    hoverlabel=dict(namelength=-1),
                    xaxis=dict(showgrid=True, hoverformat="%d-%b-%Y"),
                    yaxis=dict(showgrid=True, ticksuffix="%", tickformat="+.2f"),
                    legend=dict(orientation="h", yanchor="bottom", y=-0.35, xanchor="left", x=0),
                    height=450,
                    margin=dict(l=10, r=10, t=40, b=10)
                )
                st.plotly_chart(fig, width="stretch")
            else:
                fig = px.line(
                    df_plot,
                    x="nav_date",
                    y="nav",
                    color="scheme_name",
                    labels={"nav_date": "Date", "nav": "NAV (₹)", "scheme_name": "Scheme"},
                    title=f"Historical Net Asset Value (NAV in ₹) — {window_str}"
                )
                fig.update_traces(
                    hovertemplate="%{fullData.name}: <b>₹ %{y:.4f}</b><extra></extra>"
                )
                fig.update_layout(
                    hovermode="x unified",
                    hoversort="value descending",
                    hoverlabel=dict(namelength=-1),
                    xaxis=dict(showgrid=True, rangeslider=dict(visible=False), hoverformat="%d-%b-%Y"),
                    yaxis=dict(showgrid=True, tickprefix="₹ ", tickformat=".4f"),
                    legend=dict(orientation="h", yanchor="bottom", y=-0.45, xanchor="left", x=0),
                    height=480,
                    margin=dict(l=10, r=10, t=40, b=10)
                )
                st.plotly_chart(fig, width="stretch")
        else:
            st.info("No historical NAV records found for the selected schemes in this time window.")
    else:
        st.info("Please select at least 1 scheme to render the graph.")


st.markdown("---")

# --- 2. DATA TABLE SECTION (THEN TABLE) ---
st.markdown("### 📊 Filtered Performance Table")
st.caption("All NAVs, returns, and percentages are computed with strict 4-decimal precision.")

db_min_val = st.session_state.get("db_min_date")
if db_min_val and active_start < db_min_val:
    db_min_str = db_min_val.strftime('%d-%b-%Y') if hasattr(db_min_val, 'strftime') else str(db_min_val)
    coverage_days = (active_end - db_min_val).days
    st.warning(
        f"⚠️ **Data Notice**: Your active filter requests a **{span}-day window** (from {active_start.strftime('%d-%b-%Y')}), but the local database currently has records starting from **{db_min_str}** ({coverage_days} days of local history). "
        f"The **'Window Return % ({span}D)'** column reflects returns over the available {db_min_str}–present timeframe. "
        f"To fetch earlier history, run a historical backfill in **⚡ Data Management**."
    )

if df_schemes.empty:
    active_filters = []
    if selected_amc != "All Fund Houses":
        active_filters.append(f"AMC: {selected_amc}")
    if selected_broad_cat != "All Categories":
        active_filters.append(f"Asset Class: {selected_broad_cat}")
    if selected_sub_cat != "All Sub-Categories":
        active_filters.append(f"Category: {selected_sub_cat}")
    if selected_plan != "All Plans":
        active_filters.append(f"Plan: {selected_plan}")
    if selected_option != "All Options":
        active_filters.append(f"Option: {selected_option}")
    if selected_max_ter is not None:
        active_filters.append(f"Max TER: {selected_max_ter}%")
    if search_query:
        active_filters.append(f"Search: '{search_query}'")
    if active_scheme_code:
        active_filters.append(f"Isolated scheme code: {active_scheme_code}")
    filt_str = ", ".join(active_filters) if active_filters else "no filters"
    st.info(f"No mutual funds found matching your criteria ({filt_str}). Try loosening your filters.")
else:
    col_dl1, col_dl2 = st.columns([3, 1])
    with col_dl1:
        st.write(f"Showing **{len(df_schemes):,}** schemes of **{kpi_data['total_schemes']:,}** matching filters:")
    with col_dl2:
        df_export = df_schemes.copy()
        numeric_cols = df_export.select_dtypes(include=["float", "float64", "float32"]).columns
        df_export[numeric_cols] = df_export[numeric_cols].round(4)
        csv_bytes = df_export.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Download Data (CSV)",
            data=csv_bytes,
            file_name=f"amfi_screener_{datetime.date.today()}.csv",
            mime="text/csv"
        )
        
    st.dataframe(
        df_schemes,
        column_config={
            "scheme_code": st.column_config.NumberColumn("AMFI Code", format="%d", width="small"),
            "scheme_name": st.column_config.TextColumn("Scheme Name", width="large"),
            "fund_house": st.column_config.TextColumn("AMC", width="medium"),
            "category": st.column_config.TextColumn("Category", width="medium"),
            "plan_type": st.column_config.TextColumn("Plan", width="small"),
            "option_type": st.column_config.TextColumn("Option", width="small"),
            "expense_ratio": st.column_config.NumberColumn("Expense Ratio (TER)", format="%.4f %%", width="small"),
            "ter_status": st.column_config.TextColumn("TER Confidence", width="small"),
            "ter_source": st.column_config.TextColumn("TER Source", width="medium"),
            "ter_as_of_date": st.column_config.DateColumn("TER As Of", format="YYYY-MM-DD", width="small"),
            "exit_load_pct": st.column_config.NumberColumn("Exit Load %", format="%.4f %%", width="small"),
            "exit_load_days": st.column_config.NumberColumn("Exit Window", format="%d d", width="small"),
            "exit_load_description": st.column_config.TextColumn("Exit Rule", width="large"),
            "exit_rule_status": st.column_config.TextColumn("Exit Confidence", width="small"),
            "exit_rule_source": st.column_config.TextColumn("Exit Source", width="medium"),
            "exit_rule_as_of_date": st.column_config.DateColumn("Exit Rule As Of", format="YYYY-MM-DD", width="small"),
            "lock_in_years": st.column_config.NumberColumn("Lock-in (Yrs)", format="%d Y", width="small"),
            "latest_nav": st.column_config.NumberColumn("Latest NAV (₹)", format="₹ %.4f"),
            "latest_date": st.column_config.DateColumn("NAV Date", format="YYYY-MM-DD"),
            "change_1d_pct": st.column_config.NumberColumn("1D Chg %", format="%.4f %%"),
            "return_7d_pct": st.column_config.NumberColumn("7D Return %", format="%.4f %%"),
            "return_30d_pct": st.column_config.NumberColumn("30D Return %", format="%.4f %%"),
            "return_90d_pct": st.column_config.NumberColumn("90D Return %", format="%.4f %%"),
            "period_return_pct": st.column_config.NumberColumn(f"Window Return % ({span}D)", format="%.4f %%"),
            "return_1y_pct": st.column_config.NumberColumn("1Y Return %", format="%.4f %%"),
            "high_52w": st.column_config.NumberColumn("52W High (₹)", format="₹ %.4f"),
            "low_52w": st.column_config.NumberColumn("52W Low (₹)", format="₹ %.4f"),
            "dist_from_52w_high_pct": st.column_config.NumberColumn("From High %", format="%.4f %%"),
            "isin": st.column_config.TextColumn("ISIN", width="small"),
        },
        hide_index=True,
        width="stretch",
        height=580
    )
