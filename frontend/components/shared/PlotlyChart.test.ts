import { describe, it, expect } from "vitest";
import { getPlotlyChartLayout } from "./PlotlyChart";

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




