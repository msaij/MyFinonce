import datetime
import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import db
import date_picker
import filter_state
import theme
import quant_analytics
from streamlit_searchbox import st_searchbox

st.set_page_config(
    page_title="Quantitative MF Analysis | Indian Mutual Funds",
    page_icon="🔬",
    layout="wide"
)
theme.inject_theme()

# --- Top Header & Unified Time Horizon ---
col_h1, col_h2 = st.columns([3, 2])
with col_h1:
    theme.render_page_header(
        "Quantitative Mutual Fund Analytics",
        "Institutional risk-adjusted performance, CAPM factor regression, tail risk, and Monte Carlo predictive modeling."
    )
with col_h2:
    chosen_code_hint = st.session_state.get("quant_code_widget") or filter_state.get_filter("quant", "chosen_code")
    bench_mode_hint = st.session_state.get("quant_bench_mode_widget") or filter_state.get_filter("quant", "bench_mode", default="Category Benchmark (Synthesized Peer Average)")
    quant_ctx = {}
    if chosen_code_hint:
        try:
            prof, _ = db.get_scheme_profile(chosen_code_hint)
            if prof:
                quant_ctx["scheme_name"] = prof.get("scheme_name", f"Scheme {chosen_code_hint}")
                quant_ctx["benchmark"] = bench_mode_hint
        except Exception:
            pass
    date_picker.render_top_date_picker(current_page="quant", page_context=quant_ctx if quant_ctx else None)

active_start, active_end = date_picker.get_active_date_range()
span_days = (active_end - active_start).days

# --- SCHEME FILTER & QUANT CONTROLS (UNIFIED BOX) ---
with st.expander("🎛️ Quantitative Model Controls & Universe Filters (Fund House, Category, Scheme, Benchmark, Rf)", expanded=True):
    col_qf1, col_qf2, col_qf3, col_qf4 = st.columns(4)
    with col_qf1:
        all_amcs = ["All Fund Houses"] + db.get_amcs()
        saved_q_amc = filter_state.get_filter("quant", "filter_amc", default="All Fund Houses")
        if "quant_f_amc" not in st.session_state or st.session_state["quant_f_amc"] not in all_amcs:
            st.session_state["quant_f_amc"] = saved_q_amc if saved_q_amc in all_amcs else all_amcs[0]
        sel_q_amc = st.selectbox("Fund House (AMC)", options=all_amcs, key="quant_f_amc")
        filter_state.set_filter("quant", "filter_amc", sel_q_amc)

    with col_qf2:
        broad_cats = ["All Categories"] + db.get_broad_categories()
        saved_q_broad = filter_state.get_filter("quant", "filter_broad", default="All Categories")
        if "quant_f_broad" not in st.session_state or st.session_state["quant_f_broad"] not in broad_cats:
            st.session_state["quant_f_broad"] = saved_q_broad if saved_q_broad in broad_cats else broad_cats[0]
        sel_q_broad = st.selectbox("Asset Class", options=broad_cats, key="quant_f_broad")
        filter_state.set_filter("quant", "filter_broad", sel_q_broad)

    with col_qf3:
        sub_cats = ["All Sub-Categories"] + db.get_subcategories(
            sel_q_broad if sel_q_broad != "All Categories" else None
        )
        saved_q_sub = filter_state.get_filter("quant", "filter_sub", default="All Sub-Categories")
        if "quant_f_sub" not in st.session_state or st.session_state["quant_f_sub"] not in sub_cats:
            st.session_state["quant_f_sub"] = saved_q_sub if saved_q_sub in sub_cats else sub_cats[0]
        sel_q_sub = st.selectbox("Category", options=sub_cats, key="quant_f_sub")
        filter_state.set_filter("quant", "filter_sub", sel_q_sub)

    with col_qf4:
        plan_opts = ["All Plans", "Direct", "Regular"]
        saved_q_plan = filter_state.get_filter("quant", "filter_plan", default="Direct")
        if "quant_f_plan" not in st.session_state or st.session_state["quant_f_plan"] not in plan_opts:
            st.session_state["quant_f_plan"] = saved_q_plan if saved_q_plan in plan_opts else "Direct"
        sel_q_plan = st.selectbox("Plan Type", options=plan_opts, key="quant_f_plan")
        filter_state.set_filter("quant", "filter_plan", sel_q_plan)

    col_qs1, col_qs2 = st.columns([4.2, 1.2])
    
    def search_quant_schemes_live(searchterm: str):
        if not searchterm or not searchterm.strip():
            return []
        schemes = db.get_schemes_for_dropdown(
            amc=sel_q_amc,
            broad_cat=sel_q_broad,
            sub_cat=sel_q_sub,
            plan_type=sel_q_plan,
            option_type="All Options",
            search_term=searchterm,
            limit=25
        )
        return [(s["display_label"], s["scheme_code"]) for s in schemes]

    with col_qs1:
        st.markdown("<div style='font-size: 0.88rem; font-weight: 600; margin-bottom: 2px;'>Search Scheme Name or AMFI Code (Live Suggestions As You Type):</div>", unsafe_allow_html=True)
        live_quant_sel = st_searchbox(
            search_quant_schemes_live,
            placeholder="Type any keywords in any order (e.g. motilal arbitrage, small cap, 153187)...",
            key="quant_live_searchbox"
        )
        
    with col_qs2:
        st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
        if st.button("🔄 Reset Filters", key="quant_reset_btn", use_container_width=True):
            filter_state.set_filter("quant", "filter_amc", "All Fund Houses")
            filter_state.set_filter("quant", "filter_broad", "All Categories")
            filter_state.set_filter("quant", "filter_sub", "All Sub-Categories")
            filter_state.set_filter("quant", "filter_plan", "Direct")
            filter_state.set_filter("quant", "filter_search", "")
            for k in ["quant_f_amc", "quant_f_broad", "quant_f_sub", "quant_f_plan", "quant_f_search", "_quant_last_search", "quant_live_searchbox"]:
                st.session_state.pop(k, None)
            st.rerun()

    search_query = ""
    if live_quant_sel:
        st.session_state["quant_code_widget"] = int(live_quant_sel)
        filter_state.set_filter("quant", "chosen_code", int(live_quant_sel))

    # Fetch schemes matching the active filters
    filtered_schemes_df = db.get_screener_dataframe(
        amc=sel_q_amc,
        broad_cat=sel_q_broad,
        sub_cat=sel_q_sub,
        plan_type=sel_q_plan,
        option_type="All Options",
        search_term=search_query,
        sort_by="scheme_name",
        ascending=True,
        limit=5000
    )

    scheme_options = {}
    for _, r in filtered_schemes_df.iterrows():
        c = r["scheme_code"]
        display_label = db.format_scheme_display_name(r["scheme_name"], r.get("plan_type"), r.get("option_type"), c)
        scheme_options[c] = display_label

    # Only inject saved_code if search_query is empty, preserving user's selection across broad category switches without polluting search results
    saved_code = filter_state.get_filter("quant", "chosen_code", default=None)
    if not search_query.strip():
        if saved_code and saved_code not in scheme_options:
            prof_saved, _ = db.get_scheme_profile(saved_code)
            if prof_saved:
                scheme_options[saved_code] = db.format_scheme_display_name(
                    prof_saved["scheme_name"], prof_saved.get("plan_type"), prof_saved.get("option_type"), saved_code
                )

    scheme_code_keys = list(scheme_options.keys())

    # Detect search query change: if user typed a new search, auto-focus on the first matching fund
    last_q_search = st.session_state.get("_quant_last_search", "")
    if search_query != last_q_search:
        st.session_state["_quant_last_search"] = search_query
        if scheme_code_keys:
            st.session_state["quant_code_widget"] = scheme_code_keys[0]
    else:
        st.session_state["_quant_last_search"] = search_query

    # Validate / initialize chosen_code
    if "quant_code_widget" not in st.session_state or st.session_state["quant_code_widget"] not in scheme_code_keys:
        if saved_code in scheme_options:
            st.session_state["quant_code_widget"] = saved_code
        elif scheme_code_keys:
            st.session_state["quant_code_widget"] = scheme_code_keys[0]

    st.markdown("<div style='margin-top: 6px; margin-bottom: 8px; border-top: 1px solid rgba(128, 128, 128, 0.2);'></div>", unsafe_allow_html=True)

    # --- QUANT CONFIGURATION CONTROLLERS ---
    col_sel1, col_sel2, col_sel3 = st.columns([2.5, 1.5, 1.0])
    
    with col_sel1:
        scheme_label_count = f"Primary Mutual Fund Scheme ({len(scheme_code_keys):,} available):"
        if not scheme_code_keys:
            st.warning("⚠️ No schemes match current filter. Please clear search or broaden filters above.")
            chosen_code = None
        else:
            chosen_code = st.selectbox(
                scheme_label_count,
                options=scheme_code_keys,
                format_func=lambda x: scheme_options.get(x, str(x)),
                key="quant_code_widget"
            )
            filter_state.set_filter("quant", "chosen_code", chosen_code)
        
    with col_sel2:
        bench_choices = [
            "Category Benchmark (Synthesized Peer Average)",
            "Nifty 50 Index Fund Proxy",
            "Custom Peer Mutual Fund"
        ]
        saved_bench_mode = filter_state.get_filter("quant", "bench_mode", default=bench_choices[0])
        bench_idx = bench_choices.index(saved_bench_mode) if saved_bench_mode in bench_choices else 0
        
        bench_mode = st.selectbox(
            "Benchmark for Factor Regression:",
            options=bench_choices,
            index=bench_idx,
            key="quant_bench_mode_widget"
        )
        filter_state.set_filter("quant", "bench_mode", bench_mode)
        
    with col_sel3:
        saved_rf = filter_state.get_filter("quant", "risk_free_rate", default=6.50)
        rf_pct = st.number_input(
            "Risk-Free Rate (Rf %):",
            min_value=0.0,
            max_value=15.0,
            value=float(saved_rf),
            step=0.25,
            help="Annualized risk-free benchmark rate (India 91-Day T-Bill / RBI Repo rate proxy, typically 6.50%).",
            key="quant_rf_widget"
        )
        filter_state.set_filter("quant", "risk_free_rate", rf_pct)

    # Custom peer selector if selected
    custom_peer_code = None
    if bench_mode == "Custom Peer Mutual Fund":
        peer_options = [c for c in scheme_code_keys if c != chosen_code]
        if not peer_options:
            all_d = db.get_screener_dataframe(limit=250)
            peer_options = [r["scheme_code"] for _, r in all_d.iterrows() if r["scheme_code"] != chosen_code]
        saved_peer = filter_state.get_filter("quant", "custom_peer_code", default=peer_options[0] if peer_options else None)
        peer_idx = peer_options.index(saved_peer) if saved_peer in peer_options else 0
        
        custom_peer_code = st.selectbox(
            "Select Peer Benchmark Scheme:",
            options=peer_options,
            index=peer_idx,
            format_func=lambda x: scheme_options.get(x, str(x)),
            key="quant_custom_peer_widget"
        )
        filter_state.set_filter("quant", "custom_peer_code", custom_peer_code)

st.markdown("---")

if chosen_code:
    profile, df_hist_raw = db.get_scheme_profile(chosen_code)
    
    if profile and not df_hist_raw.empty:
        rf_annual = rf_pct / 100.0
        
        # Prepare continuous time-series for primary fund across full history, sliced to [active_start, active_end]
        df_fund, cov_fund = quant_analytics.prepare_fund_timeseries(
            df_hist_raw, active_start, active_end, risk_free_rate_ann=rf_annual
        )
        
        if df_fund.empty or not cov_fund.get("has_data", False):
            st.warning("⚠️ No NAV records found for this scheme within the selected date window. Please expand your date range.")
            st.stop()
            
        q_metrics = quant_analytics.compute_risk_adjusted_metrics(df_fund, risk_free_rate_ann=rf_annual)
        
        # --- PREPARE BENCHMARK DATA ---
        df_bench = pd.DataFrame()
        bench_label = "Benchmark"
        
        bench_unavailable_reason = None

        if bench_mode == "Category Benchmark (Synthesized Peer Average)":
            cat_name = profile.get("category", "")
            bench_label = f"Category Avg ({cat_name})"
            df_bench = quant_analytics.get_synthetic_category_benchmark(cat_name, start_date=active_start, end_date=active_end)
            if df_bench.empty:
                bench_unavailable_reason = (
                    f"No category-peer NAV data was available to build a synthesized benchmark for "
                    f"**{cat_name or 'this category'}** in the selected window."
                )

        elif bench_mode == "Nifty 50 Index Fund Proxy":
            bench_label = "Nifty 50 Index Fund"
            # Search the *full* universe for a Nifty 50 index fund, independent of whatever
            # AMC/Category/Plan filters are currently narrowing the primary-fund dropdown —
            # otherwise an active filter with no Nifty 50 fund in it previously fell back to
            # an arbitrary, unrelated fund while still labeling the chart "Nifty 50 Index Fund".
            nifty_candidates = db.get_schemes_for_dropdown(search_term="nifty 50 index", limit=50)
            nifty_direct_growth = [
                s for s in nifty_candidates
                if s["plan_type"] == "Direct" and s["option_type"] == "Growth" and "nifty 50" in s["scheme_name"].lower()
            ]
            nifty_code = nifty_direct_growth[0]["scheme_code"] if nifty_direct_growth else (nifty_candidates[0]["scheme_code"] if nifty_candidates else None)
            if nifty_code is None:
                bench_unavailable_reason = "No Nifty 50 index fund was found in the local database to use as a proxy."
            else:
                _, df_b_raw = db.get_scheme_profile(nifty_code)
                if not df_b_raw.empty:
                    df_bench, _ = quant_analytics.prepare_fund_timeseries(
                        df_b_raw, active_start, active_end, risk_free_rate_ann=rf_annual
                    )
                if df_bench.empty:
                    bench_unavailable_reason = "The Nifty 50 index fund found has no NAV data in the selected window."

        elif bench_mode == "Custom Peer Mutual Fund" and custom_peer_code:
            bench_label = scheme_options.get(custom_peer_code, "Custom Peer")
            _, df_b_raw = db.get_scheme_profile(custom_peer_code)
            if not df_b_raw.empty:
                df_bench, _ = quant_analytics.prepare_fund_timeseries(
                    df_b_raw, active_start, active_end, risk_free_rate_ann=rf_annual
                )
            if df_bench.empty:
                bench_unavailable_reason = "The selected peer fund has no NAV data in the selected window."

        # Benchmark relative factor regression metrics
        b_metrics = quant_analytics.compute_benchmark_relative_metrics(df_fund, df_bench, risk_free_rate_ann=rf_annual)
        if not b_metrics and not bench_unavailable_reason:
            bench_unavailable_reason = "Fewer than 5 trading days overlap between this fund and the benchmark in the selected window."

        if bench_unavailable_reason:
            theme.render_banner(
                f"⚠️ <b>Benchmark unavailable ({bench_label}):</b> {bench_unavailable_reason} "
                "Beta, Alpha, Tracking Error, Information Ratio, R², and Market Capture below are not computed — "
                "widen the time horizon, or pick a different benchmark mode above.",
                level="warning",
            )

        # --- 1. SCHEME IDENTITY HEADER ---
        st.markdown(f"### 📑 {profile['scheme_name']}")
        c_m1, c_m2, c_m3, c_m4 = st.columns(4)
        c_m1.caption(f"🏛️ **Fund House**: {profile['fund_house']}")
        c_m2.caption(f"🎯 **Category**: {profile['category']} ({profile.get('broad_category', 'Asset Class')})")
        c_m3.caption(f"📋 **Structure**: {profile['plan_type']} • {profile['option_type']}")
        c_m4.caption(f"🗓️ **Quant Sample**: {cov_fund['n_trading_days']:,} Trading Days ({cov_fund['actual_start'].strftime('%d-%b-%Y')} to {cov_fund['actual_end'].strftime('%d-%b-%Y')})")
        
        # Scheme Cost & Liquidity Economics
        ter_raw = profile.get("expense_ratio")
        ter_pct = float(ter_raw) if ter_raw is not None and not pd.isna(ter_raw) else None
        ter_is_official = profile.get("ter_status") == "official"
        ter_status = profile.get("ter_status") or "unknown"
        exit_status = profile.get("exit_rule_status") or "unknown"
        exit_desc = profile.get("exit_load_description") or "Exit-load rule unavailable."
        lock_raw = profile.get("lock_in_years")
        lock_in = int(lock_raw) if lock_raw is not None and not pd.isna(lock_raw) else None

        c_c1, c_c2, c_c3 = st.columns([1.1, 1.2, 2.2])
        ter_label = f"{ter_pct:.4f}% p.a." if ter_pct is not None else "Unavailable"
        c_c1.caption(f"💳 **Expense Ratio (TER)**: `{ter_label}` · {ter_status}")
        c_c2.caption(f"🚪 **Exit Load Penalty**: Transaction-lot calculation required · {exit_status}")
        lock_label = f"Lock-in: {lock_in} Years" if lock_in is not None else "Lock-in not verified"
        c_c3.caption(f"📜 **Exit Rule**: {exit_desc} &nbsp;•&nbsp; **{lock_label}**")
        
        # Coverage notification if requested window exceeds earliest available data
        if cov_fund.get("is_partial"):
            st.info(
                f"ℹ️ **Sample Coverage Notice**: Requested date window is **{cov_fund['requested_days']} calendar days** "
                f"({active_start.strftime('%d-%b-%Y')} to {active_end.strftime('%d-%b-%Y')}), but the earliest available NAV record for this scheme in the database is "
                f"**{cov_fund['actual_start'].strftime('%d-%b-%Y')}**. All quantitative factor regressions, risk metrics, and drawdown curves are strictly computed across the "
                f"**{cov_fund['n_trading_days']} available trading days** ({cov_fund['actual_start'].strftime('%d-%b-%Y')} to {cov_fund['actual_end'].strftime('%d-%b-%Y')})."
            )
        
        st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)
        
        # --- 2. TOP QUANTITATIVE FACTOR KPI CARDS ---
        k_c1, k_c2, k_c3, k_c4, k_c5, k_c6 = st.columns(6)
        
        with k_c1:
            sharpe_val = q_metrics.get("sharpe_ratio", 0.0)
            s_col = "#10B981" if sharpe_val > 1.0 else ("#2563EB" if sharpe_val > 0.5 else ("#F59E0B" if sharpe_val >= 0 else "#EF4444"))
            st.markdown(f"""
                <div class="quant-card">
                    <div class="quant-title">
                        <span>Sharpe Ratio</span>
                        <span class="info-icon tooltip-align-left" title="Sharpe Ratio (Annualized)&#10;Formula: (R_p - R_f) / σ_p&#10;Calculation: (Mean(R - Rf_daily) / σ_daily) × √252.&#10;Measures excess return earned per unit of total volatility. R_f is the risk-free rate. Values > 1.0 indicate strong risk-adjusted returns.">
                            i
                            <span class="tooltip-box">
                                <strong>Sharpe Ratio (Annualized)</strong><br>
                                <span class="tooltip-formula">(R<sub>p</sub> - R<sub>f</sub>) / σ<sub>p</sub></span><br>
                                Measures excess return earned per unit of total risk: <code>(Mean(R - R<sub>f,daily</sub>) / σ<sub>daily</sub>) × √252</code>.<br>
                                • &gt; 1.0: Strong / Optimal<br>
                                • 0.5 – 1.0: Acceptable<br>
                                • &lt; 0.5: Weak risk-adjusted return
                            </span>
                        </span>
                    </div>
                    <div class="quant-value" style="color: {s_col};">{sharpe_val:.4f}</div>
                    <div class="quant-sub">Rf: {rf_pct:.2f}% (Annualized)</div>
                </div>
            """, unsafe_allow_html=True)
            
        with k_c2:
            sortino_val = q_metrics.get("sortino_ratio", 0.0)
            so_col = "#10B981" if sortino_val > 1.5 else ("#2563EB" if sortino_val > 0.8 else "#EF4444")
            st.markdown(f"""
                <div class="quant-card">
                    <div class="quant-title">
                        <span>Sortino Ratio</span>
                        <span class="info-icon tooltip-align-left" title="Sortino Ratio&#10;Formula: (CAGR - R_f) / Downside Semi-Deviation&#10;Calculation: (CAGR - R_f) / (√(Σ(min(0, R - Rf_daily)²) / N) × √252).&#10;Measures excess return per unit of harmful downside risk, ignoring upside volatility.">
                            i
                            <span class="tooltip-box">
                                <strong>Sortino Ratio</strong><br>
                                <span class="tooltip-formula">(CAGR - R<sub>f</sub>) / σ<sub>downside</sub></span><br>
                                Measures excess return per unit of downside risk. Only penalizes negative volatility below R<sub>f</sub> (Downside Semi-Deviation × √252), rewarding positive upward surges.
                            </span>
                        </span>
                    </div>
                    <div class="quant-value" style="color: {so_col};">{sortino_val:.4f}</div>
                    <div class="quant-sub">Penalizes Downside Only</div>
                </div>
            """, unsafe_allow_html=True)
            
        with k_c3:
            has_bench = bool(b_metrics)
            beta_val = b_metrics.get("beta", 1.0)
            beta_display = f"{beta_val:.4f}" if has_bench else "N/A"
            b_sub = f"vs {bench_label[:14]}" if has_bench else "Benchmark unavailable"
            st.markdown(f"""
                <div class="quant-card">
                    <div class="quant-title">
                        <span>Market Beta (β)</span>
                        <span class="info-icon" title="Market Beta (β)&#10;Formula: Cov(R_fund, R_bench) / Var(R_bench)&#10;Calculation: Linear regression slope over overlapping trading days.&#10;Measures systematic sensitivity to benchmark movements. β = 1.0 mirrors benchmark; β > 1.0 is aggressive; β < 1.0 is defensive.">
                            i
                            <span class="tooltip-box">
                                <strong>Market Beta (β)</strong><br>
                                <span class="tooltip-formula">Cov(R<sub>p</sub>, R<sub>m</sub>) / Var(R<sub>m</sub>)</span><br>
                                Measures systematic sensitivity relative to benchmark over overlapping trading days:<br>
                                • β = 1.0: Moves in lockstep with benchmark<br>
                                • β &gt; 1.0: High volatility / aggressive<br>
                                • β &lt; 1.0: Lower volatility / defensive
                            </span>
                        </span>
                    </div>
                    <div class="quant-value">{beta_display}</div>
                    <div class="quant-sub">{b_sub}</div>
                </div>
            """, unsafe_allow_html=True)

        with k_c4:
            alpha_val = b_metrics.get("alpha_annualized_pct", 0.0)
            alpha_display = f"{alpha_val:+.4f}%" if has_bench else "N/A"
            gross_alpha_val = alpha_val + ter_pct if has_bench and ter_is_official and ter_pct is not None else None
            gross_alpha_text = f"{gross_alpha_val:+.4f}%" if gross_alpha_val is not None else "Unavailable"
            ter_for_formula = f"{ter_pct:.4f}%" if ter_is_official and ter_pct is not None else "dated official TER required"
            a_col = "#10B981" if has_bench and alpha_val > 0 else ("#EF4444" if has_bench else "#94A3B8")
            st.markdown(f"""
                <div class="quant-card">
                    <div class="quant-title">
                        <span>Jensen's Alpha</span>
                        <span class="info-icon tooltip-align-right" title="Jensen's Alpha (Net vs Gross)&#10;Net α = (R_p - R_f) - β × (R_m - R_f)&#10;Gross α = Net α + TER%&#10;Net Alpha is post-TER excess return realized by the investor. Gross Alpha measures true managerial stock-picking skill before fee deduction.">
                            i
                            <span class="tooltip-box">
                                <strong>Jensen's Alpha (Net vs Gross)</strong><br>
                                <span class="tooltip-formula">Net α = (R<sub>p</sub> - R<sub>f</sub>) - β(R<sub>m</sub> - R<sub>f</sub>)</span><br>
                                • <strong>Net Alpha</strong>: Excess annualized return realized by investor after deducting daily TER.<br>
                                • <strong>Gross Alpha</strong>: requires a dated official TER. This dashboard does not infer it from a category average.
                            </span>
                        </span>
                    </div>
                    <div class="quant-value" style="color: {a_col};">{alpha_display} <span style="font-size: 0.78rem; font-weight: 500; color: #94A3B8;">(Net)</span></div>
                    <div class="quant-sub" title="Gross Alpha = Net Alpha + dated official TER ({ter_for_formula})">{"Gross Alpha: " + gross_alpha_text if has_bench else "Benchmark unavailable"}</div>
                </div>
            """, unsafe_allow_html=True)
            
        with k_c5:
            mdd_val = q_metrics.get("max_drawdown_pct", 0.0)
            calmar_raw = q_metrics.get('calmar_ratio', 0.0)
            calmar_display = f"{calmar_raw:.2f}" if isinstance(calmar_raw, (int, float)) else "N/A (no drawdown)"
            st.markdown(f"""
                <div class="quant-card">
                    <div class="quant-title">
                        <span>Max Drawdown</span>
                        <span class="info-icon tooltip-align-right" title="Maximum Drawdown (MDD)&#10;Formula: Min((NAV_t - Peak_t) / Peak_t)&#10;Calculation: Largest peak-to-trough decline experienced before a new high is reached within the active window.&#10;Calmar Ratio = CAGR / |Max Drawdown|.">
                            i
                            <span class="tooltip-box">
                                <strong>Maximum Drawdown & Calmar</strong><br>
                                <span class="tooltip-formula">Min((NAV<sub>t</sub> - Peak<sub>t</sub>) / Peak<sub>t</sub>)</span><br>
                                The deepest percentage decline from a historical peak within the selected date window.<br>
                                <strong>Calmar Ratio</strong> = <code>CAGR / |MDD|</code> (return generated per unit of maximum drawdown risk).
                            </span>
                        </span>
                    </div>
                    <div class="quant-value" style="color: #EF4444;">{mdd_val:.4f}%</div>
                    <div class="quant-sub" title="Calmar Ratio = CAGR / |Max Drawdown|">Calmar: {calmar_display}</div>
                </div>
            """, unsafe_allow_html=True)
            
        with k_c6:
            var_val = q_metrics.get("var_95_daily_pct", 0.0)
            cvar_val = q_metrics.get("cvar_95_daily_pct", 0.0)
            st.markdown(f"""
                <div class="quant-card">
                    <div class="quant-title">
                        <span>Daily VaR 95%</span>
                        <span class="info-icon tooltip-align-right" title="Value at Risk (VaR 95%) & CVaR&#10;Formula: Empirical 5th percentile of daily returns.&#10;Calculation: Historical simulation. On 95% of days, loss will not exceed this threshold. CVaR (Expected Shortfall) is the mean loss on the worst 5% crash days.">
                            i
                            <span class="tooltip-box">
                                <strong>Daily 95% VaR & CVaR</strong><br>
                                <span class="tooltip-formula">Empirical 5th Percentile</span><br>
                                On 95% of trading days, the daily loss will not exceed this percentage.<br>
                                <strong>CVaR (Expected Shortfall)</strong>: Average loss on the worst 5% crash days (Basel III Standard).
                            </span>
                        </span>
                    </div>
                    <div class="quant-value" style="color: #EF4444;">{var_val:.4f}%</div>
                    <div class="quant-sub" title="CVaR (Expected Shortfall) = Average loss on days below 95% VaR">CVaR (ES): {cvar_val:.4f}%</div>
                </div>
            """, unsafe_allow_html=True)

        st.markdown("<div style='height: 15px;'></div>", unsafe_allow_html=True)

        # --- 3. STRUCTURED INSTITUTIONAL QUANT TABS ---
        tab_perf, tab_drawdown, tab_capm, tab_rolling, tab_monte = st.tabs([
            "📊 Risk-Adjusted Ratios & Moments",
            "📉 Underwater Drawdown & Tail Risk",
            "🎯 CAPM Regression & Market Capture",
            "📈 Rolling Volatility & Rolling Sharpe",
            "🎲 Monte Carlo Simulation (Predictive GBM)"
        ])
        
        # --- TAB 1: RISK-ADJUSTED RATIOS & MOMENTS ---
        with tab_perf:
            # 1. Comparative Cumulative Return Performance Chart
            st.markdown(f"#### 📈 Cumulative Return Trajectory vs {bench_label}")
            fig_perf = go.Figure()
            
            fig_perf.add_trace(go.Scatter(
                x=df_fund["nav_date"],
                y=df_fund["cum_return"] * 100.0,
                mode="lines",
                name=profile['scheme_name'],
                line=dict(color="#2563EB", width=2.5),
                hovertemplate="%{fullData.name}: <b>%{y:.2f}%</b><extra></extra>"
            ))

            if not df_bench.empty:
                b_perf = df_bench.copy()
                b_perf["cum_return_pct"] = (b_perf["nav"] / b_perf["nav"].iloc[0] - 1.0) * 100.0
                fig_perf.add_trace(go.Scatter(
                    x=b_perf["nav_date"],
                    y=b_perf["cum_return_pct"],
                    mode="lines",
                    name=bench_label,
                    line=dict(color="#10B981", width=2, dash="dash"),
                    hovertemplate="%{fullData.name}: <b>%{y:.2f}%</b><extra></extra>"
                ))

            fig_perf.add_hline(y=0.0, line_dash="dot", line_color="gray")
            fig_perf.update_layout(
                title=f"Cumulative Growth from Day 1 ({cov_fund['actual_start'].strftime('%d-%b-%Y')} to {cov_fund['actual_end'].strftime('%d-%b-%Y')})",
                xaxis_title="Date",
                yaxis_title="Cumulative Return (%)",
                yaxis=dict(ticksuffix="%"),
                hovermode="x unified",
                hoversort="value descending",
                hoverlabel=dict(namelength=-1),
                xaxis=dict(hoverformat="%d-%b-%Y"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                margin=dict(l=10, r=10, t=40, b=10),
                height=340
            )
            st.plotly_chart(fig_perf, width="stretch")
            
            st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)
            
            col_t1_l, col_t1_r = st.columns([1.1, 1.4])
            
            with col_t1_l:
                st.markdown("""#### 📐 Modern Portfolio Theory (MPT) Performance Metrics <span class="info-icon tooltip-align-left" title="Modern Portfolio Theory (MPT) Metrics&#10;All performance metrics, volatilities, and risk-adjusted ratios are annualized across 252 trading days strictly within the active date window.">i<span class="tooltip-box"><strong>Modern Portfolio Theory (MPT) Metrics</strong><br>All performance metrics, volatilities, and risk-adjusted ratios are annualized across 252 trading days strictly within the active date window.</span></span>""", unsafe_allow_html=True)
                mpt_table = pd.DataFrame([
                    {"Metric": "Expense Ratio (TER)", "Value": ter_label, "Calculation / Formula": f"Source confidence: {ter_status}. NAV returns are already net of TER."},
                    {"Metric": "Exit Load Penalty", "Value": "Transaction-lot calculation required", "Calculation / Formula": f"Source confidence: {exit_status}. {exit_desc}"},
                    {"Metric": "Net Jensen's Alpha", "Value": f"{alpha_val:+.4f}%", "Calculation / Formula": "(R_p - R_f) - β(R_m - R_f) (post-TER excess return)"},
                    {"Metric": "Gross Jensen's Alpha", "Value": gross_alpha_text, "Calculation / Formula": f"Net Alpha + dated official TER ({ter_for_formula})"},
                    {"Metric": "Annualized Return (CAGR)", "Value": f"{q_metrics.get('cagr_pct', 0.0):+.4f}%", "Calculation / Formula": (
                        "((NAV_end / NAV_start) ^ (365.25 / Days)) - 1" if q_metrics.get('total_days', 0) >= 30
                        else "Total Return × (365.25 / Days) — linear extrapolation used under 30 days to avoid over-annualizing a short window"
                    )},
                    {"Metric": "Period Total Return", "Value": f"{q_metrics.get('total_return_pct', 0.0):+.4f}%", "Calculation / Formula": f"(NAV_end - NAV_start) / NAV_start ({q_metrics.get('n_trading_days')} trading days)"},
                    {"Metric": "Annualized Volatility (σ)", "Value": f"{q_metrics.get('vol_annualized_pct', 0.0):.4f}%", "Calculation / Formula": "σ(Daily Returns) × √252"},
                    {"Metric": "Downside Semi-Deviation", "Value": f"{q_metrics.get('downside_dev_ann_pct', 0.0):.4f}%", "Calculation / Formula": "√(Σ(min(0, R_i - Rf_daily)²) / N) × √252"},
                    {"Metric": "Sharpe Ratio", "Value": f"{q_metrics.get('sharpe_ratio', 0.0):.4f}", "Calculation / Formula": "(Mean(R - Rf_daily) / σ_daily) × √252"},
                    {"Metric": "Sortino Ratio", "Value": f"{q_metrics.get('sortino_ratio', 0.0):.4f}", "Calculation / Formula": "(CAGR - Rf) / Downside_Semi_Deviation"},
                    {"Metric": "Calmar Ratio", "Value": (f"{q_metrics.get('calmar_ratio', 0.0):.4f}" if isinstance(q_metrics.get('calmar_ratio'), (int, float)) else "N/A (no drawdown)"), "Calculation / Formula": "CAGR / |Max Drawdown|"},
                    {"Metric": "Treynor Ratio", "Value": (f"{b_metrics.get('treynor_ratio', 0.0):.4f}" if has_bench else "N/A"), "Calculation / Formula": "(CAGR - Rf) / Beta, over the fund/benchmark's common trading window"},
                    {"Metric": "Win Rate (% Days)", "Value": f"{q_metrics.get('win_rate_pct', 0.0):.2f}%", "Calculation / Formula": "Count(Days with R > 0) / Total Days × 100%"},
                    {"Metric": "Profit Factor", "Value": f"{q_metrics.get('profit_factor', '-')}", "Calculation / Formula": "|Sum of Positive Returns| / |Sum of Negative Returns|"},
                    {"Metric": "Gain-to-Pain Ratio", "Value": f"{q_metrics.get('gain_to_pain_ratio', '-')}", "Calculation / Formula": "Sum of All Returns / |Sum of Negative Returns|"},
                    {"Metric": "Best Trading Day", "Value": f"{q_metrics.get('best_day_pct', 0.0):+.4f}%", "Calculation / Formula": "Max(Daily Return in window)"},
                    {"Metric": "Worst Trading Day", "Value": f"{q_metrics.get('worst_day_pct', 0.0):+.4f}%", "Calculation / Formula": "Min(Daily Return in window)"},
                    {"Metric": "Average Gain Day", "Value": f"+{q_metrics.get('avg_gain_pct', 0.0):.4f}%", "Calculation / Formula": "Mean(Daily Return | Return > 0)"},
                    {"Metric": "Average Loss Day", "Value": f"{q_metrics.get('avg_loss_pct', 0.0):.4f}%", "Calculation / Formula": "Mean(Daily Return | Return < 0)"},
                ])
                st.dataframe(mpt_table, hide_index=True, width="stretch", height=440)
                
            with col_t1_r:
                st.markdown("#### 🔔 Empirical Return Distribution vs Gaussian Normal Curve")
                st.caption(f"Skewness: **{q_metrics.get('skewness'):.4f}** • Excess Kurtosis: **{q_metrics.get('kurtosis'):.4f}** (Fat tail indicator)")
                
                daily_rets = df_fund["daily_return"].dropna() * 100.0
                mu = daily_rets.mean()
                sigma = daily_rets.std()
                
                # Create histogram
                fig_dist = go.Figure()
                fig_dist.add_trace(go.Histogram(
                    x=daily_rets,
                    nbinsx=40,
                    histnorm="probability density",
                    name="Empirical Returns",
                    marker_color="rgba(30, 58, 138, 0.65)",
                    opacity=0.75
                ))
                
                # Overlay fitted Gaussian Normal Curve
                if sigma > 0:
                    x_norm = np.linspace(daily_rets.min(), daily_rets.max(), 200)
                    y_norm = (1.0 / (sigma * np.sqrt(2 * np.pi))) * np.exp(-0.5 * ((x_norm - mu) / sigma) ** 2)
                    fig_dist.add_trace(go.Scatter(
                        x=x_norm,
                        y=y_norm,
                        mode="lines",
                        name="Fitted Normal (Gaussian)",
                        line=dict(color="#EF4444", width=2.5, dash="dash")
                    ))
                    
                fig_dist.add_vline(x=0, line_color="gray", line_width=1, line_dash="dot")
                fig_dist.add_vline(x=q_metrics.get("var_95_daily_pct", 0), line_color="#EF4444", line_width=1.5, annotation_text="95% VaR", annotation_position="top left")
                
                fig_dist.update_layout(
                    title="Daily Return Distribution & Tail Dispersion",
                    xaxis_title="Daily Return (%)",
                    yaxis_title="Probability Density",
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                    margin=dict(l=10, r=10, t=40, b=10),
                    height=400
                )
                st.plotly_chart(fig_dist, width="stretch")
                
                # Higher Moment Statistical Insight
                sk = q_metrics.get('skewness', 0.0)
                kt = q_metrics.get('kurtosis', 0.0)
                sk_desc = "positively skewed (frequent small losses compensated by large upside surges)" if sk > 0.2 else ("negatively skewed (fat left tail; elevated risk of abrupt crash events)" if sk < -0.2 else "symmetrically distributed")
                kt_desc = f"leptokurtic with excess kurtosis of {kt:.2f} (heavier tails and higher black swan risk than a standard normal distribution)" if kt > 0.5 else "mesokurtic (tail behavior aligns closely with standard Gaussian assumptions)"
                st.info(f"💡 **Statistical Moment Diagnosis**: Fund returns are **{sk_desc}**. The distribution is **{kt_desc}**.")

        # --- TAB 2: UNDERWATER DRAWDOWN & TAIL RISK ---
        with tab_drawdown:
            st.markdown("#### 🌊 Institutional Underwater Drawdown Trajectory")
            st.caption("Illustrates the peak-to-trough decline over time. Demonstrates maximum pain, frequency of drawdowns, and recovery duration.")
            
            fig_dd = go.Figure()
            fig_dd.add_trace(go.Scatter(
                x=df_fund["nav_date"],
                y=df_fund["drawdown_pct"],
                mode="lines",
                name="Drawdown (%)",
                fill="tozeroy",
                line=dict(color="#EF4444", width=1.5),
                fillcolor="rgba(239, 68, 68, 0.25)",
                hovertemplate="<b>%{y:.4f}%</b><extra></extra>"
            ))
            fig_dd.update_layout(
                title=f"Historical Drawdown Profile (Max Drawdown: {q_metrics.get('max_drawdown_pct'):.4f}%)",
                xaxis_title="Date",
                yaxis_title="Drawdown (%)",
                yaxis=dict(ticksuffix="%", zeroline=True, zerolinecolor="black"),
                hovermode="x unified",
                hoverlabel=dict(namelength=-1),
                xaxis=dict(hoverformat="%d-%b-%Y"),
                margin=dict(l=10, r=10, t=40, b=10),
                height=380
            )
            st.plotly_chart(fig_dd, width="stretch")
            
            st.markdown("""#### 🛡️ Value at Risk (VaR) & Expected Shortfall (CVaR) Matrix <span class="info-icon tooltip-align-left" title="Tail Risk Framework&#10;Quantifies maximum expected loss and tail crash severity at 95% confidence level under Basel III standards.">i<span class="tooltip-box"><strong>Tail Risk Framework</strong><br>Quantifies maximum expected loss and tail crash severity at 95% confidence level under Basel III standards.</span></span>""", unsafe_allow_html=True)
            var_c1, var_c2, var_c3, var_c4 = st.columns(4)
            with var_c1:
                st.metric("Daily 95% VaR", f"{q_metrics.get('var_95_daily_pct'):.4f}%", help="Calculation: Historical 5th percentile of daily returns. On 95% of trading days, losses will not exceed this threshold.")
            with var_c2:
                st.metric("Annualized 95% VaR", f"{q_metrics.get('var_95_ann_pct'):.4f}%", help="Calculation: Daily 95% VaR scaled to an annual horizon: VaR_daily × √252.")
            with var_c3:
                st.metric("Daily 95% CVaR (Expected Shortfall)", f"{q_metrics.get('cvar_95_daily_pct'):.4f}%", help="Calculation: Mean of daily returns that fall strictly below the 5% VaR threshold (Basel III Expected Shortfall standard).")
            with var_c4:
                st.metric("Current Drawdown from Peak", f"{q_metrics.get('current_drawdown_pct'):.4f}%", help="Calculation: (Latest NAV - Historical Peak NAV) / Historical Peak NAV × 100%.")

        # --- TAB 3: CAPM FACTOR REGRESSION & BENCHMARK DYNAMICS ---
        with tab_capm:
            col_capm1, col_capm2 = st.columns([1.3, 1.0])
            
            with col_capm1:
                st.markdown(f"""#### 🎯 CAPM Linear Regression vs {bench_label} <span class="info-icon tooltip-align-left" title="CAPM Regression&#10;Formula: R_fund = α + β × R_bench + ε&#10;OLS linear regression across common trading days. β is slope, R² is explained variance.">i<span class="tooltip-box"><strong>CAPM Linear Regression</strong><br><span class="tooltip-formula">R<sub>fund</sub> = α + β × R<sub>bench</sub> + ε</span><br>OLS linear regression computed across common trading days in the active window. β represents systematic market sensitivity; R² indicates the percentage of fund variance explained by the benchmark.</span></span>""", unsafe_allow_html=True)
                reg_pts = b_metrics.get("regression_points")
                if reg_pts is not None and not reg_pts.empty:
                    x_pts = reg_pts["r_bench"] * 100.0
                    y_pts = reg_pts["r_fund"] * 100.0
                    
                    beta_val = b_metrics.get("beta", 1.0)
                    r_sq_val = b_metrics.get("r_squared", 0.0)
                    
                    fig_reg = go.Figure()
                    fig_reg.add_trace(go.Scatter(
                        x=x_pts,
                        y=y_pts,
                        mode="markers",
                        name="Daily Observations",
                        marker=dict(size=6, color="#1E3A8A", opacity=0.6)
                    ))
                    
                    # Fitted regression line
                    x_line = np.linspace(x_pts.min(), x_pts.max(), 100)
                    # Regression line slope is beta
                    intercept = (y_pts.mean() - beta_val * x_pts.mean())
                    y_line = beta_val * x_line + intercept
                    
                    fig_reg.add_trace(go.Scatter(
                        x=x_line,
                        y=y_line,
                        mode="lines",
                        name=f"Regression Fit (β={beta_val:.2f}, R²={r_sq_val:.2f})",
                        line=dict(color="#10B981", width=2.5)
                    ))
                    
                    fig_reg.update_layout(
                        title=f"Factor Regression Line (β = {beta_val:.4f}, R² = {r_sq_val:.4f})",
                        xaxis_title=f"{bench_label} Daily Return (%)",
                        yaxis_title="Fund Daily Return (%)",
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                        margin=dict(l=10, r=10, t=40, b=10),
                        height=400
                    )
                    st.plotly_chart(fig_reg, width="stretch")
                else:
                    st.info("Insufficient overlapping benchmark records to calculate CAPM regression.")
                    
            with col_capm2:
                st.markdown("""#### 🏹 Up / Down Market Capture Ratio <span class="info-icon tooltip-align-right" title="Market Capture Ratio&#10;Up Capture: (Fund return on up days / Bench return on up days) × 100%&#10;Down Capture: (Fund return on down days / Bench return on down days) × 100%&#10;Ratio > 1.0 indicates superior upside asymmetry.">i<span class="tooltip-box"><strong>Market Capture Ratio</strong><br><span class="tooltip-formula">Capture Ratio = Up-Market % / Down-Market %</span><br>• <strong>Up-Market:</strong> <code>(∏(1+R<sub>fund</sub>) - 1) / (∏(1+R<sub>bench</sub>) - 1)</code> for benchmark up-days.<br>• <strong>Down-Market:</strong> Same for benchmark down-days.<br>A ratio &gt; 1.0 indicates the fund gains more during rallies than it loses during sell-offs.</span></span>""", unsafe_allow_html=True)
                st.caption("Evaluates asymmetric performance in rising vs falling market environments.")

                if not b_metrics:
                    st.info("Benchmark unavailable — capture ratios cannot be computed for this window.")
                else:
                    up_cap = b_metrics.get("up_market_capture_pct", 100.0)
                    dn_cap = b_metrics.get("down_market_capture_pct", 100.0)
                    cap_rat = b_metrics.get("capture_ratio", "-")

                    df_capture = pd.DataFrame([
                        {"Environment": "Up-Market Capture", "Capture %": up_cap, "Color": "#10B981"},
                        {"Environment": "Down-Market Capture", "Capture %": dn_cap, "Color": "#EF4444"}
                    ])

                    fig_cap = px.bar(
                        df_capture,
                        x="Environment",
                        y="Capture %",
                        color="Environment",
                        color_discrete_map={"Up-Market Capture": "#10B981", "Down-Market Capture": "#EF4444"},
                        text="Capture %",
                        title=f"Capture Ratio: {cap_rat} (>1.0 indicates superior upside asymmetry)"
                    )
                    fig_cap.add_hline(y=100.0, line_dash="dash", line_color="gray", annotation_text="Benchmark Parity (100%)")
                    fig_cap.update_traces(texttemplate="%{y:.2f}%", textposition="outside")
                    fig_cap.update_layout(showlegend=False, height=330, margin=dict(l=10, r=10, t=40, b=10))
                    st.plotly_chart(fig_cap, width="stretch")

                te_display = f"{b_metrics.get('tracking_error_pct', 0.0):.4f}%" if has_bench else "N/A"
                ir_display = f"{b_metrics.get('information_ratio', 0.0):.4f}" if has_bench else "N/A"
                rsq_display = f"{b_metrics.get('r_squared', 0.0):.4f}" if has_bench else "N/A"
                st.caption(f"• **Net Jensen's Alpha**: `{alpha_display}` (Excess post-TER return earned over CAPM benchmark)")
                st.caption(f"• **Gross Jensen's Alpha**: `{gross_alpha_text}` (requires a dated official TER; it is not inferred from the current NAV history)")
                st.caption(f"• **Tracking Error**: `{te_display}` (Annualized: `σ(R_fund - R_bench) × √252`)")
                st.caption(f"• **Information Ratio**: `{ir_display}` (`(CAGR_fund - CAGR_bench) / Tracking Error`)")
                st.caption(f"• **R-Squared**: `{rsq_display}` (`Corr(R_fund, R_bench)²` - Share of variance driven by benchmark)")

        # --- TAB 4: ROLLING VOLATILITY & ROLLING SHARPE DYNAMICS ---
        with tab_rolling:
            st.markdown("""#### 🔄 Rolling 30-Day Risk & Sharpe Stability <span class="info-icon tooltip-align-left" title="Rolling Risk Dynamics&#10;30-day sliding window of annualized volatility (σ × √252) and Sharpe ratio to detect volatility regime changes.">i<span class="tooltip-box"><strong>Rolling 30-Day Risk Dynamics</strong><br>A sliding 30-trading-day window computes rolling annualized volatility (<code>σ<sub>30d</sub> × √252</code>) and rolling Sharpe ratio (<code>(Mean<sub>30d</sub> - R<sub>f,daily</sub>) / σ<sub>30d</sub> × √252</code>) to reveal regime shifts over time.</span></span>""", unsafe_allow_html=True)
            st.caption("Rolling windows reveal volatility regime changes, structural shifts, and the temporal consistency of alpha generation.")
            
            # Use pre-calculated rolling metrics from continuous history (eliminates empty dead zones)
            df_roll = df_fund.dropna(subset=["rolling_vol_ann"]).copy()
            roll_window = 30
            if df_roll.empty:
                roll_window = 10
                df_roll = quant_analytics.compute_rolling_metrics(df_fund, window=roll_window, risk_free_rate_ann=rf_annual)

            if not df_roll.empty and len(df_roll) >= 2:
                if roll_window != 30:
                    st.caption(f"ℹ️ Fewer than 30 trading days of history available — using a {roll_window}-day rolling window instead (noisier than the standard 30-day window).")
                c_r1, c_r2 = st.columns(2)

                with c_r1:
                    fig_rvol = px.line(
                        df_roll,
                        x="nav_date",
                        y="rolling_vol_ann",
                        labels={"nav_date": "Date", "rolling_vol_ann": "Annualized Volatility (%)"},
                        title=f"Rolling {roll_window}-Day Annualized Volatility (%)"
                    )
                    fig_rvol.update_traces(
                        line_color="#2563EB", line_width=2,
                        hovertemplate="<b>%{y:.4f}%</b><extra></extra>",
                    )
                    fig_rvol.update_layout(
                        hovermode="x unified",
                        xaxis=dict(hoverformat="%d-%b-%Y"),
                        height=360, margin=dict(l=10, r=10, t=40, b=10),
                    )
                    st.plotly_chart(fig_rvol, width="stretch")

                with c_r2:
                    fig_rshp = px.line(
                        df_roll,
                        x="nav_date",
                        y="rolling_sharpe",
                        labels={"nav_date": "Date", "rolling_sharpe": "Rolling Sharpe Ratio"},
                        title=f"Rolling {roll_window}-Day Sharpe Ratio"
                    )
                    fig_rshp.add_hline(y=0, line_dash="dash", line_color="gray")
                    fig_rshp.update_traces(
                        line_color="#10B981", line_width=2,
                        hovertemplate="<b>%{y:.4f}</b><extra></extra>",
                    )
                    fig_rshp.update_layout(
                        hovermode="x unified",
                        xaxis=dict(hoverformat="%d-%b-%Y"),
                        height=360, margin=dict(l=10, r=10, t=40, b=10),
                    )
                    st.plotly_chart(fig_rshp, width="stretch")
            else:
                st.info("Insufficient trading days to calculate rolling metrics.")

        # --- TAB 5: MONTE CARLO PREDICTIVE SIMULATION ---
        with tab_monte:
            st.markdown("""#### 🎲 Geometric Brownian Motion (GBM) Monte Carlo Simulation <span class="info-icon tooltip-align-left" title="GBM Monte Carlo&#10;Stochastic simulation: dS_t = μ S_t dt + σ S_t dW_t&#10;Simulates 500 forward paths over 252 trading days using empirical drift (μ) and volatility (σ).">i<span class="tooltip-box"><strong>GBM Monte Carlo Simulation</strong><br><span class="tooltip-formula">S<sub>t</sub> = S<sub>0</sub> × exp((μ - 0.5σ²)t + σ√t × Z)</span><br>Simulates 500 future asset paths over 252 trading days (1 Year) calibrated to the empirical daily drift (μ) and volatility (σ) observed in the fund.</span></span>""", unsafe_allow_html=True)
            st.caption(
                "Simulates 500 future paths over a 1-year (252 trading day) forward horizon, calibrated on the empirical "
                "drift and volatility of the fund. Uses a fixed random seed, so re-running with identical inputs reproduces "
                "identical paths — this is a deterministic projection from today's inputs, not a live/changing forecast."
            )

            mc_results = quant_analytics.run_monte_carlo_simulation(
                latest_nav=float(profile.get("latest_nav", 10.0)),
                returns=df_fund["daily_return"].dropna().values,
                n_simulations=500,
                n_days=252,
                initial_capital=100000.0
            )

            if mc_results:
                m_c1, m_c2, m_c3, m_c4, m_c5 = st.columns(5)
                m_c1.metric("Prob of Positive Return", f"{mc_results['prob_profit_pct']:.2f}%", help="Calculation: Count(Simulations with Final Capital >= ₹100,000) / 500 × 100%.")
                m_c2.metric("Prob of Beating Inflation (6%)", f"{mc_results['prob_beat_inflation_pct']:.2f}%", help="Calculation: Count(Simulations with Final Capital >= ₹106,000) / 500 × 100%.")
                m_c3.metric("Prob of Beating 12% Return", f"{mc_results['prob_beat_12pct']:.2f}%", help="Calculation: Count(Simulations with Final Capital >= ₹112,000) / 500 × 100%.")
                m_c4.metric("Median Projected Value", f"₹ {mc_results['median_terminal']:,.2f}", delta=f"{((mc_results['median_terminal'] - 100000)/1000):+.2f}k", help="Calculation: 50th percentile terminal portfolio value across all 500 simulated GBM paths.")
                m_c5.metric("VaR 95% (1Y, ₹)", f"₹ {mc_results['var_95_capital']:,.2f}", help="95% Value-at-Risk: the rupee loss from ₹100,000 that simulations exceed only 5% of the time (initial capital minus the 5th-percentile terminal value).")

                # Fan Chart
                fig_mc = go.Figure()
                days_x = mc_results["days"]
                
                # 95th Percentile (Bull Case)
                fig_mc.add_trace(go.Scatter(
                    x=days_x, y=mc_results["p95"],
                    mode="lines",
                    name="95th Percentile (Bull Case)",
                    line=dict(color="rgba(16, 185, 129, 0.4)", width=1.5),
                    hovertemplate="%{fullData.name}: <b>₹ %{y:,.2f}</b><extra></extra>"
                ))
                # 75th Percentile
                fig_mc.add_trace(go.Scatter(
                    x=days_x, y=mc_results["p75"],
                    mode="lines",
                    name="75th Percentile (Optimistic)",
                    line=dict(color="rgba(16, 185, 129, 0.8)", width=1.5),
                    fill="tonexty",
                    fillcolor="rgba(16, 185, 129, 0.15)",
                    hovertemplate="%{fullData.name}: <b>₹ %{y:,.2f}</b><extra></extra>"
                ))
                # 50th Percentile (Median)
                fig_mc.add_trace(go.Scatter(
                    x=days_x, y=mc_results["p50"],
                    mode="lines",
                    name="50th Percentile (Median Path)",
                    line=dict(color="#2563EB", width=3),
                    hovertemplate="%{fullData.name}: <b>₹ %{y:,.2f}</b><extra></extra>"
                ))
                # 25th Percentile
                fig_mc.add_trace(go.Scatter(
                    x=days_x, y=mc_results["p25"],
                    mode="lines",
                    name="25th Percentile (Conservative)",
                    line=dict(color="rgba(239, 68, 68, 0.8)", width=1.5),
                    fill="tonexty",
                    fillcolor="rgba(37, 99, 235, 0.1)",
                    hovertemplate="%{fullData.name}: <b>₹ %{y:,.2f}</b><extra></extra>"
                ))
                # 5th Percentile (Bear Case)
                fig_mc.add_trace(go.Scatter(
                    x=days_x, y=mc_results["p5"],
                    mode="lines",
                    name="5th Percentile (Stress Test)",
                    line=dict(color="rgba(239, 68, 68, 0.4)", width=1.5),
                    fill="tonexty",
                    fillcolor="rgba(239, 68, 68, 0.15)",
                    hovertemplate="%{fullData.name}: <b>₹ %{y:,.2f}</b><extra></extra>"
                ))

                fig_mc.add_hline(y=100000.0, line_dash="dash", line_color="gray", annotation_text="Initial Capital (₹ 100,000)")
                fig_mc.update_layout(
                    title="1-Year Forward Monte Carlo Cone of Probability (₹ 100,000 Initial Capital)",
                    xaxis_title="Forward Trading Days (1 Year = 252 Days)",
                    yaxis_title="Projected Portfolio Value (₹)",
                    hovermode="x unified",
                    hoversort="value descending",
                    hoverlabel=dict(namelength=-1),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                    margin=dict(l=10, r=10, t=40, b=10),
                    height=450
                )
                st.plotly_chart(fig_mc, width="stretch")
            else:
                st.info("Insufficient return records to calibrate Monte Carlo simulation.")
    else:
        st.warning("Selected scheme has no recorded NAV history. Please select another scheme or run backfill.")
