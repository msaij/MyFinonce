/** Port of theme.py's render_status_pill(). */
export function StatusPill({
  label,
  level = "success",
}: {
  label: string;
  level?: "success" | "warning" | "danger" | "neutral";
}) {
  return (
    <span className={`mf-pill mf-pill-${level}`}>
      <span className="mf-pill-dot" />
      {label}
    </span>
  );
}
