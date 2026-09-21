import { describe, it, expect } from "vitest";
import { getPlotlyChartLayout } from "./PlotlyChart";
import { buildRotationFigure, calculateRotationLeftMargin, extractNumericReturn } from "@/lib/rotationChart";

describe("PlotlyChart getPlotlyChartLayout", () => {
  it("injects hoversort: 'value descending' by default when layout is undefined", () => {
    const layout = getPlotlyChartLayout();
    expect(layout.hoversort).toBe("value descending");
    expect(layout.autosize).toBe(true);
  });

  it("injects hoversort: 'value descending' for unified hovermode layouts", () => {
    const layout = getPlotlyChartLayout({
      height: 450,
      hovermode: "x unified",
      yaxis: { ticksuffix: "%", showgrid: true },
    });
    expect(layout.hovermode).toBe("x unified");
    expect(layout.hoversort).toBe("value descending");
    expect(layout.height).toBe(450);
  });

  it("preserves explicit hoversort if specified", () => {
    const layout = getPlotlyChartLayout({
      hovermode: "x unified",
      hoversort: "value ascending",
    });
    expect(layout.hoversort).toBe("value ascending");
  });

  it("preserves custom paper_bgcolor and font while setting hoversort", () => {
    const layout = getPlotlyChartLayout({
      paper_bgcolor: "#111",
      font: { color: "#fff" },
    });
    expect(layout.paper_bgcolor).toBe("#111");
    expect(layout.font).toEqual({ color: "#fff" });
    expect(layout.hoversort).toBe("value descending");
  });
});

describe("Plotly library hoversort support", () => {
  it("ships Plotly 3.7.0+ with hoversort attribute in plot schema", async () => {
    const Plotly = await import("plotly.js-cartesian-dist-min");
    const rawPlotly = (Plotly as any).default ?? Plotly;
    expect(rawPlotly.version).toBeDefined();
    expect(rawPlotly.version.startsWith("3.")).toBe(true);

    const schema = rawPlotly.PlotSchema ? rawPlotly.PlotSchema.get() : null;
    expect(schema).toBeDefined();
    expect(schema.layout.layoutAttributes.hoversort.values).toEqual([
      "trace",
      "value descending",
      "value ascending",
    ]);
  });

  it("auto-injects hovermode: 'x unified' when multiple line traces exist and hovermode is unset", () => {
    const layout = getPlotlyChartLayout(
      {},
      false,
      [
        { type: "scatter", mode: "lines", name: "Fund 1" },
        { type: "scatter", mode: "lines", name: "Fund 2" },
      ]
    );
    expect(layout.hovermode).toBe("x unified");
    expect(layout.hoversort).toBe("value descending");
  });

  it("preserves custom hovermode even if multiple line traces exist", () => {
    const layout = getPlotlyChartLayout(
      { hovermode: "closest" },
      false,
      [
        { type: "scatter", mode: "lines", name: "Fund 1" },
        { type: "scatter", mode: "lines", name: "Fund 2" },
      ]
    );
    expect(layout.hovermode).toBe("closest");
  });

  it("simulates unified hover sorting with 'value descending' matching top-to-bottom line order", () => {
    // Mimics the exact sorting comparator used by Plotly 3.7.0 for unified hover:
    // mockLegend.entries.sort(function (a, b) {
    //   var hoversort = fullLayout.hoversort;
    //   if (hoversort === 'value descending' || hoversort === 'value ascending') {
    //     var valueLetter = hovermode.charAt(0) === 'x' ? 'y' : 'x';
    //     var aVal = a[0][valueLetter + 'LabelVal'];
    //     var bVal = b[0][valueLetter + 'LabelVal'];
    //     if (aVal !== bVal) {
    //       return hoversort === 'value descending' ? bVal - aVal : aVal - bVal;
    //     }
    //   }
    //   return a[0].trace.index - b[0].trace.index;
    // });
    const hovermode = "x unified";
    const hoversort = "value descending";

    const sortUnifiedHover = (entries: any[]) => {
      return [...entries].sort((a, b) => {
        if (hoversort === "value descending" || hoversort === "value ascending") {
          const valueLetter = hovermode.charAt(0) === "x" ? "y" : "x";
          const aVal = a[0][valueLetter + "LabelVal"];
          const bVal = b[0][valueLetter + "LabelVal"];
          if (aVal !== bVal) {
            return hoversort === "value descending" ? bVal - aVal : aVal - bVal;
          }
        }
        return a[0].trace.index - b[0].trace.index;
      });
    };

    // Scenario 1: Fund A (index 0) = 45%, Fund B (index 1) = 120%, Fund C (index 2) = 15%
    // On the graph, Fund B is at the top (120), Fund A in the middle (45), Fund C at the bottom (15).
    const entries1 = [
      [{ yLabelVal: 45, name: "Fund A", trace: { index: 0 } }],
      [{ yLabelVal: 120, name: "Fund B", trace: { index: 1 } }],
      [{ yLabelVal: 15, name: "Fund C", trace: { index: 2 } }],
    ];
    const sorted1 = sortUnifiedHover(entries1);
    expect(sorted1.map((e) => e[0].name)).toEqual(["Fund B", "Fund A", "Fund C"]);

    // Scenario 2: Fund C surges to 150%, Fund A drops to 10%, Fund B is 80%
    // On the graph, Fund C is at the top (150), Fund B in the middle (80), Fund A at the bottom (10).
    const entries2 = [
      [{ yLabelVal: 10, name: "Fund A", trace: { index: 0 } }],
      [{ yLabelVal: 80, name: "Fund B", trace: { index: 1 } }],
      [{ yLabelVal: 150, name: "Fund C", trace: { index: 2 } }],
    ];
    const sorted2 = sortUnifiedHover(entries2);
    expect(sorted2.map((e) => e[0].name)).toEqual(["Fund C", "Fund B", "Fund A"]);

    // Scenario 3: Negative numbers (e.g. Drawdown or losses: -2% is higher than -25%)
    const entries3 = [
      [{ yLabelVal: -25, name: "Fund Heavy Loss", trace: { index: 0 } }],
      [{ yLabelVal: -2, name: "Fund Mild Loss", trace: { index: 1 } }],
    ];
    const sorted3 = sortUnifiedHover(entries3);
    expect(sorted3.map((e) => e[0].name)).toEqual(["Fund Mild Loss", "Fund Heavy Loss"]);
  });
});

describe("Category Rotation Chart Configuration", () => {
  it("includes yaxis.automargin: true and margin.l >= 240 in layout", () => {
    const mockRows = [
      { category: "Equity Scheme - Large Cap", median_return: 12.5 },
      { category: "Debt Scheme - Liquid Fund", median_return: 6.2 },
    ];
    const figure = buildRotationFigure(mockRows);
    expect(figure.layout.yaxis).toBeDefined();
    expect(figure.layout.yaxis.automargin).toBe(true);
    expect(figure.layout.margin.l).toBeGreaterThanOrEqual(240);
    expect(figure.layout.margin.l).toBeLessThanOrEqual(280);
  });

  it("handles long AMFI category strings like Balanced Advantage with adequate left margin", () => {
    const mockRows = [
      { category: "Hybrid Scheme - Dynamic Asset Allocation or Balanced Advantage", median_return: 8.45 },
      { category: "Equity Scheme - Sectoral/Thematic", median_return: 15.2 },
    ];
    const figure = buildRotationFigure(mockRows);
    expect(figure.layout.yaxis.automargin).toBe(true);
    expect(figure.layout.margin.l).toBeGreaterThanOrEqual(240);
    // Dynamic margin calculation should scale up for the 59-character category string
    expect(figure.layout.margin.l).toBe(280);
    expect(figure.data[0].y).toContain("Hybrid Scheme - Dynamic Asset Allocation or Balanced Advantage");
  });

  it("preserves positive/negative bar colors, percentage inside labels, and hover templates", () => {
    const mockRows = [
      { category: "Top Positive Cat", median_return: 5.6789 },
      { category: "Negative Loss Cat", median_return: -3.21 },
      { category: "Zero Flat Cat", median_return: 0.0 },
    ];
    const figure = buildRotationFigure(mockRows);
    const trace = figure.data[0];

    expect(trace.type).toBe("bar");
    expect(trace.orientation).toBe("h");
    expect(trace.textposition).toBe("inside");
    expect(trace.hovertemplate).toBe("<b>%{y}</b><br>Median Return: %{x:+.4f}%<extra></extra>");

    // Colors: #10B981 for >= 0, #EF4444 for < 0
    // Because top25 is reversed, mockRows[2] (0.0) is first, mockRows[1] (-3.21) is second, mockRows[0] (5.6789) is third
    expect(trace.marker.color).toEqual(["#10B981", "#EF4444", "#10B981"]);
    expect(trace.text).toEqual(["+0.00%", "-3.21%", "+5.68%"]);
    expect(trace.x).toEqual([0.0, -3.21, 5.6789]);
    expect(trace.y).toEqual(["Zero Flat Cat", "Negative Loss Cat", "Top Positive Cat"]);
  });

  it("safely handles empty category list without crashing", () => {
    const figure = buildRotationFigure([]);
    expect(figure.data[0].x).toEqual([]);
    expect(figure.data[0].y).toEqual([]);
    expect(figure.layout.yaxis.automargin).toBe(true);
    expect(figure.layout.margin.l).toBeGreaterThanOrEqual(240);
  });

  it("limits to top 25 categories and places highest median return at the top", () => {
    const rows = Array.from({ length: 30 }, (_, i) => ({
      category: `Category ${i + 1}`,
      median_return: 30 - i, // 30 down to 1
    }));
    const figure = buildRotationFigure(rows);
    const trace = figure.data[0];
    expect(trace.x.length).toBe(25);
    expect(trace.y.length).toBe(25);
    // Sliced top 25 (returns 30 down to 6), reversed -> returns 6 up to 30
    // The top of the chart (last element) is the highest return (30)
    expect(trace.x[24]).toBe(30);
    expect(trace.y[24]).toBe("Category 1");
    expect(trace.x[0]).toBe(6);
  });

  it("integrates with getPlotlyChartLayout preserving automargin and merged margin padding", () => {
    const figure = buildRotationFigure([
      { category: "Hybrid Scheme - Dynamic Asset Allocation or Balanced Advantage", median_return: 8.5 },
    ]);
    const mergedLayout = getPlotlyChartLayout(figure.layout, false, figure.data);
    expect((mergedLayout.yaxis as any)?.automargin).toBe(true);
    expect((mergedLayout.margin as any)?.l).toBeGreaterThanOrEqual(240);
    expect((mergedLayout.margin as any)?.r).toBe(20);
    expect((mergedLayout.margin as any)?.t).toBe(40);
    expect((mergedLayout.margin as any)?.b).toBe(40);
    expect(mergedLayout.autosize).toBe(true);
  });

  it("safely handles null/undefined inputs, missing median_return, and null categories without crashing", () => {
    // 1. Completely undefined/null argument
    const figureDefault = buildRotationFigure(undefined as any);
    expect(figureDefault.data[0].x).toEqual([]);
    expect(figureDefault.data[0].y).toEqual([]);

    // 2. Rows with null, undefined, or NaN median_return and null category
    const rowsWithNulls = [
      { category: null as any, median_return: null as any },
      { category: "Valid Cat", median_return: NaN as any },
      { category: undefined as any, median_return: 4.2 },
    ];
    const figureNulls = buildRotationFigure(rowsWithNulls);
    const trace = figureNulls.data[0];
    expect(trace.x).toEqual([4.2, 0, 0]);
    expect(trace.y).toEqual(["", "Valid Cat", ""]);
    expect(trace.text).toEqual(["+4.20%", "+0.00%", "+0.00%"]);
    expect(trace.marker.color).toEqual(["#10B981", "#10B981", "#10B981"]);

    // 3. calculateRotationLeftMargin with null/undefined array
    expect(calculateRotationLeftMargin(undefined as any)).toBe(260);
    expect(calculateRotationLeftMargin(null as any)).toBe(260);
    expect(calculateRotationLeftMargin([null as any, undefined as any])).toBe(260);
  });

  it("configures xaxis zeroline and ticksuffix for clear positive/negative demarcation", () => {
    const figure = buildRotationFigure([
      { category: "Test Category", median_return: 5.5 },
    ]);
    expect(figure.layout.xaxis.ticksuffix).toBe("%");
    expect(figure.layout.xaxis.showgrid).toBe(true);
    expect(figure.layout.xaxis.zeroline).toBe(true);
  });

  it("extractNumericReturn correctly sanitizes numbers, strings, and non-finite values", () => {
    expect(extractNumericReturn(12.34)).toBe(12.34);
    expect(extractNumericReturn(-5.67)).toBe(-5.67);
    expect(extractNumericReturn("14.5")).toBe(14.5);
    expect(extractNumericReturn("-3.25")).toBe(-3.25);
    expect(extractNumericReturn(0)).toBe(0);
    expect(extractNumericReturn(null)).toBe(0);
    expect(extractNumericReturn(undefined)).toBe(0);
    expect(extractNumericReturn(NaN)).toBe(0);
    expect(extractNumericReturn(Infinity)).toBe(0);
    expect(extractNumericReturn(-Infinity)).toBe(0);
    expect(extractNumericReturn("not-a-number")).toBe(0);
    expect(extractNumericReturn({})).toBe(0);
  });

  it("handles Infinity, -Infinity, and numeric strings in buildRotationFigure without axis corruption", () => {
    const figure = buildRotationFigure([
      { category: "Positive String", median_return: "14.5" as any },
      { category: "Negative String", median_return: "-3.5" as any },
      { category: "Pos Infinity", median_return: Infinity },
      { category: "Neg Infinity", median_return: -Infinity },
    ]);

    const trace = figure.data[0];
    // Sliced and reversed:
    // [Neg Infinity, Pos Infinity, Negative String, Positive String]
    expect(trace.x).toEqual([0, 0, -3.5, 14.5]);
    expect(trace.text).toEqual(["+0.00%", "+0.00%", "-3.50%", "+14.50%"]);
    expect(trace.marker.color).toEqual(["#10B981", "#10B981", "#EF4444", "#10B981"]);
  });

  it("calculateRotationLeftMargin safely handles non-string primitives and objects without NaN", () => {
    const margin = calculateRotationLeftMargin([123 as any, true as any, {} as any]);
    expect(Number.isNaN(margin)).toBe(false);
    expect(margin).toBeGreaterThanOrEqual(240);
    expect(margin).toBeLessThanOrEqual(280);
  });

  it("configures yaxis with type 'category' and dtick 1 to prevent tick decimation and continuous numeric coercion", () => {
    const figure = buildRotationFigure([
      { category: "2026 Target Maturity", median_return: 7.1 },
      { category: "1000 Index", median_return: 8.2 },
    ]);
    expect(figure.layout.yaxis.type).toBe("category");
    expect(figure.layout.yaxis.dtick).toBe(1);
    expect(figure.layout.yaxis.automargin).toBe(true);
    expect(figure.data[0].y).toEqual(["1000 Index", "2026 Target Maturity"]);
  });

  it("extractNumericReturn normalizes -0 and string '-0' to +0 and handles whitespace strings", () => {
    const n0 = extractNumericReturn(-0);
    expect(Object.is(n0, -0)).toBe(false);
    expect(Object.is(n0, 0)).toBe(true);

    const s0 = extractNumericReturn("-0");
    expect(Object.is(s0, -0)).toBe(false);
    expect(Object.is(s0, 0)).toBe(true);

    expect(extractNumericReturn("  15.75  ")).toBe(15.75);
    expect(extractNumericReturn("   ")).toBe(0);
  });

  it("trims leading and trailing whitespace in category names and left margin calculations", () => {
    const figure = buildRotationFigure([
      { category: "  Equity Scheme - Small Cap  ", median_return: 12.0 },
    ]);
    expect(figure.data[0].y[0]).toBe("Equity Scheme - Small Cap");

    const margin = calculateRotationLeftMargin(["  Hybrid Scheme - Balanced Advantage  "]);
    const expectedRaw = calculateRotationLeftMargin(["Hybrid Scheme - Balanced Advantage"]);
    expect(margin).toBe(expectedRaw);
  });
});




