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
import portfolio_advisor
import portfolio_sim

st.set_page_config(page_title="Portfolio Suggestion | Indian Mutual Funds", page_icon="\U0001F9ED", layout="wide")
theme.inject_theme()

col_h1, col_h2 = st.columns([3, 2])
with col_h1:
    theme.render_page_header(
        "Portfolio Suggestion",
        "Pick a risk level and a budget; get a model portfolio built from disclosed, quantitative rules against official AMFI historical data."
    )
with col_h2:
    date_picker.render_top_date_picker(current_page="portfolio_suggestion")

active_start, active_end = date_picker.get_active_date_range()

theme.render_banner(
    "<b>This is not personalized investment advice.</b> Indian Mutual Funds Dashboard is not a SEBI-registered "
    "Investment Adviser or Research Analyst. Every allocation and fund pick below is the output of a disclosed, "
    "inspectable rule or formula (see “How this works” at the bottom) applied to official AMFI historical data — "
    "never a personalized recommendation. Past performance and historical backtests are not a guarantee of future "
    "results. Consult a SEBI-registered Investment Adviser before investing.",
    level="warning",
)

# --- STEP 1: RISK PROFILE ---
st.markdown("### 1. Risk Profile")

saved_mode = filter_state.get_filter("portfolio_advisor", "profile_mode", default="Questionnaire")
profile_mode_options = ["Take the Risk Questionnaire", "I know my risk level"]
if "padv_profile_mode_widget" not in st.session_state:
    st.session_state["padv_profile_mode_widget"] = profile_mode_options[0] if saved_mode == "Questionnaire" else profile_mode_options[1]
profile_mode = st.radio("How should we figure out your risk level?", profile_mode_options, key="padv_profile_mode_widget", horizontal=True)
filter_state.set_filter("portfolio_advisor", "profile_mode", "Questionnaire" if profile_mode == profile_mode_options[0] else "QuickSelect")

risk_tier = None
questionnaire_score = None

if profile_mode == profile_mode_options[0]:
    with st.form("padv_questionnaire_form"):
        st.caption("Answer honestly — this is the same style of behavioral risk-tolerance questionnaire real advisers use, since self-picking “aggressive” is known to be unreliable.")
        answers = {}
        for q in portfolio_advisor.RISK_QUESTIONNAIRE:
            saved_answer = filter_state.get_filter("portfolio_advisor", f"q_{q['key']}", default=q["options"][0][0])
            labels = [opt[0] for opt in q["options"]]
            default_idx = labels.index(saved_answer) if saved_answer in labels else 0
            choice = st.radio(q["question"], labels, index=default_idx, key=f"padv_q_{q['key']}")
            answers[q["key"]] = dict(q["options"])[choice]
            filter_state.set_filter("portfolio_advisor", f"q_{q['key']}", choice)
        submitted = st.form_submit_button("Score My Risk Profile", type="primary")
    if submitted or filter_state.get_filter("portfolio_advisor", "computed_tier", default=None):
        score, tier = portfolio_advisor.score_questionnaire(answers)
        filter_state.set_filter("portfolio_advisor", "computed_tier", tier)
        filter_state.set_filter("portfolio_advisor", "computed_score", score)
    risk_tier = filter_state.get_filter("portfolio_advisor", "computed_tier", default=None)
    questionnaire_score = filter_state.get_filter("portfolio_advisor", "computed_score", default=None)
    if risk_tier:
        st.markdown(
            theme.render_status_pill(f"Your score: {questionnaire_score} → {risk_tier}", level="success"),
            unsafe_allow_html=True,
        )
else:
    saved_tier = filter_state.get_filter("portfolio_advisor", "quick_tier", default="Balanced")
    if "padv_quick_tier_widget" not in st.session_state:
        st.session_state["padv_quick_tier_widget"] = saved_tier if saved_tier in portfolio_advisor.RISK_TIERS else "Balanced"
    risk_tier = st.selectbox("Risk Level", options=portfolio_advisor.RISK_TIERS, key="padv_quick_tier_widget")
    filter_state.set_filter("portfolio_advisor", "quick_tier", risk_tier)

if risk_tier:
    alloc = portfolio_advisor.SLEEVE_ALLOCATIONS[risk_tier]
    eq_total = alloc["Equity Core"] + alloc["Equity Satellite"] + alloc["International Equity"]
    st.caption(f"**{risk_tier}** targets ~{eq_total*100:.0f}% total equity, ~{alloc['Debt']*100:.0f}% debt, {alloc['Gold']*100:.0f}% gold, {alloc['Liquid Buffer']*100:.0f}% liquid buffer.")

st.markdown("---")

# --- STEP 2: BUDGET & HORIZON ---
st.markdown("### 2. Budget & Horizon")
col_b1, col_b2, col_b3 = st.columns(3)
with col_b1:
    mode_options = ["Lump Sum", "SIP (Monthly)"]
    saved_inv_mode = filter_state.get_filter("portfolio_advisor", "invest_mode", default=mode_options[0])
    if "padv_mode_widget" not in st.session_state or st.session_state["padv_mode_widget"] not in mode_options:
        st.session_state["padv_mode_widget"] = saved_inv_mode if saved_inv_mode in mode_options else mode_options[0]
    invest_mode = st.selectbox("Investment Mode", options=mode_options, key="padv_mode_widget")
    filter_state.set_filter("portfolio_advisor", "invest_mode", invest_mode)

with col_b2:
    if invest_mode == "Lump Sum":
        saved_amt = filter_state.get_filter("portfolio_advisor", "lump_sum_amount", default=100000)
        if "padv_lump_widget" not in st.session_state:
            st.session_state["padv_lump_widget"] = int(saved_amt)
        budget = st.number_input("Lump Sum Amount (₹)", min_value=1000, max_value=100000000, step=5000, key="padv_lump_widget")
        filter_state.set_filter("portfolio_advisor", "lump_sum_amount", budget)
        sip_amount = 0.0
        lump_sum_amount = float(budget)
    else:
        saved_sip = filter_state.get_filter("portfolio_advisor", "sip_amount", default=5000)
        if "padv_sip_widget" not in st.session_state:
            st.session_state["padv_sip_widget"] = int(saved_sip)
        budget = st.number_input("Monthly SIP Amount (₹)", min_value=500, max_value=1000000, step=500, key="padv_sip_widget")
        filter_state.set_filter("portfolio_advisor", "sip_amount", budget)
        sip_amount = float(budget)
        lump_sum_amount = 0.0

with col_b3:
    saved_horizon = filter_state.get_filter("portfolio_advisor", "horizon_years", default=5)
    if "padv_horizon_widget" not in st.session_state:
        st.session_state["padv_horizon_widget"] = int(saved_horizon)
    horizon_years = st.number_input("Investment Horizon (Years)", min_value=1, max_value=40, step=1, key="padv_horizon_widget")
    filter_state.set_filter("portfolio_advisor", "horizon_years", horizon_years)

if risk_tier:
    warning = portfolio_advisor.suitability_warning(risk_tier, float(horizon_years))
    if warning:
        theme.render_banner(f"⚠️ {html.escape(warning)}", level="warning")

st.markdown("---")

# --- STEP 3: GENERATE ---
if not risk_tier:
    st.info("Complete Step 1 to see your risk tier before generating a portfolio.")
    st.stop()

budget_amount = float(budget)


@st.cache_data(ttl=600, show_spinner=False)
def _cached_rules_based(tier, start, end, budget, data_version):
    return portfolio_advisor.build_rules_based_portfolio(tier, start, end, budget)


@st.cache_data(ttl=600, show_spinner=False)
def _cached_mvo(tier, start, end, budget, _rules_result, data_version):
    # _rules_result is excluded from hashing (DataFrame-bearing, slow/fragile to hash) but is
    # itself a deterministic function of (tier, start, end, budget, data_version) via
    # _cached_rules_based above — those four already correctly key this cache entry.
    return portfolio_advisor.build_mvo_portfolio(tier, start, end, budget, _rules_result)


@st.cache_data(ttl=600, show_spinner=False)
def _cached_backtest(weights_items, start, end, mode, lump, sip, data_version):
    return portfolio_advisor.backtest_portfolio(dict(weights_items), start, end, mode, lump, sip)


generate = st.button("\U0001F680 Generate Suggested Portfolios", type="primary", use_container_width=True)
if generate:
    st.session_state["padv_generated"] = True

if not st.session_state.get("padv_generated"):
    st.info("Set your risk profile and budget above, then click **Generate Suggested Portfolios**.")
    st.stop()

data_version = db.get_data_version()
bt_mode = "SIP" if invest_mode.startswith("SIP") else "Lump Sum"

with st.spinner(f"Screening funds and building your {risk_tier} portfolio..."):
    rules_result = _cached_rules_based(risk_tier, active_start, active_end, budget_amount, data_version)

with st.spinner("Running the mean-variance optimizer..."):
    mvo_result = _cached_mvo(risk_tier, active_start, active_end, budget_amount, rules_result, data_version) if "error" not in rules_result else {"error": "Skipped — rules-based screen failed first."}

if "error" in rules_result:
    theme.render_banner(f"⚠️ {html.escape(rules_result['error'])}", level="danger")
    st.stop()

rules_weights_items = tuple(sorted(rules_result.get("weights", {}).items()))
rules_backtest = _cached_backtest(rules_weights_items, active_start, active_end, bt_mode, lump_sum_amount, sip_amount, data_version)
mvo_backtest = None
if "error" not in mvo_result:
    mvo_weights_items = tuple(sorted(mvo_result.get("weights", {}).items()))
    mvo_backtest = _cached_backtest(mvo_weights_items, active_start, active_end, bt_mode, lump_sum_amount, sip_amount, data_version)


def _render_alloc_chart(result, title):
    target = result.get("target_alloc")
    rows = []
    if target:
        for sleeve, w in target.items():
            if w > 0:
                rows.append({"Sleeve": sleeve, "Weight %": round(w * 100, 2), "Type": "Target"})
    actual_by_sleeve = {}
    for pick in result.get("picks", []):
        if "scheme_code" in pick:
            actual_by_sleeve[pick["sleeve"]] = actual_by_sleeve.get(pick["sleeve"], 0) + pick["weight_pct"]
    for sleeve, w in actual_by_sleeve.items():
        rows.append({"Sleeve": sleeve, "Weight %": round(w, 2), "Type": "Actual (Suggested)"})
    if not rows:
        return
    df_alloc = pd.DataFrame(rows)
    fig = px.bar(df_alloc, x="Sleeve", y="Weight %", color="Type", barmode="group", title=title)
    fig.update_layout(yaxis=dict(ticksuffix="%"), height=380, margin=dict(l=10, r=10, t=40, b=10),
                       legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
    st.plotly_chart(fig, width="stretch")


def _render_picks_table(result):
    rows = []
    for pick in result.get("picks", []):
        if "scheme_code" not in pick:
            if "error" in pick:
                theme.render_banner(f"ℹ️ <b>{html.escape(pick.get('sleeve', ''))}:</b> {html.escape(pick['error'])}", level="info")
            continue
        rows.append({
            "Sleeve": pick["sleeve"], "Fund": pick["scheme_name"], "AMC": pick["fund_house"],
            "Weight %": pick["weight_pct"], "Amount (₹)": pick["amount"],
            "TER %": pick.get("expense_ratio"), "Sharpe": pick.get("sharpe_ratio"),
            "Sortino": pick.get("sortino_ratio"), "Max DD %": pick.get("max_drawdown_pct"),
        })
    if not rows:
        st.warning("No funds could be selected for this method in the current window.")
        return
    df = pd.DataFrame(rows)
    st.dataframe(
        df, hide_index=True, width="stretch",
        column_config={
            "Weight %": st.column_config.NumberColumn(format="%.2f %%"),
            "Amount (₹)": st.column_config.NumberColumn(format="₹ %.2f"),
            "TER %": st.column_config.NumberColumn(format="%.4f %%"),
            "Sharpe": st.column_config.NumberColumn(format="%.4f"),
            "Sortino": st.column_config.NumberColumn(format="%.4f"),
            "Max DD %": st.column_config.NumberColumn(format="%.4f %%"),
        },
    )
    for pick in result.get("picks", []):
        if "scheme_code" in pick:
            with st.expander(f"Why {pick['scheme_name']}?"):
                st.write(pick["why"])
    for note in result.get("folded_notes", []):
        theme.render_banner(f"ℹ️ {html.escape(note)}", level="info")


def _render_backtest(bt, key_prefix):
    if bt is None or "error" in bt:
        msg = bt.get("error", "Could not backtest this portfolio.") if bt else "Could not backtest this portfolio."
        theme.render_banner(f"⚠️ {html.escape(msg)}", level="warning")
        return

    twr = bt["twr_metrics"]
    k1, k2, k3, k4 = st.columns(4)
    with k1:
        theme.render_metric_card("Final Value", f"₹ {bt['final_value']:,.2f}", f"Invested ₹ {bt['total_invested']:,.2f}")
    with k2:
        xirr_pct = bt["money_weighted_xirr_pct"]
        theme.render_metric_card("XIRR (Money-Weighted)", theme.format_signed_pct(xirr_pct) if xirr_pct is not None else "N/A",
                                  "Your actual annualized return", tone=theme.tone_class(xirr_pct) if xirr_pct is not None else "")
    with k3:
        twr_cagr = twr.get("cagr_pct")
        theme.render_metric_card("Time-Weighted CAGR", theme.format_signed_pct(twr_cagr) if twr_cagr is not None else "N/A",
                                  "Strategy return, excludes timing", tone=theme.tone_class(twr_cagr) if twr_cagr is not None else "")
    with k4:
        sharpe = twr.get("sharpe_ratio")
        theme.render_metric_card("Sharpe (TWR)", f"{sharpe:.4f}" if isinstance(sharpe, (int, float)) else "N/A", "Risk-adjusted return")

    k5, k6 = st.columns(2)
    with k5:
        mdd = twr.get("max_drawdown_pct")
        theme.render_metric_card("Max Drawdown (TWR)", f"{mdd:.4f}%" if isinstance(mdd, (int, float)) else "N/A", "Deepest peak-to-trough decline")
    with k6:
        vol = twr.get("vol_annualized_pct")
        theme.render_metric_card("Volatility (TWR, Ann.)", f"{vol:.4f}%" if isinstance(vol, (int, float)) else "N/A", "Annualized standard deviation")

    df_result = bt["df_result"]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df_result["nav_date"], y=df_result["portfolio_value"], mode="lines", name="Portfolio Value", line=dict(color="#2563EB", width=2.5)))
    fig.add_trace(go.Scatter(x=df_result["nav_date"], y=df_result["total_invested"], mode="lines", name="Capital Invested", line=dict(color="#94A3B8", width=1.5, dash="dash")))
    fig.update_layout(hovermode="x unified", xaxis=dict(showgrid=True, hoverformat="%d-%b-%Y"),
                       yaxis=dict(showgrid=True, tickprefix="₹ ", tickformat=","), height=400,
                       margin=dict(l=10, r=10, t=20, b=10),
                       legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
    st.plotly_chart(fig, width="stretch", key=f"{key_prefix}_growth_chart")


tab_rules, tab_mvo, tab_compare = st.tabs(["\U0001F4D0 Rules-Based Model Portfolio", "\U0001F4CA Efficient-Frontier Optimized", "⚖️ Compare Both"])

with tab_rules:
    st.caption("Strategic asset allocation by risk tier, then the top risk-adjusted-quality fund per sleeve — see “Why this fund?” below each pick.")
    _render_alloc_chart(rules_result, "Target vs. Actual Allocation")
    _render_picks_table(rules_result)
    st.markdown("#### Historical Backtest")
    _render_backtest(rules_backtest, "rules")

with tab_mvo:
    st.caption("Mean-variance optimization (max historical Sharpe, long-only) over the rules-based screen's own top candidates per sleeve — not the raw fund universe.")
    if "error" in mvo_result:
        theme.render_banner(f"⚠️ {html.escape(mvo_result['error'])}", level="warning")
    else:
        theme.render_banner(
            f"ℹ️ Historical realized return stands in for “expected return” here — a well-known simplification. "
            f"Achieved annualized volatility: <b>{mvo_result.get('achieved_vol_pct', 0):.4f}%</b> "
            f"(tier ceiling: ~{mvo_result.get('vol_ceiling_pct', 0):.0f}%"
            + ("" if mvo_result.get("vol_ceiling_applied", True) else ", relaxed — infeasible for this candidate set") + ").",
            level="info",
        )
        _render_alloc_chart(mvo_result, "Optimizer Allocation")
        _render_picks_table(mvo_result)
        st.markdown("#### Historical Backtest")
        _render_backtest(mvo_backtest, "mvo")

with tab_compare:
    if "error" in mvo_result or mvo_backtest is None or "error" in mvo_backtest:
        st.info("The optimizer didn't produce a comparable portfolio for this window — see the Efficient-Frontier tab for details.")
    else:
        rows = []
        for label, bt in [("Rules-Based", rules_backtest), ("Efficient-Frontier", mvo_backtest)]:
            if bt and "error" not in bt:
                twr = bt["twr_metrics"]
                rows.append({
                    "Method": label,
                    "XIRR %": bt.get("money_weighted_xirr_pct"),
                    "TWR CAGR %": twr.get("cagr_pct"),
                    "Sharpe": twr.get("sharpe_ratio"),
                    "Volatility %": twr.get("vol_annualized_pct"),
                    "Max Drawdown %": twr.get("max_drawdown_pct"),
                })
        if rows:
            df_cmp = pd.DataFrame(rows)
            st.dataframe(
                df_cmp, hide_index=True, width="stretch",
                column_config={c: st.column_config.NumberColumn(format="%.4f") for c in df_cmp.columns if c != "Method"},
            )

            fig_scatter = go.Figure()
            for r in rows:
                fig_scatter.add_trace(go.Scatter(
                    x=[r["Volatility %"]], y=[r["TWR CAGR %"]], mode="markers+text",
                    text=[r["Method"]], textposition="top center",
                    marker=dict(size=16, color="#2563EB" if r["Method"] == "Rules-Based" else "#DC2626"),
                    name=r["Method"],
                ))
            fig_scatter.update_layout(
                title="Risk vs. Return — Both Constructed Portfolios",
                xaxis=dict(title="Annualized Volatility (TWR) %", ticksuffix="%"),
                yaxis=dict(title="Time-Weighted CAGR %", ticksuffix="%"),
                height=420, margin=dict(l=10, r=10, t=40, b=10), showlegend=False,
            )
            st.plotly_chart(fig_scatter, width="stretch")

st.markdown("---")

with st.expander("\U0001F4D6 How this works — full methodology"):
    st.markdown("#### Strategic Asset Allocation by Risk Tier")
    df_sleeve = pd.DataFrame(portfolio_advisor.SLEEVE_ALLOCATIONS).T * 100.0
    st.dataframe(df_sleeve.round(1), width="stretch", column_config={c: st.column_config.NumberColumn(format="%.1f %%") for c in df_sleeve.columns})
    st.caption(
        "The Debt sleeve's own credit quality stays conservative at every tier (Liquid/Overnight/Money-Market/"
        "Ultra-Short/Low-Duration/Short-Duration/Corporate-Bond/Banking-&-PSU only — Credit Risk and Gilt/Dynamic-"
        "Bond categories are excluded at every tier). Equity weight and the Equity-Satellite mix are the risk dial, "
        "not debt credit quality — a safety sleeve that takes on credit risk defeats its own purpose."
    )
    st.markdown("#### Fund Scoring (Rules-Based Method)")
    st.write(
        f"Within each sleeve, candidates are filtered to **active, Direct-plan, Growth-option** schemes with at "
        f"least ~3 years of track record, then ranked by a disclosed composite z-score: "
        f"Sharpe {portfolio_advisor.SCORE_WEIGHTS['sharpe']*100:.0f}%, "
        f"Sortino {portfolio_advisor.SCORE_WEIGHTS['sortino']*100:.0f}%, "
        f"return vs. sleeve peers {portfolio_advisor.SCORE_WEIGHTS['alpha_vs_sleeve_median']*100:.0f}%, "
        f"max drawdown (inverted) {portfolio_advisor.SCORE_WEIGHTS['max_drawdown']*100:.0f}%, "
        f"expense ratio (inverted) {portfolio_advisor.SCORE_WEIGHTS['expense_ratio']*100:.0f}%. "
        "A fund from an AMC already used in another sleeve is skipped in favor of the next-ranked candidate, to avoid concentration."
    )
    st.markdown("#### Mean-Variance Optimization")
    st.write(
        "Solves for the long-only weighting that maximizes historical Sharpe ratio across the rules-based screen's "
        "own top-3-per-sleeve candidates (never the raw universe — optimizing over thousands of noisy historical "
        "series is a well-known way to get an unstable, overfit result). Each fund's weight is bounded near its "
        "sleeve's rules-based target (±15 percentage points) and capped at 40% of the portfolio, and the whole "
        "portfolio's volatility is constrained to the tier's target ceiling. Historical CAGR stands in for “expected "
        "return” — a standard simplification, and one reason this method is shown *alongside* the rules-based one "
        "rather than in place of it."
    )
    st.markdown("#### Known Limitations")
    st.write(
        "- No fund AUM/size or manager-tenure data exists in this database, so scoring can't weight by those.\n"
        "- No minimum-investment-amount data per scheme; a flat ₹500 floor is assumed for the small-budget folding rule.\n"
        "- Tax treatment (equity vs. debt LTCG/STCG rules) is not modeled.\n"
        "- ELSS picks carry a mandatory 3-year lock-in, not separately enforced here.\n"
        "- Backtested performance of the constructed portfolio describes the past, not a forecast."
    )
