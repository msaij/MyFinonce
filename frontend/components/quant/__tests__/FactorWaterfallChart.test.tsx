import { describe, it, expect } from "vitest";
import {
  FactorWaterfallChart,
  buildFactorWaterfallFigure,
} from "../FactorWaterfallChart";

describe("FactorWaterfallChart - buildFactorWaterfallFigure", () => {
  it("generates valid waterfall trace conforming to institutional oracle specification", () => {
    const fig = buildFactorWaterfallFigure({
      alphaAnnPct: 2.5,
      factorContributions: { market: 12.0, size: 3.0, value: -1.5, momentum: 2.0 },
      feeDragPct: 0.75,
      netReturnPct: 17.25,
    });

    expect(fig.data).toBeDefined();
    expect(fig.data.length).toBe(1);

    const trace = fig.data[0] as any;
    expect(trace.type).toBe("waterfall");
    expect(trace.orientation).toBe("v");
    expect(trace.x).toEqual([
      "Manager Alpha",
      "MARKET Return",
      "SIZE Return",
      "VALUE Return",
      "MOMENTUM Return",
      "TER Fee Drag",
      "Net Return",
    ]);
    expect(trace.measure[trace.measure.length - 1]).toBe("total");
    expect(trace.measure.slice(0, -1).every((m: string) => m === "relative")).toBe(true);
    expect(trace.connector?.line?.color).toBe("rgb(63, 63, 63)");
  });

  it("ensures net return matches alpha + sum(factors) - fee_drag mathematically", () => {
    const fig = buildFactorWaterfallFigure({
      alphaAnnPct: 2.0,
      factorContributions: { market: 10.0, smb: 2.0 },
      feeDragPct: 0.8,
    });

    const trace = fig.data[0] as any;
    const yVals = trace.y;
    // Manager Alpha (2.0) + Market (10.0) + SMB (2.0) - TER (0.8) = 13.2
    expect(yVals[0]).toBe(2.0);
    expect(yVals[yVals.length - 2]).toBe(-0.8);
    expect(yVals[yVals.length - 1]).toBe(13.2);
  });

  it("handles negative alpha and negative factor returns cleanly", () => {
    const fig = buildFactorWaterfallFigure({
      alphaAnnPct: -1.5,
      factorContributions: { value: -3.2 },
      feeDragPct: 0.9,
      netReturnPct: -5.6,
    });

    const trace = fig.data[0] as any;
    expect(trace.y[0]).toBe(-1.5);
    expect(trace.y[1]).toBe(-3.2);
    expect(trace.y[2]).toBe(-0.9);
    expect(trace.y[3]).toBe(-5.6);
  });

  it("supports precomputed waterfallData from backend response", () => {
    const precomputed = {
      labels: ["Alpha", "MKT", "Net Return"],
      values: [1.2, 8.5, 9.7],
      measures: ["relative" as const, "relative" as const, "total" as const],
      text: ["+1.20%", "+8.50%", "9.70%"],
    };

    const fig = buildFactorWaterfallFigure({
      waterfallData: precomputed,
      schemeName: "HDFC Top 100",
    });

    const trace = fig.data[0] as any;
    expect(trace.type).toBe("waterfall");
    expect(trace.x).toEqual(["Alpha", "MKT", "Net Return"]);
    expect(trace.y).toEqual([1.2, 8.5, 9.7]);
    expect(trace.text).toEqual(["+1.20%", "+8.50%", "9.70%"]);
  });

  it("handles backend Plotly figure passthrough and dark mode styling", () => {
    const backendFig = {
      data: [{ type: "waterfall", x: ["Alpha"], y: [2.5] }],
      layout: { title: { text: "Backend Waterfall" } },
    };

    const fig = buildFactorWaterfallFigure({ figure: backendFig });
    expect(fig.data).toEqual(backendFig.data);
    expect(fig.layout?.font).toBeDefined();
  });

  it("exports FactorWaterfallChart React component function", () => {
    expect(typeof FactorWaterfallChart).toBe("function");
  });
});
