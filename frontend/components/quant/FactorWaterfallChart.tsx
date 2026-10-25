"use client";

import React, { useMemo } from "react";
import { PlotlyChart } from "@/components/shared/PlotlyChart";

export interface FactorWaterfallChartProps {
  /** Manager annualized excess alpha in % (e.g. 2.45) */
  alphaAnnPct?: number;
  /** Factor contributions in % (e.g. { market: 12.0, size: 3.0, value: -1.5, momentum: 2.0 }) */
  factorContributions?: Record<string, number>;
  /** Total TER or fee drag in % (e.g. 0.75) */
  feeDragPct?: number;
  /** Final net return in % (optional; auto-calculated if omitted) */
  netReturnPct?: number;

  /** Precomputed waterfall data from backend /api/quant/{scheme_code}/factors */
  waterfallData?: {
    labels: string[];
    values: number[];
    measures: ("relative" | "total")[];
    text?: string[];
  };

  /** Precomputed Plotly figure from backend figures.factor_waterfall */
  figure?: {
    data: unknown[];
    layout?: Record<string, unknown>;
  };

  schemeName?: string;
  title?: string;
  className?: string;
  height?: number;
}

export function buildFactorWaterfallFigure({
  alphaAnnPct = 0,
  factorContributions = {},
  feeDragPct = 0,
  netReturnPct,
  waterfallData,
  figure,
  schemeName,
  title,
  height = 380,
}: FactorWaterfallChartProps) {
  // 1. If full Plotly figure passed, apply institutional WCAG overrides
  if (figure && figure.data && figure.data.length > 0) {
    const isDark = typeof document !== "undefined" && document.documentElement.classList.contains("dark");
    const textColor = isDark ? "#F8FAFC" : "#0F172A";
    return {
      data: figure.data,
      layout: {
        template: "plotly_white",
        font: { color: textColor, family: "inherit" },
        title: {
          text: title ?? (figure.layout?.title as any)?.text ?? (schemeName ? `Factor Risk Attribution Waterfall — ${schemeName}` : "Institutional Factor Attribution Waterfall"),
          font: { color: textColor, size: 14, weight: 700 },
        },
        height,
        margin: { l: 50, r: 20, t: 40, b: 60 },
        ...figure.layout,
      },
    };
  }

  // 2. If precomputed waterfallData passed, format trace directly
  if (waterfallData && waterfallData.labels && waterfallData.labels.length > 0) {
    const trace = {
      type: "waterfall",
      orientation: "v",
      measure: waterfallData.measures,
      x: waterfallData.labels,
      y: waterfallData.values,
      text: waterfallData.text ?? waterfallData.values.map((v, i) =>
        waterfallData.measures[i] === "total" ? `${v.toFixed(2)}%` : `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`
      ),
      textposition: "outside",
      connector: { line: { color: "rgb(63, 63, 63)", width: 1.5 } },
      increasing: { marker: { color: "#059669" } },
      decreasing: { marker: { color: "#DC2626" } },
      totals: { marker: { color: "#2563EB" } },
      hovertemplate: "<b>%{x}</b><br>Attribution: <b>%{y:+.2f}%</b><extra></extra>",
    };

    const isDark = typeof document !== "undefined" && document.documentElement.classList.contains("dark");
    const textColor = isDark ? "#F8FAFC" : "#0F172A";

    return {
      data: [trace],
      layout: {
        template: "plotly_white",
        font: { color: textColor, family: "inherit" },
        title: {
          text: title ?? (schemeName ? `Factor Risk Attribution Waterfall — ${schemeName}` : "Institutional Factor Attribution Waterfall"),
          font: { color: textColor, size: 14, weight: 700 },
        },
        yaxis: { title: "Annualized Return Contribution (%)", ticksuffix: "%", gridcolor: isDark ? "rgba(148, 163, 184, 0.15)" : "rgba(100, 116, 139, 0.2)" },
        xaxis: { tickangle: -15 },
        height,
        margin: { l: 50, r: 20, t: 40, b: 60 },
      },
    };
  }

  // 3. Construct trace from discrete parameters (matches oracle_build_waterfall_chart_spec)
  const x: string[] = ["Manager Alpha"];
  const y: number[] = [Number(alphaAnnPct.toFixed(4))];
  const measures: ("relative" | "total")[] = ["relative"];

  for (const [fac, contrib] of Object.entries(factorContributions)) {
    x.push(`${fac.toUpperCase()} Return`);
    y.push(Number(contrib.toFixed(4)));
    measures.push("relative");
  }

  if (feeDragPct !== 0 || Object.keys(factorContributions).length > 0) {
    x.push("TER Fee Drag");
    y.push(Number((-Math.abs(feeDragPct)).toFixed(4)));
    measures.push("relative");
  }

  const computedNetReturn = netReturnPct !== undefined
    ? Number(netReturnPct.toFixed(4))
    : Number((alphaAnnPct + Object.values(factorContributions).reduce((a, b) => a + b, 0) - Math.abs(feeDragPct)).toFixed(4));

  x.push("Net Return");
  y.push(computedNetReturn);
  measures.push("total");

  const text = y.map((val, idx) => (measures[idx] === "total" ? `${val.toFixed(2)}%` : `${val >= 0 ? "+" : ""}${val.toFixed(2)}%`));

  const trace = {
    type: "waterfall",
    orientation: "v",
    measure: measures,
    x,
    y,
    text,
    textposition: "outside",
    connector: { line: { color: "rgb(63, 63, 63)", width: 1.5 } },
    increasing: { marker: { color: "#059669" } },
    decreasing: { marker: { color: "#DC2626" } },
    totals: { marker: { color: "#2563EB" } },
    hovertemplate: "<b>%{x}</b><br>Attribution: <b>%{y:+.2f}%</b><extra></extra>",
  };

  const isDark = typeof document !== "undefined" && document.documentElement.classList.contains("dark");
  const textColor = isDark ? "#F8FAFC" : "#0F172A";

  return {
    data: [trace],
    layout: {
      template: "plotly_white",
      font: { color: textColor, family: "inherit" },
      title: {
        text: title ?? (schemeName ? `Factor Risk Attribution Waterfall — ${schemeName}` : "Institutional Factor Attribution Waterfall"),
        font: { color: textColor, size: 14, weight: 700 },
      },
      yaxis: { title: "Annualized Return Contribution (%)", ticksuffix: "%", gridcolor: isDark ? "rgba(148, 163, 184, 0.15)" : "rgba(100, 116, 139, 0.2)" },
      xaxis: { tickangle: -15 },
      height,
      margin: { l: 50, r: 20, t: 40, b: 60 },
    },
  };
}

export function FactorWaterfallChart(props: FactorWaterfallChartProps) {
  const chartFigure = useMemo(() => buildFactorWaterfallFigure(props), [props]);
  return <PlotlyChart figure={chartFigure} className={props.className} />;
}
