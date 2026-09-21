export interface RotationFigureRow {
  category: string;
  median_return: number;
  [key: string]: unknown;
}

/**
 * Safely extracts a finite numeric return value.
 * Handles numbers, numeric strings, NaN, Infinity, -Infinity, -0, null, and undefined.
 */
export function extractNumericReturn(val: unknown): number {
  if (typeof val === "number") {
    if (!Number.isFinite(val)) return 0;
    return Object.is(val, -0) ? 0 : val;
  }
  if (typeof val === "string") {
    const parsed = Number.parseFloat(val.trim());
    if (!Number.isFinite(parsed)) return 0;
    return Object.is(parsed, -0) ? 0 : parsed;
  }
  return 0;
}

/**
 * Computes an appropriate left margin padding for category rotation charts.
 * AMFI category strings range from short ("Equity", "Debt") to ~60 characters
 * ("Hybrid Scheme - Dynamic Asset Allocation or Balanced Advantage").
 * Ensures margin.l is at least 240px (defaulting to 260px up to 280px based on length)
 * so long labels are never clipped along the left edge.
 */
export function calculateRotationLeftMargin(categories: string[] = []): number {
  const safeCategories = Array.isArray(categories) ? categories : [];
  const maxLen = safeCategories.reduce((max, cat) => {
    const raw = typeof cat === "string" ? cat : cat != null ? String(cat) : "";
    const str = raw.trim();
    return Math.max(max, str.length);
  }, 0);
  return Math.max(260, Math.min(280, maxLen * 5 + 30));
}

/**
 * Builds the Plotly horizontal bar chart figure for Category Rotation.
 * Preserves positive/negative coloring, inside percentage labels, and hover templates.
 * Enables automargin: true, type: "category", and dtick: 1 on yaxis, with dynamic margin.l >= 240
 * to eliminate label clipping and prevent tick decimation.
 */
export function buildRotationFigure(rotationRows: RotationFigureRow[] = []) {
  const safeRows = Array.isArray(rotationRows) ? rotationRows : [];
  const top25 = safeRows.slice(0, 25).reverse();
  const categories = top25.map((r) => String(r?.category ?? "").trim());
  const returns = top25.map((r) => extractNumericReturn(r?.median_return));
  const leftMargin = calculateRotationLeftMargin(categories);

  return {
    data: [
      {
        type: "bar",
        orientation: "h",
        x: returns,
        y: categories,
        marker: {
          color: returns.map((val) => (val >= 0 ? "#10B981" : "#EF4444")),
        },
        text: returns.map((val) => `${val >= 0 ? "+" : ""}${val.toFixed(2)}%`),
        textposition: "inside",
        hovertemplate: "<b>%{y}</b><br>Median Return: %{x:+.4f}%<extra></extra>",
      },
    ],
    layout: {
      title: { text: "Top 25 Categories Ranked by Median Return" },
      xaxis: { ticksuffix: "%", showgrid: true, zeroline: true },
      yaxis: {
        automargin: true,
        type: "category",
        dtick: 1,
      },
      margin: { l: leftMargin, r: 20, t: 40, b: 40 },
      height: 600,
    },
  };
}

