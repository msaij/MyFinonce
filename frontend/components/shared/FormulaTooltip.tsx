/**
 * Port of theme.py's tooltip_icon(). `children` may contain simple HTML
 * (matching the original, which passed pre-built formula HTML strings) --
 * pass a React fragment/JSX for the rich content instead of a raw string
 * where possible; only use dangerouslySetInnerHTML if content is ported
 * verbatim from a trusted, hardcoded original string (never user input).
 */

export interface FormulaTooltipProps {
  children?: React.ReactNode;
  align?: "left" | "center" | "right";
  label?: string;
  formula?: string;
  description?: string;
}

export function FormulaTooltip({
  children,
  align = "center",
  label,
  formula,
  description,
}: FormulaTooltipProps) {
  const alignClass = align === "left" ? "tooltip-align-left" : align === "right" ? "tooltip-align-right" : "";
  return (
    <span className={`info-icon ${alignClass}`}>
      i
      <span className="tooltip-box">
        {label && <strong className="block text-xs font-bold text-slate-900 mb-1.5">{label}</strong>}
        {formula && (
          <code className="block rounded bg-slate-100 border border-slate-300 px-2 py-1.5 text-[11px] font-mono font-semibold text-slate-900 mb-1.5 overflow-x-auto whitespace-pre-wrap">
            {formula}
          </code>
        )}
        {description && <span className="block text-xs leading-relaxed text-slate-700 font-medium mb-1">{description}</span>}
        {children && <span className="block text-xs leading-relaxed text-slate-700 font-medium">{children}</span>}
      </span>
    </span>
  );
}
