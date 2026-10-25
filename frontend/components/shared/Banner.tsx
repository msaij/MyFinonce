/** Port of theme.py's render_banner(). */
export function Banner({
  children,
  level = "info",
}: {
  children: React.ReactNode;
  level?: "info" | "warning" | "danger";
}) {
  return <div className={`mf-banner mf-banner-${level}`}>{children}</div>;
}
