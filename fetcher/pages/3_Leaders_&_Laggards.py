import datetime
import re
import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import db
import date_picker
import filter_state
import theme
from streamlit_searchbox import st_searchbox

st.set_page_config(page_title="Leaders & Laggards | Indian Mutual Funds", page_icon="🏆", layout="wide")
theme.inject_theme()

# A fund needs at least this many trading days of NAV history *inside the active window*
# before it's eligible for volatility-based ranking/classification — otherwise a fund with
# 1-2 NAV prints can win flagship KPIs or get badged "low risk" purely from a lack of data.
MIN_TRADING_DAYS_FOR_RANKING = 5

col_h1, col_h2 = st.columns([3.2, 1.8])
with col_h1:
    theme.render_page_header(
        "Leaders & Laggards — Relative Alpha & Momentum Engine",
        "Institutional peer outperformance (Alpha), risk-adjusted rankings, 4-quadrant momentum scatter, and category rotation."
    )
with col_h2:
    date_picker.render_top_date_picker(current_page="leaders")

active_start, active_end = date_picker.get_active_date_range()
span = (active_end - active_start).days
window_label = f"{active_start.strftime('%d-%b-%Y')} to {active_end.strftime('%d-%b-%Y')} ({span}D)"

# --- FILTER CONTROLS (Asset Class, Category, Plan, Option, Top N, Search) ---
with st.expander("🔍 Filter Universe & Leadership Scope (Asset Class, Category, Plan, Option, Search)", expanded=True):
    col_f_head1, col_f_head2 = st.columns([4.2, 1.2])
    with col_f_head1:
        st.markdown("<div style='color: var(--mf-muted); font-size: 0.92rem; padding-top: 6px;'>Narrow down the mutual fund universe by asset class, category, plan, or live keyword search.</div>", unsafe_allow_html=True)
    with col_f_head2:
        if st.button("🔄 Reset Filters", key="reset_leaders_btn", use_container_width=True):
            filter_state.reset_section("leaders")
            for k in [
                "leaders_broad_widget", "leaders_sub_widget", "leaders_plan_widget",
                "leaders_option_widget", "leaders_top_n_widget", "leaders_search_widget",
                "leaders_live_searchbox"
            ]:
                st.session_state.pop(k, None)
            st.rerun()

    col_c1, col_c2, col_c3, col_c4 = st.columns(4)

    with col_c1:
        broad_cats = ["All Categories"] + db.get_broad_categories()
        saved_broad = filter_state.get_filter("leaders", "broad_cat", default="All Categories")
        if "leaders_broad_widget" not in st.session_state or st.session_state["leaders_broad_widget"] not in broad_cats:
            st.session_state["leaders_broad_widget"] = saved_broad if saved_broad in broad_cats else broad_cats[0]
        selected_broad_cat = st.selectbox("Asset Class", options=broad_cats, key="leaders_broad_widget")
        filter_state.set_filter("leaders", "broad_cat", selected_broad_cat)

    with col_c2:
        sub_cats = ["All Sub-Categories"] + db.get_subcategories(
            selected_broad_cat if selected_broad_cat != "All Categories" else None
        )
        saved_sub = filter_state.get_filter("leaders", "sub_cat", default="All Sub-Categories")
        if "leaders_sub_widget" not in st.session_state or st.session_state["leaders_sub_widget"] not in sub_cats:
            st.session_state["leaders_sub_widget"] = saved_sub if saved_sub in sub_cats else sub_cats[0]
        selected_sub_cat = st.selectbox("Category", options=sub_cats, key="leaders_sub_widget")
        filter_state.set_filter("leaders", "sub_cat", selected_sub_cat)

    with col_c3:
        plan_options = ["All Plans", "Direct", "Regular"]
        saved_plan = filter_state.get_filter("leaders", "plan", default="All Plans")
        if "leaders_plan_widget" not in st.session_state or st.session_state["leaders_plan_widget"] not in plan_options:
            st.session_state["leaders_plan_widget"] = saved_plan if saved_plan in plan_options else plan_options[0]
        selected_plan = st.selectbox("Plan Type", options=plan_options, key="leaders_plan_widget")
        filter_state.set_filter("leaders", "plan", selected_plan)

    with col_c4:
        option_options = ["All Options", "Growth", "IDCW"]
        saved_opt = filter_state.get_filter("leaders", "option", default="All Options")
        if "leaders_option_widget" not in st.session_state or st.session_state["leaders_option_widget"] not in option_options:
            st.session_state["leaders_option_widget"] = saved_opt if saved_opt in option_options else option_options[0]
        selected_option = st.selectbox("Option Type", options=option_options, key="leaders_option_widget")
        filter_state.set_filter("leaders", "option", selected_option)

    col_c5, col_c6 = st.columns([3.2, 1.2])
    with col_c5:
        def search_leaders_live(searchterm: str):
            if not searchterm or not searchterm.strip():
                return []
            schemes = db.get_schemes_for_dropdown(
                broad_cat=selected_broad_cat,
                sub_cat=selected_sub_cat,
                plan_type=selected_plan,
                option_type=selected_option,
                search_term=searchterm,
                limit=20
            )
            opts = [(f"🔍 Filter universe by '{searchterm}' ({len(schemes)} matches)", searchterm)]
            for s in schemes:
                opts.append((s["display_label"], str(s["scheme_code"])))
            return opts

        st.markdown("<div style='font-size: 0.88rem; font-weight: 600; margin-bottom: 2px;'>Filter Schemes / Keyword (Live Suggestions As You Type):</div>", unsafe_allow_html=True)
        live_leaders_sel = st_searchbox(
            search_leaders_live,
            placeholder="Type keywords in any order (e.g. motilal arbitrage, small cap, 118989)...",
            key="leaders_live_searchbox"
        )
        if live_leaders_sel:
            search_query = str(live_leaders_sel)
            filter_state.set_filter("leaders", "search", search_query)
        else:
            search_query = filter_state.get_filter("leaders", "search", default="")

    with col_c6:
        saved_top_n = int(filter_state.get_filter("leaders", "top_n", default=10))
        top_n_opts = [5, 10, 15, 20, 25, 50]
        if "leaders_top_n_widget" not in st.session_state or st.session_state["leaders_top_n_widget"] not in top_n_opts:
            st.session_state["leaders_top_n_widget"] = saved_top_n if saved_top_n in top_n_opts else 10
        top_n = st.selectbox("Show Leaders Count", options=top_n_opts, key="leaders_top_n_widget")
        filter_state.set_filter("leaders", "top_n", top_n)

# --- FETCH ADVANCED LEADERSHIP DATASET FROM DUCKDB ---
df_all = db.get_advanced_leaders_dataframe(
    broad_cat=selected_broad_cat,
    sub_cat=selected_sub_cat,
    plan_type=selected_plan,
    option_type=selected_option,
    start_date=active_start,
    end_date=active_end
)

if df_all.empty:
    st.warning("No mutual fund data found matching your active filters and date window. Please adjust the filters above.")
    st.stop()

# Apply multi-token all-words keyword filter if present
if search_query and search_query.strip():
    tokens = [t for t in re.findall(r'[a-zA-Z0-9]+', search_query.lower()) if t]
    searchable_text = (
        df_all["display_name"].fillna("").astype(str).str.lower() + " " +
        df_all["scheme_name"].fillna("").astype(str).str.lower() + " " +
        df_all["scheme_code"].fillna("").astype(str) + " " +
        df_all["fund_house"].fillna("").astype(str).str.lower() + " " +
        df_all["category"].fillna("").astype(str).str.lower()
    )
    mask = pd.Series(True, index=df_all.index)
    for token in tokens:
        mask &= searchable_text.str.contains(token, regex=False)
    df_all = df_all[mask].copy()

if df_all.empty:
    st.info(f"No schemes matched the keyword '{search_query}'.")
    st.stop()

# --- MINIMUM TRACK-RECORD GUARD ---
# annualized_vol_pct is only populated (non-NaN) on the real date-windowed query path; on that
# path a fund with just 1-2 NAV prints in the window has almost no daily_ret observations and
# would otherwise rank alongside funds with a full window of history with no indication its
# number is far noisier — or get badged e.g. "Institutional Alpha Star" purely from a lack of data.
has_vol_data = df_all["annualized_vol_pct"].notna().any()
excluded_thin_data = 0
if has_vol_data:
    thin_mask = df_all["n_trading_days"] < MIN_TRADING_DAYS_FOR_RANKING
    excluded_thin_data = int(thin_mask.sum())
    if excluded_thin_data > 0:
        df_all = df_all[~thin_mask].copy()

if df_all.empty:
    st.info(
        f"All {excluded_thin_data} matching funds have fewer than {MIN_TRADING_DAYS_FOR_RANKING} trading days of NAV "
        "history in this window — not enough to rank. Widen the time horizon."
    )
    st.stop()

if excluded_thin_data > 0:
    st.caption(
        f"ℹ️ {excluded_thin_data:,} fund(s) with fewer than {MIN_TRADING_DAYS_FOR_RANKING} trading days of history in this "
        "window were excluded from ranking (too little data for a meaningful return/volatility figure)."
    )

# --- TOP INSTITUTIONAL KPI CARDS ---
total_funds = len(df_all)
advancers = int((df_all["period_return_pct"] > 0).sum())
decliners = int((df_all["period_return_pct"] < 0).sum())
adv_pct = (advancers / total_funds * 100.0) if total_funds > 0 else 0.0
market_median_ret = float(df_all["period_return_pct"].median())

top_alpha_row = df_all.sort_values("cat_alpha_pct", ascending=False).iloc[0]
best_cat_summary = df_all.groupby("category")["period_return_pct"].median().sort_values(ascending=False)
leading_cat_name = best_cat_summary.index[0] if not best_cat_summary.empty else "N/A"
leading_cat_ret = best_cat_summary.iloc[0] if not best_cat_summary.empty else 0.0

kc1, kc2, kc3, kc4 = st.columns(4)

with kc1:
    theme.render_metric_card(
        f"Market Breadth ({span}D)",
        f"{advancers:,} / {total_funds:,}",
        f"{adv_pct:.1f}% Advancing Funds",
        tone=theme.tone_class(adv_pct - 50),
    )

with kc2:
    theme.render_metric_card(
        "Universe Median Return",
        theme.format_signed_pct(market_median_ret),
        "Baseline Peer Benchmark",
        tone=theme.tone_class(market_median_ret),
    )

with kc3:
    theme.render_metric_card(
        "Highest Category Alpha",
        theme.format_signed_pct(top_alpha_row['cat_alpha_pct']),
        str(top_alpha_row['display_name']),
        tone=theme.tone_class(top_alpha_row['cat_alpha_pct']),
    )

with kc4:
    theme.render_metric_card(
        "Top Momentum Category",
        str(leading_cat_name),
        f"Median Return: {theme.format_signed_pct(leading_cat_ret)}",
        sub_tone="pos" if leading_cat_ret >= 0 else "neg",
    )

st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)

# Helper function to assign Quartile badge
def quartile_badge(q):
    if q == 1:
        return "🥇 Q1 (Top 25%)"
    elif q == 2:
        return "🥈 Q2 (25-50%)"
    elif q == 3:
        return "🥉 Q3 (50-75%)"
    return "🔻 Q4 (Bottom 25%)"

df_all["Quartile Rank"] = df_all["quartile"].apply(quartile_badge)

# --- PRO ANALYSIS TABS ---
tab_alpha, tab_quadrant, tab_abs, tab_rotation, tab_laggards = st.tabs([
    "🌟 Category Alpha Leaders",
    "⚖️ Risk-Reward 4-Quadrant Matrix",
    "🚀 Absolute Return Momentum",
    "🔄 Category Rotation Heatmap",
    "🩺 Laggard Diagnostic Engine"
])

# ==============================================================================
# TAB 1: CATEGORY ALPHA LEADERS (PEER RELATIVE OUTPERFORMANCE)
# ==============================================================================
with tab_alpha:
    st.info(
        "💡 **Institutional Alpha Ranking**: Measures each fund's excess return against its exact peer Category Median. "
        "This neutralizes broad sectoral or commodity market tides (e.g. Gold ETFs surging 20%) and highlights fund managers generating true Alpha."
    )
    
    col_a_gain, col_a_loss = st.columns(2)
    
    df_alpha_gainers = df_all.sort_values("cat_alpha_pct", ascending=False).head(top_n)
    df_alpha_losers = df_all.sort_values("cat_alpha_pct", ascending=True).head(top_n)
    
    with col_a_gain:
        st.markdown(f"#### 🚀 Top {top_n} Category Alpha Leaders ({window_label})")
        fig_ag = px.bar(
            df_alpha_gainers.sort_values("cat_alpha_pct", ascending=True),
            x="cat_alpha_pct",
            y="display_name",
            orientation="h",
            color="cat_alpha_pct",
            color_continuous_scale=["#6EE7B7", "#059669"],
            labels={"cat_alpha_pct": "Alpha vs Category Median %", "display_name": "Scheme"},
            title=f"Excess Return Above Peer Category Median"
        )
        fig_ag.update_traces(
            texttemplate="+%{x:.4f}%",
            textposition="inside",
            hovertemplate="<b>%{y}</b><br>Category Alpha: <b>+%{x:.4f}%</b><extra></extra>"
        )
        fig_ag.update_layout(
            # Data is already sorted ascending (weakest-of-top-N first); Plotly's default
            # bottom-to-top category order then puts the #1 leader at the top, matching the
            # Laggards panel's "most extreme at top" convention. Do not reverse this axis.
            yaxis=dict(showticklabels=True),
            xaxis=dict(ticksuffix="%", showgrid=True),
            coloraxis_showscale=False,
            height=320 + (len(df_alpha_gainers) * 22),
            margin=dict(l=10, r=10, t=35, b=10)
        )
        st.plotly_chart(fig_ag, width="stretch")
        
    with col_a_loss:
        st.markdown(f"#### 🔻 Top {top_n} Category Alpha Laggards ({window_label})")
        fig_al = px.bar(
            df_alpha_losers.sort_values("cat_alpha_pct", ascending=False),
            x="cat_alpha_pct",
            y="display_name",
            orientation="h",
            color="cat_alpha_pct",
            color_continuous_scale=["#DC2626", "#FCA5A5"],
            labels={"cat_alpha_pct": "Alpha vs Category Median %", "display_name": "Scheme"},
            title=f"Underperformance Below Peer Category Median"
        )
        fig_al.update_traces(
            # Explicit +/- sign: if the filtered universe is small, this "laggards" list can
            # include a fund whose alpha is coincidentally positive — an unsigned "2.10%" would
            # visually read as a loss it isn't.
            texttemplate="%{x:+.4f}%",
            textposition="inside",
            hovertemplate="<b>%{y}</b><br>Category Alpha: <b>%{x:+.4f}%</b><extra></extra>"
        )
        fig_al.update_layout(
            yaxis=dict(showticklabels=True),
            xaxis=dict(ticksuffix="%", showgrid=True),
            coloraxis_showscale=False,
            height=320 + (len(df_alpha_losers) * 22),
            margin=dict(l=10, r=10, t=35, b=10)
        )
        st.plotly_chart(fig_al, width="stretch")

    st.markdown("---")
    col_at1, col_at2 = st.columns([3, 1])
    with col_at1:
        st.markdown("### 📋 Category Alpha Data Table")
    with col_at2:
        alpha_csv = df_alpha_gainers.to_csv(index=False).encode("utf-8")
        st.download_button(
            "📥 Export (CSV)", data=alpha_csv,
            file_name=f"category_alpha_leaders_{datetime.date.today()}.csv",
            mime="text/csv", key="dl_alpha_leaders_csv"
        )

    alpha_cols_show = [
        "Quartile Rank", "scheme_code", "display_name", "category", "fund_house",
        "expense_ratio", "exit_load_pct",
        "period_return_pct", "cat_median_return", "cat_alpha_pct", "annualized_vol_pct", "dist_from_52w_high_pct"
    ]
    df_alpha_tbl = df_alpha_gainers[alpha_cols_show].copy()

    st.dataframe(
        df_alpha_tbl,
        column_config={
            "Quartile Rank": st.column_config.TextColumn("Quartile", width="small"),
            "scheme_code": st.column_config.NumberColumn("AMFI Code", format="%d", width="small"),
            "display_name": st.column_config.TextColumn("Scheme Name & Spec", width="large"),
            "category": st.column_config.TextColumn("Peer Category", width="medium"),
            "fund_house": st.column_config.TextColumn("AMC", width="small"),
            "expense_ratio": st.column_config.NumberColumn("TER %", format="%.4f %%", width="small"),
            "exit_load_pct": st.column_config.NumberColumn("Exit Load %", format="%.4f %%", width="small"),
            "period_return_pct": st.column_config.NumberColumn(f"Fund Return % ({span}D)", format="%+.4f %%"),
            "cat_median_return": st.column_config.NumberColumn("Category Median %", format="%+.4f %%"),
            "cat_alpha_pct": st.column_config.NumberColumn("Category Alpha %", format="%+.4f %%"),
            "annualized_vol_pct": st.column_config.NumberColumn("Ann. Volatility %", format="%.2f %%"),
            "dist_from_52w_high_pct": st.column_config.NumberColumn("From 52W High %", format="%.2f %%"),
        },
        hide_index=True,
        width="stretch"
    )

# ==============================================================================
# TAB 2: RISK-REWARD 4-QUADRANT SCATTER MATRIX
# ==============================================================================
with tab_quadrant:
    st.markdown("#### ⚖️ Risk-Reward 4-Quadrant Institutional Matrix")
    st.caption("Visualizes the trade-off between Risk (Annualized Volatility %) and Reward (Period Return %). Dashed crosshairs mark the active universe medians.")
    
    # Only funds with a real, computed volatility figure are plotted — never a fabricated one.
    df_quad = df_all[df_all["annualized_vol_pct"].notna() & (df_all["annualized_vol_pct"] > 0.1)].copy()
    n_excluded_from_quad = len(df_all) - len(df_quad)

    if df_quad.empty:
        theme.render_banner(
            "No fund in the current filtered universe has a computed volatility figure for this window "
            "(the window is likely too short, or the funds trade too infrequently, to measure day-to-day variance). "
            "Widen the time horizon to populate this chart.",
            level="warning",
        )
    else:
        if n_excluded_from_quad > 0:
            st.caption(
                f"ℹ️ {n_excluded_from_quad:,} fund(s) excluded from this chart — no measurable volatility in this window."
            )

        med_vol = float(df_quad["annualized_vol_pct"].median())
        med_ret = float(df_quad["period_return_pct"].median())

        # Quadrant Classification
        def classify_quadrant(row):
            if row["period_return_pct"] >= med_ret and row["annualized_vol_pct"] <= med_vol:
                return "Institutional Alpha Stars (High Return, Low Risk)"
            elif row["period_return_pct"] >= med_ret and row["annualized_vol_pct"] > med_vol:
                return "High-Beta Momentum (High Return, High Risk)"
            elif row["period_return_pct"] < med_ret and row["annualized_vol_pct"] <= med_vol:
                return "Defensive Anchors (Low Return, Low Risk)"
            else:
                return "Value Traps / Laggards (Low Return, High Risk)"

        df_quad["Quadrant"] = df_quad.apply(classify_quadrant, axis=1)

        # Show quadrant summary counts
        q_counts = df_quad["Quadrant"].value_counts()
        qc1, qc2, qc3, qc4 = st.columns(4)
        with qc1:
            st.markdown(f"""
                <div class="quadrant-box" style="border-left: 4px solid #10B981;">
                    <b style="color: #10B981;">Institutional Alpha Stars</b><br/>
                    <span style="font-size: 1.4rem; font-weight: 700;">{q_counts.get('Institutional Alpha Stars (High Return, Low Risk)', 0):,}</span> funds<br/>
                    <small style="color: var(--mf-muted);">High Return, Low Volatility</small>
                </div>
            """, unsafe_allow_html=True)
        with qc2:
            st.markdown(f"""
                <div class="quadrant-box" style="border-left: 4px solid #3B82F6;">
                    <b style="color: #3B82F6;">High-Beta Momentum</b><br/>
                    <span style="font-size: 1.4rem; font-weight: 700;">{q_counts.get('High-Beta Momentum (High Return, High Risk)', 0):,}</span> funds<br/>
                    <small style="color: var(--mf-muted);">High Return, High Volatility</small>
                </div>
            """, unsafe_allow_html=True)
        with qc3:
            st.markdown(f"""
                <div class="quadrant-box" style="border-left: 4px solid #94A3B8;">
                    <b style="color: #94A3B8;">Defensive Anchors</b><br/>
                    <span style="font-size: 1.4rem; font-weight: 700;">{q_counts.get('Defensive Anchors (Low Return, Low Risk)', 0):,}</span> funds<br/>
                    <small style="color: var(--mf-muted);">Low Return, Low Volatility</small>
                </div>
            """, unsafe_allow_html=True)
        with qc4:
            st.markdown(f"""
                <div class="quadrant-box" style="border-left: 4px solid #EF4444;">
                    <b style="color: #EF4444;">Value Traps / Laggards</b><br/>
                    <span style="font-size: 1.4rem; font-weight: 700;">{q_counts.get('Value Traps / Laggards (Low Return, High Risk)', 0):,}</span> funds<br/>
                    <small style="color: var(--mf-muted);">Low Return, High Volatility</small>
                </div>
            """, unsafe_allow_html=True)

        # Plot Quadrant Scatter
        sample_df = df_quad.head(500) if len(df_quad) > 500 else df_quad

        color_map = {
            "Institutional Alpha Stars (High Return, Low Risk)": "#10B981",
            "High-Beta Momentum (High Return, High Risk)": "#3B82F6",
            "Defensive Anchors (Low Return, Low Risk)": "#94A3B8",
            "Value Traps / Laggards (Low Return, High Risk)": "#EF4444"
        }

        fig_quad = px.scatter(
            sample_df,
            x="annualized_vol_pct",
            y="period_return_pct",
            color="Quadrant",
            color_discrete_map=color_map,
            hover_name="display_name",
            hover_data={
                "category": True,
                "period_return_pct": ":.4f%",
                "annualized_vol_pct": ":.2f%",
                "cat_alpha_pct": ":+.4f%",
                "Quartile Rank": True,
                "Quadrant": False
            },
            labels={
                "annualized_vol_pct": "Annualized Volatility % (Risk)",
                "period_return_pct": f"Window Return % ({span}D)"
            },
            title=f"Risk-Reward Scatter ({len(sample_df):,} of {len(df_quad):,} eligible funds shown{' — capped for render performance' if len(df_quad) > 500 else ''})"
        )

        # Add Crosshairs
        fig_quad.add_vline(x=med_vol, line_dash="dash", line_color="#94A3B8", annotation_text=f"Median Vol: {med_vol:.2f}%")
        fig_quad.add_hline(y=med_ret, line_dash="dash", line_color="#94A3B8", annotation_text=f"Median Ret: {med_ret:+.2f}%")

        fig_quad.update_layout(
            xaxis=dict(ticksuffix="%", showgrid=True),
            yaxis=dict(ticksuffix="%", showgrid=True),
            legend=dict(orientation="h", yanchor="bottom", y=-0.35, xanchor="left", x=0),
            height=540,
            margin=dict(l=10, r=10, t=40, b=10)
        )
        st.plotly_chart(fig_quad, width="stretch")

# ==============================================================================
# TAB 3: ABSOLUTE RETURN MOMENTUM
# ==============================================================================
with tab_abs:
    col_g, col_l = st.columns(2)
    
    df_abs_gainers = df_all.sort_values("period_return_pct", ascending=False).head(top_n)
    df_abs_losers = df_all.sort_values("period_return_pct", ascending=True).head(top_n)
    
    with col_g:
        st.markdown(f"#### 🚀 Top {top_n} Absolute Gainers ({window_label})")
        fig_g = px.bar(
            df_abs_gainers.sort_values("period_return_pct", ascending=True),
            x="period_return_pct",
            y="display_name",
            orientation="h",
            color_discrete_sequence=["#10B981"],
            labels={"period_return_pct": "% Return", "display_name": "Scheme"},
            title=f"Highest Absolute Point-to-Point Gainers"
        )
        fig_g.update_traces(
            texttemplate="+%{x:.4f}%",
            textposition="inside",
            hovertemplate="<b>%{y}</b><br>Return: +%{x:.4f}%<extra></extra>"
        )
        fig_g.update_layout(
            # Ascending-sorted data + default (non-reversed) category order already puts the
            # #1 gainer at the top, matching the Losers panel's "most extreme at top" convention.
            yaxis=dict(showticklabels=True),
            xaxis=dict(ticksuffix="%", showgrid=True),
            height=320 + (len(df_abs_gainers) * 22),
            margin=dict(l=10, r=10, t=35, b=10)
        )
        st.plotly_chart(fig_g, width="stretch")
        
    with col_l:
        st.markdown(f"#### 🔻 Top {top_n} Absolute Losers ({window_label})")
        fig_l = px.bar(
            df_abs_losers.sort_values("period_return_pct", ascending=False),
            x="period_return_pct",
            y="display_name",
            orientation="h",
            color_discrete_sequence=["#EF4444"],
            labels={"period_return_pct": "% Return", "display_name": "Scheme"},
            title=f"Lowest Absolute Point-to-Point Losers"
        )
        fig_l.update_traces(
            texttemplate="%{x:+.4f}%",
            textposition="inside",
            hovertemplate="<b>%{y}</b><br>Return: %{x:+.4f}%<extra></extra>"
        )
        fig_l.update_layout(
            yaxis=dict(showticklabels=True),
            xaxis=dict(ticksuffix="%", showgrid=True),
            height=320 + (len(df_abs_losers) * 22),
            margin=dict(l=10, r=10, t=35, b=10)
        )
        st.plotly_chart(fig_l, width="stretch")

    st.markdown("---")
    col_abst1, col_abst2 = st.columns([3, 1])
    with col_abst1:
        st.markdown("### 📋 Absolute Return Leaderboard")
    with col_abst2:
        abs_csv = pd.concat([df_abs_gainers, df_abs_losers]).to_csv(index=False).encode("utf-8")
        st.download_button(
            "📥 Export (CSV)", data=abs_csv,
            file_name=f"absolute_return_leaderboard_{datetime.date.today()}.csv",
            mime="text/csv", key="dl_abs_momentum_csv"
        )

    col_metric_name = f"Window Return % ({span}D)"
    tab_tbl_g, tab_tbl_l = st.tabs([f"🚀 Top {top_n} Gainers Table", f"🔻 Top {top_n} Losers Table"])
    
    with tab_tbl_g:
        st.dataframe(
            df_abs_gainers[["Quartile Rank", "scheme_code", "display_name", "fund_house", "category", "expense_ratio", "exit_load_pct", "period_return_pct", "cat_alpha_pct", "dist_from_52w_high_pct"]].rename(
                columns={"display_name": "Scheme Name", "fund_house": "AMC", "category": "Category", "expense_ratio": "TER %", "exit_load_pct": "Exit Load %", "period_return_pct": col_metric_name, "cat_alpha_pct": "Category Alpha %"}
            ),
            column_config={
                "TER %": st.column_config.NumberColumn(format="%.4f %%"),
                "Exit Load %": st.column_config.NumberColumn(format="%.4f %%"),
                col_metric_name: st.column_config.NumberColumn(format="+%.4f %%"),
                "Category Alpha %": st.column_config.NumberColumn(format="%+.4f %%"),
                "dist_from_52w_high_pct": st.column_config.NumberColumn("From 52W High %", format="%.2f %%"),
            },
            hide_index=True,
            width="stretch"
        )
    
    with tab_tbl_l:
        st.dataframe(
            df_abs_losers[["Quartile Rank", "scheme_code", "display_name", "fund_house", "category", "expense_ratio", "exit_load_pct", "period_return_pct", "cat_alpha_pct", "dist_from_52w_high_pct"]].rename(
                columns={"display_name": "Scheme Name", "fund_house": "AMC", "category": "Category", "expense_ratio": "TER %", "exit_load_pct": "Exit Load %", "period_return_pct": col_metric_name, "cat_alpha_pct": "Category Alpha %"}
            ),
            column_config={
                "TER %": st.column_config.NumberColumn(format="%.4f %%"),
                "Exit Load %": st.column_config.NumberColumn(format="%.4f %%"),
                col_metric_name: st.column_config.NumberColumn(format="%+.4f %%"),
                "Category Alpha %": st.column_config.NumberColumn(format="%+.4f %%"),
                "dist_from_52w_high_pct": st.column_config.NumberColumn("From 52W High %", format="%.2f %%"),
            },
            hide_index=True,
            width="stretch"
        )

# ==============================================================================
# TAB 4: CATEGORY & SECTOR ROTATION HEATMAP
# ==============================================================================
with tab_rotation:
    st.markdown("#### 🔄 Category & Sector Momentum Rotation Matrix")
    st.caption("Identifies which asset classes and categories are leading or lagging the broader market in this timeframe.")
    
    cat_rot = df_all.groupby(["broad_category", "category"]).agg(
        schemes_count=("scheme_code", "count"),
        avg_ter=("expense_ratio", "mean"),
        avg_exit=("exit_load_pct", "mean"),
        median_return=("period_return_pct", "median"),
        mean_return=("period_return_pct", "mean"),
        max_return=("period_return_pct", "max"),
        min_return=("period_return_pct", "min"),
        avg_vol=("annualized_vol_pct", "mean")
    ).reset_index()
    
    cat_rot["avg_ter"] = cat_rot["avg_ter"].round(4)
    cat_rot["avg_exit"] = cat_rot["avg_exit"].round(4)
    cat_rot["median_return"] = cat_rot["median_return"].round(4)
    cat_rot["mean_return"] = cat_rot["mean_return"].round(4)
    cat_rot["spread_pct"] = (cat_rot["max_return"] - cat_rot["min_return"]).round(4)
    cat_rot = cat_rot.sort_values("median_return", ascending=False).reset_index(drop=True)
    
    # Category Bar Chart
    fig_rot = px.bar(
        cat_rot.head(25).sort_values("median_return", ascending=True),
        x="median_return",
        y="category",
        color="median_return",
        color_continuous_scale="Temps",
        orientation="h",
        labels={"median_return": "Category Median Return %", "category": "Mutual Fund Category"},
        title="Top 25 Categories Ranked by Median Return"
    )
    fig_rot.update_traces(
        texttemplate="%{x:+.2f}%",
        textposition="inside",
        hovertemplate="<b>%{y}</b><br/>Median Return: <b>%{x:+.4f}%</b><extra></extra>"
    )
    fig_rot.update_layout(
        yaxis=dict(showticklabels=True),
        xaxis=dict(ticksuffix="%", showgrid=True),
        height=620,
        margin=dict(l=10, r=10, t=40, b=10)
    )
    st.plotly_chart(fig_rot, width="stretch")
    
    st.markdown("### 📊 Category Rotation Leaderboard Table")
    st.dataframe(
        cat_rot.rename(columns={
            "broad_category": "Asset Class",
            "category": "Category",
            "schemes_count": "Active Schemes",
            "avg_ter": "Avg TER %",
            "avg_exit": "Avg Exit %",
            "median_return": f"Median Return % ({span}D)",
            "mean_return": "Average Return %",
            "spread_pct": "Return Dispersion %",
            "avg_vol": "Avg Volatility %"
        }),
        column_config={
            "Avg TER %": st.column_config.NumberColumn(format="%.4f %%"),
            "Avg Exit %": st.column_config.NumberColumn(format="%.4f %%"),
            f"Median Return % ({span}D)": st.column_config.NumberColumn(format="%+.4f %%"),
            "Average Return %": st.column_config.NumberColumn(format="%+.4f %%"),
            "Return Dispersion %": st.column_config.NumberColumn(format="%.2f %%"),
            "Avg Volatility %": st.column_config.NumberColumn(format="%.2f %%"),
        },
        hide_index=True,
        width="stretch"
    )

# ==============================================================================
# TAB 5: LAGGARD DIAGNOSTIC & CYCLICAL DISTRESS ENGINE
# ==============================================================================
with tab_laggards:
    st.markdown("#### 🩺 Laggard Diagnostics & Cyclical Distress Analysis")
    st.caption("Distinguishes between temporary Cyclical Laggards (entire sector depressed, potential contrarian buy) and Structural Value Destroyers (fund severely lagging while category is healthy).")
    
    # Classify underperformers
    def diagnose_laggard(row):
        cat_med = row["cat_median_return"]
        alpha = row["cat_alpha_pct"]
        dd = row["dist_from_52w_high_pct"]
        
        if cat_med < 0 and alpha >= -1.5:
            return "🟡 Cyclical Dip (Category-wide correction; moving with peers)"
        elif alpha < -3.0:
            return "🔴 Structural Drag (Chronic underperformance vs peers)"
        elif dd < -15.0:
            return "🟠 Deep Drawdown (Far from 52W High)"
        else:
            return "⚪ Mild Underperformer"

    df_lag = df_all.sort_values("cat_alpha_pct", ascending=True).copy()
    df_lag["Diagnostic Classification"] = df_lag.apply(diagnose_laggard, axis=1)
    
    # Filter only funds with negative alpha or deep drawdowns
    df_diag = df_lag[(df_lag["cat_alpha_pct"] < 0) | (df_lag["dist_from_52w_high_pct"] < -10)].head(100)
    
    diag_counts = df_diag["Diagnostic Classification"].value_counts()
    dc1, dc2, dc3 = st.columns(3)
    with dc1:
        st.markdown(f"""
            <div class="quadrant-box" style="border-left: 4px solid #EAB308;">
                <b style="color: #EAB308;">Cyclical Dips</b><br/>
                <span style="font-size: 1.3rem; font-weight: 700;">{diag_counts.get('🟡 Cyclical Dip (Category-wide correction; moving with peers)', 0):,}</span> funds<br/>
                <small style="color: var(--mf-muted);">Sector down, fund tracking peers</small>
            </div>
        """, unsafe_allow_html=True)
    with dc2:
        st.markdown(f"""
            <div class="quadrant-box" style="border-left: 4px solid #EF4444;">
                <b style="color: #EF4444;">Structural Value Destroyers</b><br/>
                <span style="font-size: 1.3rem; font-weight: 700;">{diag_counts.get('🔴 Structural Drag (Chronic underperformance vs peers)', 0):,}</span> funds<br/>
                <small style="color: var(--mf-muted);">Severe alpha loss while peers gain</small>
            </div>
        """, unsafe_allow_html=True)
    with dc3:
        st.markdown(f"""
            <div class="quadrant-box" style="border-left: 4px solid #F97316;">
                <b style="color: #F97316;">Deep 52W Drawdowns</b><br/>
                <span style="font-size: 1.3rem; font-weight: 700;">{diag_counts.get('🟠 Deep Drawdown (Far from 52W High)', 0):,}</span> funds<br/>
                <small style="color: var(--mf-muted);">&gt;15% below peak NAV</small>
            </div>
        """, unsafe_allow_html=True)

    st.markdown("### 📋 Laggard Diagnostic Registry (Top 100 Underperforming Funds)")
    
    col_dl1, col_dl2 = st.columns([3, 1])
    with col_dl2:
        csv_lag = df_diag.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Export Diagnostics (CSV)",
            data=csv_lag,
            file_name=f"laggards_diagnostic_{datetime.date.today()}.csv",
            mime="text/csv"
        )
        
    st.dataframe(
        df_diag[[
            "Diagnostic Classification", "Quartile Rank", "scheme_code", "display_name", "category", "fund_house",
            "expense_ratio", "exit_load_pct",
            "period_return_pct", "cat_median_return", "cat_alpha_pct", "dist_from_52w_high_pct"
        ]],
        column_config={
            "Diagnostic Classification": st.column_config.TextColumn("Diagnostic Assessment", width="medium"),
            "Quartile Rank": st.column_config.TextColumn("Quartile", width="small"),
            "scheme_code": st.column_config.NumberColumn("AMFI Code", format="%d", width="small"),
            "display_name": st.column_config.TextColumn("Scheme Name & Spec", width="large"),
            "category": st.column_config.TextColumn("Category", width="medium"),
            "fund_house": st.column_config.TextColumn("AMC", width="small"),
            "expense_ratio": st.column_config.NumberColumn("TER %", format="%.4f %%", width="small"),
            "exit_load_pct": st.column_config.NumberColumn("Exit Load %", format="%.4f %%", width="small"),
            "period_return_pct": st.column_config.NumberColumn("Fund Return %", format="%+.4f %%"),
            "cat_median_return": st.column_config.NumberColumn("Category Median %", format="%+.4f %%"),
            "cat_alpha_pct": st.column_config.NumberColumn("Negative Alpha %", format="%+.4f %%"),
            "dist_from_52w_high_pct": st.column_config.NumberColumn("From 52W High %", format="%.2f %%"),
        },
        hide_index=True,
        width="stretch"
    )
