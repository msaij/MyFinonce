"use client";

import { useState } from "react";

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
  /**
   * Position the box against the viewport instead of the icon. Needed inside a
   * scrolling container (a DataTable header): an absolutely positioned box there is
   * clipped by the container's overflow and simply never appears.
   */
  fixed?: boolean;
  /** With `fixed`: roughly how tall the box is, to decide whether it fits below the icon. */
  estHeight?: number;
}

const BOX_WIDTH = 320;
const EST_HEIGHT = 220;

export function FormulaTooltip({
  children,
  align = "center",
  label,
  formula,
  description,
  fixed = false,
  estHeight = EST_HEIGHT,
}: FormulaTooltipProps) {
  const [pos, setPos] = useState<React.CSSProperties | undefined>(undefined);
  const alignClass = align === "left" ? "tooltip-align-left" : align === "right" ? "tooltip-align-right" : "";
  const onEnter = fixed
    ? (e: React.MouseEvent<HTMLSpanElement>) => {
        const r = e.currentTarget.getBoundingClientRect();
        const width = Math.min(BOX_WIDTH, window.innerWidth * 0.9);
        const left = Math.max(8, Math.min(r.left + r.width / 2 - width / 2, window.innerWidth - width - 8));
        // Below the icon unless that would run off the bottom of the screen.
        const below = r.bottom + 8 + estHeight < window.innerHeight;
        setPos({
          position: "fixed", left, width, right: "auto", transform: "none",
          ...(below ? { top: r.bottom + 8, bottom: "auto" } : { bottom: window.innerHeight - r.top + 8, top: "auto" }),
        });
      }
    : undefined;
  return (
    <span className={`info-icon ${alignClass} ${fixed ? "tooltip-fixed" : ""}`} onMouseEnter={onEnter}>
      i
      <span className="tooltip-box normal-case tracking-normal" style={pos}>
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
