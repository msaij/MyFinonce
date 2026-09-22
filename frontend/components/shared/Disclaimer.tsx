/**
 * The app-wide "not investment advice" notice. Shown once, in the sidebar, on
 * every page (see Sidebar.tsx) rather than as a banner at the top of each page:
 * compact, but in the warning colour with an icon so it is never missed.
 */
export function NonAdviceNotice() {
  return (
    <div
      role="note"
      className="rounded-md border px-2.5 py-2 text-[0.7rem] leading-snug"
      style={{ background: "var(--mf-warning-bg)", borderColor: "var(--mf-warning)", borderLeftWidth: 3, color: "var(--mf-fg)" }}
      title="Screener sorts and labels such as “Institutional Alpha Star” or “Value Trap / Laggard” describe a fund versus its category median; they are not recommendations. Returns are NAV-based and exclude exit loads, STT and tax."
    >
      <div className="font-bold" style={{ color: "var(--mf-warning)" }}>
        <span aria-hidden>⚠ </span>Not investment advice
      </div>
      <div className="mt-0.5" style={{ color: "var(--mf-muted)" }}>
        MyFinonce is not a SEBI-registered adviser. Figures come from official AMFI data. Consult a SEBI-registered adviser before investing.
      </div>
    </div>
  );
}
