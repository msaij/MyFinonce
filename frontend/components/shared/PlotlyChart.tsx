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
 */
const Plot = dynamic(
  async () => {
    const { default: createPlotlyComponent } = await import("react-plotly.js/factory");
    const Plotly = await import("plotly.js-dist-min");
    return createPlotlyComponent(Plotly.default ?? Plotly);
  },
  { ssr: false }
);

export interface PlotlyChartProps {
  figure: { data: unknown[]; layout?: Record<string, unknown> };
  className?: string;
  style?: React.CSSProperties;
}

export function PlotlyChart({ figure, className, style }: PlotlyChartProps) {
  const layout = useMemo(
    () => ({
      autosize: true,
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      font: { color: "var(--mf-fg)" },
      margin: { t: 30, r: 20, b: 40, l: 50 },
      ...figure.layout,
    }),
    [figure.layout]
  );

  return (
    <div className={className} style={{ width: "100%", ...style }}>
      <Plot
        data={figure.data as never}
        layout={layout as never}
        useResizeHandler
        style={{ width: "100%", height: "100%" }}
        config={{ responsive: true, displaylogo: false }}
      />
    </div>
  );
}
