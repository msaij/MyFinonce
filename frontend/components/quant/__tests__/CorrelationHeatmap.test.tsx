import { describe, it, expect } from "vitest";
import {
  CorrelationHeatmap,
  buildCorrelationHeatmapFigure,
  getCellContrastTextColor,
} from "../CorrelationHeatmap";

describe("CorrelationHeatmap - buildCorrelationHeatmapFigure", () => {
  it("generates valid heatmap trace conforming to institutional oracle specification", () => {
    const corr = [
      [1.0, 0.8, 0.1],
      [0.8, 1.0, 0.2],
      [0.1, 0.2, 1.0],
    ];
    const names = ["FundA", "FundB", "FundC"];
    const fig = buildCorrelationHeatmapFigure({ corrMatrix: corr, assetNames: names });

    expect(fig.data.length).toBe(1);
    const trace = fig.data[0] as any;

    expect(trace.type).toBe("heatmap");
    expect(trace.zmin).toBe(-1.0);
    expect(trace.zmax).toBe(1.0);
    expect(trace.hoverongaps).toBe(false);
    expect(trace.colorscale).toBe("RdBu");
    expect(trace.x).toEqual(names);
    expect(trace.y).toEqual(names);
  });

  it("reorders rows and columns based on HRP clusterOrder quasi-diagonalization", () => {
    const corr = [
      [1.0, 0.2, 0.85],
      [0.2, 1.0, 0.1],
      [0.85, 0.1, 1.0],
    ];
    const names = ["A", "B", "C"];
    const clusterOrder = ["A", "C", "B"]; // Correlated A and C placed adjacent

    const fig = buildCorrelationHeatmapFigure({
      corrMatrix: corr,
      assetNames: names,
      clusterOrder,
    });
    const trace = fig.data[0] as any;

    expect(trace.x).toEqual(clusterOrder);
    expect(trace.y).toEqual(clusterOrder);
    expect(trace.z[0][1]).toBe(0.85); // A x C correlation
    expect(trace.z[0][0]).toBe(1.0);
    expect(trace.z[1][1]).toBe(1.0);
    expect(trace.z[2][2]).toBe(1.0);
  });

  it("strictly enforces WCAG AA dynamic cell text contrast (>= 4.5:1)", () => {
    // Dark cells (deep red: z <= -0.70, deep blue: z >= 0.75) must select #FFFFFF
    expect(getCellContrastTextColor(-1.0)).toBe("#FFFFFF");
    expect(getCellContrastTextColor(-0.85)).toBe("#FFFFFF");
    expect(getCellContrastTextColor(-0.70)).toBe("#FFFFFF");
    expect(getCellContrastTextColor(0.75)).toBe("#FFFFFF");
    expect(getCellContrastTextColor(0.85)).toBe("#FFFFFF");
    expect(getCellContrastTextColor(1.0)).toBe("#FFFFFF");

    // Neutral and transitional cells (-0.70 < z < 0.75) must select #000000
    expect(getCellContrastTextColor(-0.65)).toBe("#000000");
    expect(getCellContrastTextColor(-0.5)).toBe("#000000");
    expect(getCellContrastTextColor(-0.2)).toBe("#000000");
    expect(getCellContrastTextColor(0.0)).toBe("#000000");
    expect(getCellContrastTextColor(0.2)).toBe("#000000");
    expect(getCellContrastTextColor(0.5)).toBe("#000000");
    expect(getCellContrastTextColor(0.70)).toBe("#000000");
  });

  it("generates in-cell annotations with dynamic contrast text colors", () => {
    const corr = [
      [1.0, -0.8],
      [-0.8, 1.0],
    ];
    const names = ["FundX", "FundY"];
    const fig = buildCorrelationHeatmapFigure({ corrMatrix: corr, assetNames: names, showAnnotations: true });

    const annotations = (fig.layout as any).annotations;
    expect(annotations).toBeDefined();
    expect(annotations.length).toBe(4);
    // Diagonal 1.0 should have white text
    expect(annotations[0].font.color).toBe("#FFFFFF");
    // Deep negative -0.8 should have white text
    expect(annotations[1].font.color).toBe("#FFFFFF");

    // Neutral cell 0.25 should have black text (#000000)
    const figNeutral = buildCorrelationHeatmapFigure({
      corrMatrix: [
        [1.0, 0.25],
        [0.25, 1.0],
      ],
      assetNames: ["FundA", "FundB"],
      showAnnotations: true,
    });
    const neutralAnnotations = (figNeutral.layout as any).annotations;
    expect(neutralAnnotations[1].font.color).toBe("#000000");
  });

  it("handles empty or missing matrix gracefully without throwing", () => {
    const emptyFig = buildCorrelationHeatmapFigure({ corrMatrix: [], assetNames: [] });
    expect(emptyFig.data).toEqual([]);
  });

  it("exports CorrelationHeatmap React component function", () => {
    expect(typeof CorrelationHeatmap).toBe("function");
  });
});
