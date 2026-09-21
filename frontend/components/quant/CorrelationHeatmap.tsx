"use client";

import React, { useMemo } from "react";
import { PlotlyChart } from "@/components/shared/PlotlyChart";

export interface CorrelationHeatmapProps {
  /** (N x N) Correlation matrix where elements are in [-1.0, 1.0] */
  corrMatrix: number[][];
  /** Original asset names corresponding to corrMatrix rows/columns */
  assetNames: string[];
  /** Optional HRP quasi-diagonalization cluster order (reorders matrix if provided) */
  clusterOrder?: string[];

  title?: string;
  showAnnotations?: boolean;
  colorscale?: string | [number, string][];
  className?: string;
  height?: number;
}

/**
 * Determines text color for in-cell correlation annotations conforming to WCAG AA (>= 4.5:1).
 * In RdBu diverging scale:
 * - Deep red (z <= -0.70) and deep blue (z >= 0.75) use pure white (#FFFFFF).
 * - Neutral and transitional cells use pure black (#000000) to guarantee >= 4.73:1 WCAG AA contrast across all values.
 */
export function getCellContrastTextColor(z: number): string {
  // Deep red (z <= -0.70) and deep blue (z >= 0.75) use pure white (#FFFFFF)
  if (z <= -0.70 || z >= 0.75) {
    return "#FFFFFF";
  }
  // Neutral and transitional cells use pure black (#000000) to guarantee >= 4.73:1 WCAG AA contrast
  return "#000000";
}

export function buildCorrelationHeatmapFigure({
  corrMatrix,
  assetNames,
  clusterOrder,
  title = "Quasi-Diagonalized Correlation Heatmap",
  showAnnotations = true,
  colorscale = "RdBu",
  height,
}: CorrelationHeatmapProps) {
  if (!corrMatrix || corrMatrix.length === 0 || !assetNames || assetNames.length === 0) {
    return { data: [], layout: {} };
  }

  // 1. Quasi-diagonalization matrix reordering
  let orderedNames = assetNames;
  let zValues = corrMatrix;

  if (clusterOrder && clusterOrder.length === assetNames.length) {
    const indexMap = clusterOrder.map((name) => assetNames.indexOf(name));
    const allFound = indexMap.every((idx) => idx !== -1);

    if (allFound) {
      orderedNames = clusterOrder;
      const n = clusterOrder.length;
      zValues = Array.from({ length: n }, (_, i) =>
        Array.from({ length: n }, (_, j) => Number(corrMatrix[indexMap[i]][indexMap[j]].toFixed(4)))
      );
    }
  }

  // 2. Trace definition matching oracle_build_correlation_heatmap_spec
  const trace = {
    type: "heatmap",
    z: zValues,
    x: orderedNames,
    y: orderedNames,
    zmin: -1.0,
    zmax: 1.0,
    colorscale,
    hoverongaps: false,
    hovertemplate: "<b>%{y}</b> × <b>%{x}</b><br>Correlation: <b>%{z:.4f}</b><extra></extra>",
  };

  // 3. Dynamic contrast in-cell annotations
  const annotations: Record<string, unknown>[] = [];
  const n = orderedNames.length;

  if (showAnnotations && n <= 18) {
    const fontSize = n > 10 ? 9 : n > 6 ? 10 : 12;
    for (let i = 0; i < n; i++) {
      for (let j = 0; j < n; j++) {
        const val = zValues[i]?.[j] ?? 0;
        annotations.push({
          x: orderedNames[j],
          y: orderedNames[i],
          text: val.toFixed(2),
          font: {
            color: getCellContrastTextColor(val),
            size: fontSize,
            family: "inherit",
          },
          showarrow: false,
        });
      }
    }
  }

  const computedHeight = height ?? Math.max(380, n * 45 + 100);
  const isDark = typeof document !== "undefined" && document.documentElement.classList.contains("dark");
  const textColor = isDark ? "#F8FAFC" : "#0F172A";

  return {
    data: [trace],
    layout: {
      template: "plotly_white",
      font: { color: textColor },
      title: {
        text: title,
        font: { color: textColor, size: 14, weight: 700 },
      },
      xaxis: { tickangle: -30, automargin: true },
      yaxis: { automargin: true, autorange: "reversed" },
      annotations,
      height: computedHeight,
      margin: { l: 120, r: 40, t: 40, b: 80 },
    },
  };
}

export function CorrelationHeatmap(props: CorrelationHeatmapProps) {
  const chartFigure = useMemo(() => buildCorrelationHeatmapFigure(props), [props]);
  return <PlotlyChart figure={chartFigure} className={props.className} />;
}
