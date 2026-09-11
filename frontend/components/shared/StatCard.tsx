import { Tone } from "@/lib/format";

/**
 * Port of theme.py's render_metric_card(), unified with the Quant page's
 * separate hand-rolled "quant-card" pattern -- see the migration plan's
 * "Quant Analysis page's StatCard" section. `thresholds` (optional) drives
 * a 4-way color ladder (lib/metricThresholds.ts) instead of the simple
 * pos/neg/warn tone, for metrics like Sharpe that need more than a binary
 * good/bad color.
 */

export interface StatCardProps {
  title: string;
  value: string;
  sub?: string;
  tone?: Tone | "warn" | "";
  subTone?: "pos" | "neg" | "neutral";
  tooltip?: React.ReactNode;
}

export function StatCard({ title, value, sub = "", tone = "", subTone = "neutral", tooltip }: StatCardProps) {
  const toneClass = tone === "pos" ? "mf-pos" : tone === "neg" ? "mf-neg" : tone === "warn" ? "mf-warn" : "";
  const subClass =
    subTone === "pos" ? "metric-sub-pos" : subTone === "neg" ? "metric-sub-neg" : "metric-sub-neutral";

  return (
    <div className="metric-card">
      <div className="metric-title">
        <span>{title}</span>
        {tooltip}
      </div>
      <div className={`metric-value ${toneClass}`} title={value}>
        {value}
      </div>
      <div className={subClass} title={sub}>
        {sub}
      </div>
    </div>
  );
}
