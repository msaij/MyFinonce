import { formatDate } from "@/lib/format";
import { riskColor } from "@/lib/riskometer";

/**
 * A fund's SEBI riskometer: a swatch on the shared light-to-dark scale, then the label in
 * ordinary text colour (the colour marks the level; it never carries the text). Hovering
 * gives the date AMFI's figure is from.
 */
export function RiskBadge({ level, asOf, emptyText = "Not published" }: { level?: string | null; asOf?: string | null; emptyText?: string }) {
  const color = riskColor(level);
  if (!level || !color) {
    return (
      <span className="text-xs" style={{ color: "var(--mf-muted)" }} title="AMFI publishes no riskometer for this scheme (common for closed-ended and some index and fund-of-fund schemes).">
        {emptyText}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap" title={asOf ? `SEBI riskometer, as AMFI published it on ${formatDate(asOf)}` : "SEBI riskometer"}>
      <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: color, boxShadow: "inset 0 0 0 1px rgba(0,0,0,0.15)" }} aria-hidden />
      {level}
    </span>
  );
}
