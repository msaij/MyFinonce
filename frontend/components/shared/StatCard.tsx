import { Tone } from "@/lib/format";

/**
 * Port of theme.py's render_metric_card(), unified with the Quant page's
 * separate hand-rolled "quant-card" pattern -- see the migration plan's
 * "Quant Analysis page's StatCard" section. `tone` includes "accent" (blue)
 * alongside pos/neg/warn so a 4-band ladder (Sharpe's >1.0/>0.5/>=0/else) is
 * expressible -- callers needing a value-driven ladder compute the tone
 * themselves via lib/metricThresholds.ts's colorForValue() and pass the
 * result straight through, rather than StatCard owning threshold logic.
 */

export interface StatCardProps {
  title: string;
  value: string;
  /** A string, or two short lines of mixed tone (the box clamps at two lines). */
  sub?: React.ReactNode;
  tone?: Tone | "warn" | "accent" | "";
  subTone?: "pos" | "neg" | "neutral";
  tooltip?: React.ReactNode;
  /** A tint behind the value (e.g. a diverging scale), keeping the value in normal ink. */
  valueBackground?: string | null;
}

export function StatCard({ title, value, sub = "", tone = "", subTone = "neutral", tooltip, valueBackground }: StatCardProps) {
  const toneClass =
    tone === "pos" ? "mf-pos" : tone === "neg" ? "mf-neg" : tone === "warn" ? "mf-warn" : tone === "accent" ? "mf-accent" : "";
  const subClass =
    subTone === "pos" ? "metric-sub-pos" : subTone === "neg" ? "metric-sub-neg" : "metric-sub-neutral";

  return (
    <div className="metric-card">
      <div className="metric-title">
        <span>{title}</span>
        {tooltip}
      </div>
      <div className={`metric-value ${toneClass}`} title={value}>
        {valueBackground ? (
          <span className="rounded-md px-1.5" style={{ background: valueBackground }}>
            {value}
          </span>
        ) : (
          value
        )}
      </div>
      <div className={`metric-sub ${subClass}`} title={typeof sub === "string" ? sub : undefined}>
        {sub}
      </div>
    </div>
  );
}
