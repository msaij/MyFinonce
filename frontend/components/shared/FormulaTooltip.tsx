/**
 * Port of theme.py's tooltip_icon(). `children` may contain simple HTML
 * (matching the original, which passed pre-built formula HTML strings) --
 * pass a React fragment/JSX for the rich content instead of a raw string
 * where possible; only use dangerouslySetInnerHTML if content is ported
 * verbatim from a trusted, hardcoded original string (never user input).
 */

export function FormulaTooltip({
  children,
  align = "center",
}: {
  children: React.ReactNode;
  align?: "left" | "center" | "right";
}) {
  const alignClass = align === "left" ? "tooltip-align-left" : align === "right" ? "tooltip-align-right" : "";
  return (
    <span className={`info-icon ${alignClass}`}>
      i<span className="tooltip-box">{children}</span>
    </span>
  );
}
