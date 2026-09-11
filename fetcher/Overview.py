import datetime
import os
import re
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from streamlit_searchbox import st_searchbox
import db
import amfi_sync
import date_picker
import theme

# --- Page Configuration ---
st.set_page_config(
    page_title="Indian Mutual Funds Analytics",
    page_icon="🇮🇳",
    layout="wide",
    initial_sidebar_state="expanded"
)
theme.inject_theme()

# --- Initialize Database & Background Sync ---
db.init_db()
amfi_sync.ensure_sync_daemon_running()

# --- Fetch Market Overview Stats ---
stats = db.get_market_overview_stats()

# --- Header Section (Title on left, Calendar Range Picker on right beside Deploy) ---
col_h1, col_h2 = st.columns([3, 2])
with col_h1:
    theme.render_page_header(
        "Indian Mutual Funds Analytics",
        "Institutional-grade macro analytics engine powered by official AMFI regulatory NAV records"
    )
with col_h2:
    date_picker.render_top_date_picker(current_page="overview")

active_start, active_end = date_picker.get_active_date_range()
span = (active_end - active_start).days

# --- Scope Slicer Bar (Plan Type Selection) ---
col_flt1, col_flt2 = st.columns([2, 3])
with col_flt1:
    plan_scope = st.radio(
        "Plan Type Filter:",
        ["All Plans", "Direct", "Regular"],
        horizontal=True,
        key="overview_plan_filter"
    )

kpis = db.get_kpis(plan_type=plan_scope, start_date=active_start, end_date=active_end)

adv = kpis.get("advancers", 0)
dec = kpis.get("decliners", 0)
unch = kpis.get("unchanged", 0)
tot_active = adv + dec + unch
adv_pct = (adv / tot_active * 100.0) if tot_active > 0 else 0.0
dec_pct = (dec / tot_active * 100.0) if tot_active > 0 else 0.0

med_ret = kpis.get("median_return", 0.0)
avg_ret = kpis.get("avg_return", 0.0)

# --- Top 6 Executive Command Bar KPIs ---
k1, k2, k3, k4, k5, k6 = st.columns(6)

with k1:
    theme.render_metric_card("Tracked Universe", f"{stats['total_schemes']:,}", f"Across {stats['total_amcs']} Fund Houses")

with k2:
    cov_str = f"{stats['min_date']} → {stats['max_date']}" if stats['min_date'] else "All Available"
    theme.render_metric_card("NAV Data Points", f"{stats['total_nav_records']:,}", cov_str)

with k3:
    breadth_sub = f"Adv: {adv:,} ({adv_pct:.1f}%) | Dec: {dec:,}"
    theme.render_metric_card(
        f"Market Breadth ({span}D)", f"{adv_pct:.1f}% Positive", breadth_sub,
        tone=theme.tone_class(adv - dec),
    )

with k4:
    theme.render_metric_card(
        f"Median Alpha ({span}D)", theme.format_signed_pct(med_ret), f"Industry Mean: {theme.format_signed_pct(avg_ret)}",
        tone=theme.tone_class(med_ret),
    )

with k5:
    if kpis.get("top_performer"):
        top_name = kpis["top_performer"]["name"]
        top_ret = kpis["top_performer"]["return_pct"]
        theme.render_metric_card(
            f"Top Alpha ({span}D)", theme.format_signed_pct(top_ret), top_name,
            tone=theme.tone_class(top_ret),
        )
    else:
        theme.render_metric_card("Top Alpha", "-", "No data")

with k6:
    if kpis.get("lag_performer"):
        lag_name = kpis["lag_performer"]["name"]
        lag_ret = kpis["lag_performer"]["return_pct"]
        theme.render_metric_card(
            f"Drawdown Laggard ({span}D)", theme.format_signed_pct(lag_ret), lag_name,
            tone=theme.tone_class(lag_ret), sub_tone="neg",
        )
    else:
        theme.render_metric_card("Drawdown Laggard", "-", "No data")

st.markdown("<div style='height: 15px;'></div>", unsafe_allow_html=True)

# --- Section 1: Macro Asset Class Performance & Dynamic Slicer ---
col_sec_head1, col_sec_head2 = st.columns([3, 2])
with col_sec_head1:
    st.markdown("### 📊 Macro Asset Class Performance")
    st.caption("Aggregated returns and scheme volume by SEBI broad asset classification (4-decimal precision).")
with col_sec_head2:
    horizon_choice = st.radio(
        "Asset Class Horizon:",
        ["1 Month (30D)", "1 Day", "1 Week (7D)", "3 Months (90D)", "1 Year (12M)"],
        horizontal=True,
        key="asset_horizon_radio"
    )

horizon_map = {
    "1 Day": "avg_1d",
    "1 Week (7D)": "avg_7d",
    "1 Month (30D)": "avg_30d",
    "3 Months (90D)": "avg_90d",
    "1 Year (12M)": "avg_1y"
}
selected_col = horizon_map.get(horizon_choice, "avg_30d")

col_left, col_right = st.columns([3, 2])

with col_left:
    df_assets = stats["asset_dist"].copy()
    if not df_assets.empty and selected_col in df_assets.columns:
        df_assets["bar_color"] = df_assets[selected_col].apply(lambda x: "#10B981" if (x or 0) >= 0 else "#EF4444")
        fig_assets = px.bar(
            df_assets,
            x="broad_category",
            y=selected_col,
            color="bar_color",
            color_discrete_map="identity",
            labels={"broad_category": "Asset Class", selected_col: f"Avg Return (%)"},
            title=f"Average Return by Asset Class ({horizon_choice})"
        )
        fig_assets.update_traces(
            texttemplate="%{y:+.4f}%",
            textposition="outside",
            hovertemplate="<b>%{x}</b>: %{y:+.4f}%<extra></extra>"
        )
        fig_assets.update_layout(
            showlegend=False,
            yaxis=dict(ticksuffix="%", showgrid=True),
            xaxis=dict(showgrid=False),
            height=360,
            margin=dict(l=10, r=10, t=40, b=10)
        )
        st.plotly_chart(fig_assets, width="stretch")

with col_right:
    if not df_assets.empty:
        st.dataframe(
            df_assets.rename(columns={
                "broad_category": "Asset Class",
                "count": "Total Schemes",
                "avg_1d": "Avg 1D %",
                "avg_7d": "Avg 7D %",
                "avg_30d": "Avg 30D %",
                "avg_90d": "Avg 90D %",
                "avg_1y": "Avg 1Y %"
            })[["Asset Class", "Total Schemes", "Avg 1D %", "Avg 7D %", "Avg 30D %", "Avg 90D %", "Avg 1Y %"]],
            column_config={
                "Avg 1D %": st.column_config.NumberColumn(format="%.4f %%"),
                "Avg 7D %": st.column_config.NumberColumn(format="%.4f %%"),
                "Avg 30D %": st.column_config.NumberColumn(format="%.4f %%"),
                "Avg 90D %": st.column_config.NumberColumn(format="%.4f %%"),
                "Avg 1Y %": st.column_config.NumberColumn(format="%.4f %%"),
            },
            hide_index=True,
            width="stretch",
            height=360
        )

# --- Section 2: Macro Asset Class Trajectory (Normalized Base-100 Timeline) ---
st.markdown("---")
st.markdown("### 📈 Macro Asset Class Trajectory (Indexed Performance, Base = 100)")
st.caption(f"Historical relative trajectory of Equity, Hybrid, and Debt asset classes normalized to 100 on {active_start.strftime('%d-%b-%Y')}.")

df_trend = db.get_macro_asset_class_trend(active_start, active_end, plan_type=plan_scope)
if not df_trend.empty and len(df_trend) > 3:
    fig_trend = px.line(
        df_trend,
        x="nav_date",
        y="Indexed Performance",
        color="Asset Class",
        color_discrete_map={
            "Equity": "#2563EB",
            "Hybrid": "#F59E0B",
            "Debt": "#10B981"
        },
        labels={"nav_date": "Date", "Indexed Performance": "Indexed Growth (Base 100)"}
    )
    fig_trend.update_traces(
        hovertemplate="%{fullData.name}: <b>%{y:.2f}</b><extra></extra>"
    )
    fig_trend.update_layout(
        height=350,
        margin=dict(l=10, r=10, t=20, b=10),
        hovermode="x unified",
        hoversort="value descending",
        hoverlabel=dict(namelength=-1),
        xaxis=dict(showgrid=True, hoverformat="%d-%b-%Y"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    st.plotly_chart(fig_trend, width="stretch")
else:
    st.info("Insufficient historical points in this range to plot indexed trajectory.")

# --- Section 3: Fund House (AMC) Intelligence & League Table ---
st.markdown("---")
col_amc1, col_amc2 = st.columns([2.5, 2.5])

with col_amc1:
    st.markdown("### 🏢 Top Fund Houses (AMCs)")
    amc_view = st.radio(
        "AMC View Mode:",
        ["Scheme Volume (Market Share)", "Portfolio Performance (Avg 30D %)"],
        horizontal=True,
        key="amc_view_radio"
    )
    df_top_amcs = stats["top_amcs"].copy()
    if not df_top_amcs.empty:
        if "Volume" in amc_view:
            fig_amc = px.pie(
                df_top_amcs.head(10),
                names="fund_house",
                values="schemes_count",
                hole=0.45,
                title="Top 10 AMCs by Scheme Volume"
            )
            fig_amc.update_traces(
                textinfo="label+percent",
                hovertemplate="<b>%{label}</b><br>Schemes: %{value:,}<br>Share: %{percent}<extra></extra>"
            )
            fig_amc.update_layout(showlegend=False, height=360, margin=dict(l=10, r=10, t=30, b=10))
            st.plotly_chart(fig_amc, width="stretch")
        else:
            df_perf_amc = df_top_amcs.dropna(subset=["avg_30d"]).sort_values(by="avg_30d", ascending=True).tail(10)
            df_perf_amc["bar_color"] = df_perf_amc["avg_30d"].apply(lambda x: "#10B981" if (x or 0) >= 0 else "#EF4444")
            fig_amc_bar = px.bar(
                df_perf_amc,
                x="avg_30d",
                y="fund_house",
                orientation="h",
                color="bar_color",
                color_discrete_map="identity",
                title="Top Fund Houses by 30D Portfolio Return"
            )
            fig_amc_bar.update_traces(
                texttemplate="%{x:+.2f}%",
                textposition="outside",
                hovertemplate="<b>%{y}</b>: %{x:+.4f}%<extra></extra>"
            )
            fig_amc_bar.update_layout(
                showlegend=False,
                height=360,
                margin=dict(l=10, r=10, t=30, b=10),
                yaxis_title=None,
                xaxis_title="Avg 30D Return (%)"
            )
            st.plotly_chart(fig_amc_bar, width="stretch")

with col_amc2:
    st.markdown("### 🏆 AMC League Table")
    st.caption("Leading fund houses ranked with scheme count and multi-horizon returns.")
    if not df_top_amcs.empty:
        st.dataframe(
            df_top_amcs.rename(columns={
                "fund_house": "Fund House",
                "schemes_count": "Schemes",
                "avg_30d": "Avg 30D %",
                "avg_90d": "Avg 90D %",
                "avg_1y": "Avg 1Y %"
            }),
            column_config={
                "Fund House": st.column_config.TextColumn("Fund House", width="medium"),
                "Schemes": st.column_config.NumberColumn("Schemes", format="%d"),
                "Avg 30D %": st.column_config.NumberColumn("Avg 30D %", format="%.4f %%"),
                "Avg 90D %": st.column_config.NumberColumn("Avg 90D %", format="%.4f %%"),
                "Avg 1Y %": st.column_config.NumberColumn("Avg 1Y %", format="%.4f %%"),
            },
            hide_index=True,
            width="stretch",
            height=360
        )

# --- Section 4: Interactive Category Performance Matrix ---
st.markdown("---")
st.markdown("### 📑 Detailed Category Performance Matrix")
st.caption("Comprehensive risk & return metrics across standardized SEBI fund categories with 4-decimal regulatory precision.")

with st.expander("🔍 Filter Category Matrix (Asset Class, Category Search)", expanded=True):
    col_cat_filter1, col_cat_filter2 = st.columns([3.2, 1.8])
    with col_cat_filter1:
        cat_scope = st.radio(
            "Filter Asset Class:",
            ["All Asset Classes", "Equity", "Debt", "Hybrid", "Solution Oriented", "Other / Index / ETF"],
            horizontal=True,
            key="cat_matrix_scope"
        )
    cat_param = "All" if cat_scope == "All Asset Classes" else cat_scope

    def search_categories_live(searchterm: str):
        if not searchterm or not searchterm.strip():
            return []
        terms = [t for t in re.findall(r'[a-zA-Z0-9]+', searchterm.lower()) if t]
        all_cats = db.get_subcategories(cat_param if cat_param != "All" else None)
        matches = [c for c in all_cats if all(t in c.lower() for t in terms)]
        opts = [(f"🔍 Filter table for '{searchterm}' ({len(matches)} matching)", f"QUERY:{searchterm}")]
        for c in matches[:15]:
            opts.append((c, f"CAT:{c}"))
        return opts

    with col_cat_filter2:
        st.markdown("<div style='font-size: 0.88rem; font-weight: 600; margin-bottom: 2px;'>Search Category (Live Suggestions As You Type):</div>", unsafe_allow_html=True)
        cat_search_selection = st_searchbox(
            search_categories_live,
            placeholder="Type e.g. small cap, liquid, flexi...",
            key="cat_live_searchbox"
        )

cat_search_val = ""
if cat_search_selection:
    s_val = str(cat_search_selection)
    if s_val.startswith("CAT:"):
        cat_search_val = s_val.split(":", 1)[1]
    elif s_val.startswith("QUERY:"):
        cat_search_val = s_val.split(":", 1)[1]
    else:
        cat_search_val = s_val

df_cat_matrix = db.get_category_performance_matrix(broad_category=cat_param)

if cat_search_val and not df_cat_matrix.empty:
    cat_tokens = [t for t in re.findall(r'[a-zA-Z0-9]+', cat_search_val.lower()) if t]
    mask = pd.Series(True, index=df_cat_matrix.index)
    search_col = (df_cat_matrix["Asset Class"].fillna("") + " " + df_cat_matrix["Category"].fillna("")).str.lower()
    for token in cat_tokens:
        mask &= search_col.str.contains(token, regex=False)
    df_cat_matrix = df_cat_matrix[mask]

st.dataframe(
    df_cat_matrix,
    column_config={
        "Asset Class": st.column_config.TextColumn("Asset Class", width="medium"),
        "Category": st.column_config.TextColumn("Category", width="large"),
        "Schemes": st.column_config.NumberColumn("Schemes", format="%d"),
        "Avg TER %": st.column_config.NumberColumn("Avg TER %", format="%.4f %%", width="small"),
        "Avg Exit %": st.column_config.NumberColumn("Avg Exit %", format="%.4f %%", width="small"),
        "Avg 1D %": st.column_config.NumberColumn("Avg 1D %", format="%.4f %%"),
        "Avg 7D %": st.column_config.NumberColumn("Avg 7D %", format="%.4f %%"),
        "Avg 30D %": st.column_config.NumberColumn("Avg 30D %", format="%.4f %%"),
        "Avg 90D %": st.column_config.NumberColumn("Avg 90D %", format="%.4f %%"),
        "Avg 1Y %": st.column_config.NumberColumn("Avg 1Y %", format="%.4f %%"),
        "52W High Gap %": st.column_config.NumberColumn("52W High Gap %", format="%.4f %%"),
        "Top Fund (30D) %": st.column_config.NumberColumn("Top Fund (30D)", format="%.4f %%"),
        "Bottom Fund (30D) %": st.column_config.NumberColumn("Bottom Fund (30D)", format="%.4f %%"),
    },
    hide_index=True,
    width="stretch",
    height=420
)
