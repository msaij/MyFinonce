# Original User Request

## Initial Request — 2026-09-13T05:50:10Z

Revamp the Indian Mutual Funds institutional intelligence platform to BlackRock Aladdin- and Google DeepMind-grade standards, transforming a 27-million-row historical dataset into an institutional multi-factor risk attribution, regime-aware portfolio optimization, and explainable AI synthesis platform.

Working directory: `C:\Users\warco\Documents\indian-mutual-funds`

## Requirements

### R1. Aladdin-Grade Factor Risk Attribution & Tail Stress Testing
- Multi-factor risk decomposition: Implement a robust multi-factor attribution model (Market / Nifty 50, Size / MidSmall spread, Value, and Momentum) calibrated to the Indian equity fund ecosystem.
- Macroeconomic scenario stress testing: Evaluate fund and portfolio drawdowns against historical shock windows (e.g. March 2020 COVID crash, 2022 global inflation rate-hike shock, 2024 volatility spikes).
- Comprehensive tail risk modeling: Calculate 95% and 99% Expected Shortfall (CVaR), Cornish-Fisher adjusted Value at Risk, Maximum Drawdown duration/recovery metrics, and downside capture efficiency.

### R2. Advanced Portfolio Optimization (Hierarchical Risk Parity & Black-Litterman)
- Hierarchical Risk Parity (HRP): Implement machine-learning tree clustering (quasi-diagonalization and recursive bisection) on the asset covariance matrix to overcome classic Markowitz Mean-Variance instability and weight collapse.
- Black-Litterman allocation: Implement Bayesian portfolio construction blending equilibrium market priors with user/analyst tactical views.
- Risk budgeting & diversification metrics: Provide Effective Number of Constituents (ENCB), portfolio concentration metrics (Herfindahl-Hirschman Index), and Sharpe/Sortino efficient frontier overlays.

### R3. Dual-Era Historical TER Synchronization & Fee Drag Attribution
- Complete backfill orchestration: Ingest and reconcile the AMFI TER historical archive spanning both regulatory eras (pre-2024 Reg 52(6A) unstandardized formats and 2026 standardized schemas).
- Fee drag attribution: Compute cumulative expense ratio drag comparing Direct vs. Regular compounding impacts over 1Y, 3Y, 5Y, and 10Y horizons, isolating gross alpha vs net alpha.

### R4. DeepMind-Grade Explainable AI Intelligence & Institutional UX
- Algorithmic fund diagnostic narratives: Deterministically generate natural language qualitative diagnoses explaining factor exposures, risk-adjusted outperformance, style drift, and peer rankings.
- High-performance institutional UI: Interactive factor exposure waterfall, correlation matrix heatmaps, and scenario simulation sliders built with Next.js 14, Plotly, and Tailwind CSS.
- Complete state synchronization: Deep-linkable URL query params across date ranges, benchmark selections, and scheme codes ensuring institutional auditability and sharable analysis states.

## Acceptance Criteria

### Verification & Testing
- [ ] Backend test suite: All pytest test suites (186+ baseline plus newly added factor/HRP/stress-testing modules) pass with 100% success rate (`docker exec mf_backend pytest tests/ -v`).
- [ ] Analytical precision: Numerical calculations for annualization, CAGR, Sharpe, Sortino, Treynor, Alpha, Beta, CVaR, and factor weights match institutional mathematical definitions within 4 decimal places.
- [ ] Database query performance: Lateral join queries on `nav_history` and `summary_table` execute in under 1,000ms for 10-year rolling windows across 25,000+ schemes.
- [ ] Frontend test suite & production build: Next.js frontend builds cleanly (`docker exec mf_frontend npm run build`) with zero TypeScript errors, zero lint warnings, and passes all existing and new Jest tests (`docker exec mf_frontend npm test`).
- [ ] Visual design & contrast: All typography and chart visual tokens conform to WCAG AA contrast standards (>= 4.5:1) in both light and dark mode with high readability.

## 2026-09-16T03:24:17Z

Server restarted and API quota has refreshed. Please resume orchestration, complete Phase 2 (Final Milestone Phase 1 & Phase 2 verification) and Phase 3 (Final Forensic Audit & Victory Audit), and deliver the final report.

## 2026-09-17T05:30:00Z

This is a single self-contained fix; keep it small and focused.

Add interactive 3-state column sorting (Ascending → Descending → Reset to normal) to the table headers in the shared DataTable component used by the Filtered Performance Table on the screener page.

Working directory: C:\Users\warco\OneDrive\Documents\indian-mutual-funds\frontend
Integrity mode: development

## Requirements

### R1. Interactive 3-State Column Sorting
Clicking any column header in the table must cycle through three sorting states:
1. First click: Sort ascending (`asc`).
2. Second click: Sort descending (`desc`).
3. Third click: Reset to original / unsorted order (`normal`).
Clicking a different column immediately initiates sorting on that column in ascending order, resetting any previously active column sort.

### R2. Visual Sort Indicators & Affordance
Each sortable column header must visually indicate its sorting capability and current state:
- An unsorted column presents a clear hover affordance and/or neutral sort indicator.
- An ascending sorted column displays an upward indicator (e.g., ▲ / ↑).
- A descending sorted column displays a downward indicator (e.g., ▼ / ↓).
- Resetting the sort restores the neutral / unsorted header appearance.

### R3. Type-Aware Comparator & Robust Null Handling
Sorting must handle various column data types appropriately:
- Numbers and numeric metrics (NAV, returns, expense ratio, scheme code) sort numerically.
- Dates sort chronologically.
- Text strings sort alphabetically (case-insensitive).
- Null, undefined, or missing values must be handled gracefully without throwing errors and placed consistently at the end of the sorted order.

### R4. Automated Vitest Verification
Create an automated Vitest test suite (`components/shared/DataTable.test.tsx`) covering:
- Cycling through all three states: unsorted → ascending → descending → unsorted.
- Correct row ordering for numeric, string, and date data types in both directions.
- Switching active sort between different columns.
- Exact restoration of original row sequence upon reset.

## Acceptance Criteria

### Sorting Behavior
- [ ] Clicking an unsorted column header sorts rows in ascending order based on that column's values.
- [ ] Clicking an ascending column header flips rows to descending order.
- [ ] Clicking a descending column header removes sorting, restoring the exact original row order.
- [ ] Clicking column B while column A is active sets column B to ascending and deactivates sorting on column A.
- [ ] Null or undefined values do not cause runtime errors and are sorted consistently.

### UI & Affordance
- [ ] Column headers show an active visual indicator (upward arrow/icon for ascending, downward arrow/icon for descending).
- [ ] The visual indicator disappears or returns to neutral state when reset to normal.
- [ ] Column headers indicate clickable affordance (e.g., cursor pointer).

### Verification & Quality
- [ ] Vitest test suite `components/shared/DataTable.test.tsx` passes cleanly via `npm test` with 0 failures.
- [ ] TypeScript check and linting pass with 0 errors via `npm run lint`.

## 2026-09-18T04:00:32Z

This is a single self-contained fix; keep it small and focused.

Fix the Category Rotation graph in the Executive Market Overview & Alpha Intelligence dashboard (`frontend/app/page.tsx`) so that all category names on the Y-axis are fully visible without clipping or cut-off letters.

Working directory: C:\Users\warco\OneDrive\Documents\indian-mutual-funds\frontend
Integrity mode: development

## Requirements

### R1. Full Category Label Visibility on Y-Axis
Ensure all category labels on the Y-axis of the Category Rotation horizontal bar chart ("Top 25 Categories Ranked by Median Return") render completely and clearly:
- Enable `automargin: true` on `yaxis` in the chart layout.
- Configure sufficient left margin padding (`margin.l` - e.g. 240px to 280px, or dynamically sized according to the longest category name) so long AMFI category strings (such as "Equity Scheme - Sectoral/Thematic" or "Hybrid Scheme - Dynamic Asset Allocation or Balanced Advantage") are never clipped on the left edge.
- Ensure tick labels preserve readability across standard desktop and laptop viewport sizes.

### R2. Responsive Layout & Aesthetic Consistency
- Ensure the chart layout integrates smoothly within the card container and AppShell theme without horizontal clipping or overflow bugs.
- Preserve all existing chart capabilities: positive/negative bar coloring (`#10B981` / `#EF4444`), inside percentage labels, and hover templates (`%{y}<br>Median Return: %{x:+.4f}%`).

### R3. Automated Test Verification & Site Accessibility
- Add or update automated Vitest tests (e.g. in `frontend/components/shared/PlotlyChart.test.ts` or a new test suite) verifying that the rotation chart configuration includes `yaxis.automargin` and adequate left margin.
- Verify that the full frontend test suite (`npm test`) passes with 0 failures and `npm run lint` passes with 0 warnings/errors.
- Ensure the Next.js production build (`npm run build`) compiles cleanly and the site remains accessible.

## Acceptance Criteria

### Label Visibility & Layout
- [ ] Category names along the Y-axis of the Category Rotation chart are 100% visible with no letters clipped or pushed outside the visible plot area.
- [ ] The `yaxis` layout configuration has `automargin: true` and an appropriate left margin (`margin.l` >= 240).
- [ ] Long category strings like "Hybrid Scheme - Dynamic Asset Allocation or Balanced Advantage" display in full.

### Chart Behavior & Styling
- [ ] Median return bar colors (green for >= 0, red for < 0) and inside percentage labels remain intact.
- [ ] Hover tooltip displays the full category name and formatted median return without regressions.
- [ ] The chart resizes cleanly within its container without breaking page layout.

### Verification & Quality
- [ ] Vitest tests pass cleanly via `npm test` with 0 failures.
- [ ] Linting and type checking pass cleanly via `npm run lint` and `tsc --noEmit`.
- [ ] The production application build succeeds with 0 errors.

## 2026-09-18T08:37:02Z

This is a single self-contained fix; keep it small and focused.

Fix the Period Return / Window Return percentage calculation in the Mutual Fund Scheme Screener and across all analytics endpoints (`backend/app/db/queries.py`) so that selecting "All Available", "Past 10 Years", or any custom date window calculates returns accurately across all schemes instead of returning null.

Working directory: C:\Users\warco\OneDrive\Documents\indian-mutual-funds\backend
Integrity mode: development

## Requirements

### R1. Accurate Period Return Calculation Across All Date Windows
In `backend/app/db/queries.py`, eliminate the artificial `(CAST(%s AS date) - CAST(%s AS date)) >= 3200 AND (pe.nav_date - ps.nav_date) < 3200 THEN NULL` condition that causes schemes younger than ~8.8 years to return `NULL` whenever a large date range (such as "All Available" or "Past 10 Years") is selected.
Ensure `period_return_pct` computes the percentage return over the scheme's available history within the active window:
- Formula: `ROUND((((COALESCE(pe.nav, s.latest_nav) - ps.nav) / NULLIF(ps.nav, 0) * 100.0))::numeric, 4)::double precision`
- Safely returns `NULL` only when a scheme has zero NAV data points in the selected window (`ps.nav IS NULL`) or when `ps.nav <= 0`.
- Update SQL query parameter bindings across all modified functions so placeholders (`%s`) and parameter lists match exactly.

### R2. Scan and Align All Related Query Endpoints
Apply the fix consistently across all affected endpoints in `backend/app/db/queries.py`:
- `get_screener_data`: Fix `period_return_pct` in the screener table rows.
- `get_kpis`: Fix `period_calc` CTE so `advancers`, `decliners`, `unchanged`, `median_return`, `avg_return`, `top_performer`, `lag_performer`, and `best_cat` include all qualifying schemes.
- `get_gainers_losers`: Fix top gainers and losers calculation so schemes are not artificially excluded based on age.
- `get_leaders_laggards`: Fix `scheme_perf` and `cat_median_sql` so category median return, alpha, and quadrant classification cover all active schemes in the window.

### R3. Automated Test Verification & Edge-Case Protection
- Update or create automated tests (in `backend/tests/test_db_queries.py` or a dedicated test suite) testing:
  - "All Available" date range (e.g. 2008 to present): schemes with short histories (e.g., 1-3 years) compute valid non-null returns.
  - "Past 10 Years" and arbitrary custom date ranges: schemes compute accurate returns.
  - Inactive / discontinued schemes with 0 NAV records in the selected window cleanly evaluate to `NULL` without errors or division by zero.
- Ensure the full backend test suite (`pytest`) passes cleanly.

## Acceptance Criteria

### Screener & Period Return Accuracy
- [ ] Querying `/api/screener` with date range set to "All Available" returns valid numeric `period_return_pct` for schemes of any age with NAV records in the window.
- [ ] No scheme with valid NAV data returns `period_return_pct = null` solely because its history is less than 3200 days.
- [ ] Schemes with no NAV history in the window cleanly return `null` without crashing or throwing database errors.

### KPI & Analytics Alignment
- [ ] `/api/screener/kpis` with "All Available" reflects the full universe (e.g. advancers + decliners + unchanged accounts for all active schemes with data, rather than only 14% of the universe).
- [ ] `get_gainers_losers` and `get_leaders_laggards` compute accurately for any date window without crashing or dropping qualifying funds.

### Verification & Quality
- [ ] Parameter bindings match SQL placeholders across all modified query methods with zero `IndexError` or SQL parameter mismatch errors.
- [ ] Automated pytest tests pass with 0 failures.
- [ ] The backend API remains accessible and healthy at `http://localhost:8000/api/health`.


