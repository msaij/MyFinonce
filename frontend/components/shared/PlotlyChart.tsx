"use client";

import dynamic from "next/dynamic";
import { useMemo } from "react";

/**
 * Thin wrapper around react-plotly.js. See the migration plan's "Charting"
 * section: the backend serializes its already-correct Python
 * fig.to_plotly_json() {data, layout} directly, since Python and JS Plotly
 * share the same figure-JSON schema by construction -- this component's job
 * is just to render that JSON, not to rebuild chart logic in TypeScript.
 *
 * Loaded dynamically with ssr:false -- plotly.js touches `window` at import
 * time and cannot run during Next.js server-side rendering.
 *
 * Uses plotly.js-cartesian-dist-min, not the full plotly.js-dist-min --
 * every chart in this app (backend-built and frontend-built alike) is one of
 * scatter/bar/pie/histogram, all 2D cartesian trace types. The full bundle
 * also ships 3D, geo/mapbox, gl-accelerated, and finance (candlestick/ohlc)
 * trace types this app never uses; the full bundle measured ~1s to fetch
 * over the wire even after the dev-server-vs-production-build fix, purely
 * from its own size, and the cartesian-only bundle is a large, free trim.
 */
const Plot = dynamic(
  async () => {
    const { default: createPlotlyComponent } = await import("react-plotly.js/factory");
    const Plotly = await import("plotly.js-cartesian-dist-min");
    return createPlotlyComponent(Plotly.default ?? Plotly);
  },
  {
    ssr: false,
    loading: () => (
      <div className="w-full h-full min-h-[340px] flex flex-col items-center justify-center animate-pulse rounded-lg bg-slate-100/40 dark:bg-slate-800/20">
        <div className="w-7 h-7 rounded-full border-2 border-blue-500/30 border-t-blue-500 animate-spin mb-2" />
        <span className="text-xs text-slate-400 font-medium">Loading visualization...</span>
      </div>
    ),
  }
);

export interface PlotlyChartProps {
  figure: { data: unknown[]; layout?: Record<string, unknown> };
  className?: string;
  style?: React.CSSProperties;
}

export function getPlotlyChartLayout(
  figureLayout?: Record<string, unknown>,
  isDark?: boolean,
  figureData?: unknown[]
): Record<string, unknown> {
  const isDarkTheme = isDark ?? (typeof document !== "undefined" && document.documentElement.classList.contains("dark"));
  const textColor = isDarkTheme ? "#f8fafc" : "#0f172a";
  const hoverBg = isDarkTheme ? "#1e293b" : "#ffffff";
  const hoverBorder = isDarkTheme ? "#475569" : "#cbd5e1";

  // If the figure has multiple lines/series and no hovermode was explicitly set, default to 'x unified'
  // so the hover pop-up displays all traces in the exact same top-to-bottom visual order as the lines.
  const hasMultipleLines =
    Array.isArray(figureData) &&
    figureData.filter((t: any) => t && (t.type === "scatter" || !t.type) && (t.mode?.includes?.("lines") || t.stackgroup)).length > 1;

  const defaultHovermode = hasMultipleLines ? "x unified" : figureLayout?.hovermode;

  return {
    autosize: true,
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: textColor },
    hoverlabel: {
      bgcolor: hoverBg,
      bordercolor: hoverBorder,
      font: { color: textColor },
    },
    margin: { t: 30, r: 20, b: 40, l: 50 },
    // By default across all graphs in all pages, sort unified hover popups descending by value
    // so the popup items appear in the exact same top-to-bottom order as the visual lines on the plot.
    hoversort: "value descending",
    ...(defaultHovermode ? { hovermode: defaultHovermode } : {}),
    ...figureLayout,
    ...(figureLayout?.margin
      ? {
          margin: {
            t: 30,
            r: 20,
            b: 40,
            l: 50,
            ...(figureLayout.margin as Record<string, unknown>),
          },
        }
      : {}),
    ...(figureLayout?.hoversort ? {} : { hoversort: "value descending" }),
  };
}

export function PlotlyChart({ figure, className, style }: PlotlyChartProps) {
  const layout = useMemo(
    () => getPlotlyChartLayout(figure?.layout, undefined, figure?.data as unknown[]),
    [figure?.layout, figure?.data]
  );
  const chartHeight = typeof layout.height === "number" && layout.height > 0 ? (layout.height as number) : 360;

  return (
    <div
      className={`relative w-full overflow-hidden ${className ?? ""}`}
      style={{
        width: "100%",
        minHeight: chartHeight,
        height: style?.height ?? chartHeight,
        ...style,
      }}
    >
      <Plot
        data={(Array.isArray(figure?.data) ? figure.data : []) as never}
        layout={{ ...layout, height: chartHeight, autosize: true } as never}
        useResizeHandler
        style={{ width: "100%", height: chartHeight }}
        config={{ responsive: true, displaylogo: false }}
      />
    </div>
  );
}
