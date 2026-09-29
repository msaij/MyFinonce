"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { NonAdviceNotice } from "@/components/shared/Disclaimer";
import { getAlertCount } from "@/lib/api/holdings";

/**
 * The left menu: brand, the page links (grouped as the owner set them out) and the
 * not-investment-advice notice.
 *
 * It deliberately does not repeat what a page already shows: the Plan / Option / Window
 * controls and how current the NAVs are sit in the top bar of every page that uses them,
 * each page names its own subject, and the database statistics live on Data Management.
 *
 * At laptop width and above it is fixed on the left; below that it is a drawer AppShell opens.
 */

type IconName = "calculator" | "ipo" | "overview" | "holdings" | "screener" | "compare" | "quant" | "data";

interface NavItem {
  href: string;
  label: string;
  icon: IconName;
  /** Full name, for the tooltip, when the label is shortened. */
  title?: string;
  /** Other paths that belong to this page (a fund's own page sits with fund research). */
  also?: string[];
}

// A group with no title renders its links without a heading: standalone pages that belong to no domain.
const NAV_GROUPS: { title: string | null; items: NavItem[] }[] = [
  { title: null, items: [{ href: "/calculator", label: "Calculator", icon: "calculator" }] },
  { title: "Stock Market", items: [{ href: "/nse-ipo", label: "NSE IPO", icon: "ipo" }] },
  {
    title: "Mutual Funds",
    // Market overview, then your own portfolio, then single-fund research; data upkeep last.
    items: [
      { href: "/", label: "Overview", icon: "overview" },
      { href: "/holdings", label: "Holdings", icon: "holdings" },
      { href: "/screener", label: "Scheme Screener", icon: "screener", also: ["/scheme/"] },
      { href: "/compare", label: "Compare & Simulate", icon: "compare" },
      { href: "/quant", label: "Quant Analysis", icon: "quant", title: "Quantitative MF Analysis" },
      { href: "/admin", label: "Data Management", icon: "data" },
    ],
  },
];

const isActive = (pathname: string, item: NavItem) =>
  pathname === item.href || (item.href !== "/" && pathname.startsWith(item.href)) || !!item.also?.some((p) => pathname.startsWith(p));

/** Simple 18px line icons, drawn in the link's own colour. */
function NavIcon({ name }: { name: IconName }) {
  const paths: Record<IconName, React.ReactNode> = {
    calculator: (
      <>
        <rect x="5" y="3" width="14" height="18" rx="2" />
        <path d="M8 7h8M8 11h.01M12 11h.01M16 11h.01M8 15h.01M12 15h.01M16 15v3M8 18h.01M12 18h.01" />
      </>
    ),
    ipo: <path d="M4 17l5-5 4 4 7-8M15 8h5v5" />,
    overview: (
      <>
        <rect x="4" y="4" width="7" height="7" rx="1.5" />
        <rect x="13" y="4" width="7" height="7" rx="1.5" />
        <rect x="4" y="13" width="7" height="7" rx="1.5" />
        <rect x="13" y="13" width="7" height="7" rx="1.5" />
      </>
    ),
    holdings: (
      <>
        <rect x="3" y="7" width="18" height="13" rx="2" />
        <path d="M9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2M3 12h18" />
      </>
    ),
    screener: <path d="M4 5h16l-6 7v6l-4 2v-8L4 5z" />,
    compare: <path d="M7 4v16M17 4v16M7 8h5M12 16h5M4 20h6M14 20h6" />,
    quant: <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />,
    data: (
      <>
        <ellipse cx="12" cy="6" rx="7" ry="3" />
        <path d="M5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3" />
      </>
    ),
  };
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      {paths[name]}
    </svg>
  );
}

export function Sidebar({ open = false, onClose }: { open?: boolean; onClose?: () => void }) {
  const pathname = usePathname();
  // Unacknowledged holdings alerts (evaluated server-side after each NAV sync).
  const { data: alertCount } = useQuery({ queryKey: ["holdings", "alert-count"], queryFn: getAlertCount, refetchInterval: 60_000 });
  const unackedAlerts = alertCount?.unacked ?? 0;

  return (
    <aside
      className={`fixed inset-y-0 left-0 z-40 flex h-screen w-64 flex-col gap-4 overflow-y-auto border-r p-4 scrollbar-thin transition-transform duration-200 lg:z-30 lg:translate-x-0 ${
        open ? "translate-x-0 shadow-xl" : "-translate-x-full"
      }`}
      style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }}
      aria-label="Main menu"
    >
      <div className="flex items-center justify-between">
        <Link href="/" className="text-xl font-bold tracking-tight" onClick={onClose}>
          myFinonce
        </Link>
        <button type="button" onClick={onClose} className="rounded-lg px-2 py-1 text-sm lg:hidden" style={{ color: "var(--mf-muted)" }} aria-label="Close menu">
          ✕
        </button>
      </div>

      <nav className="flex flex-col" aria-label="Pages">
        {NAV_GROUPS.map((group, gi) => (
          <div key={group.title ?? `ungrouped-${gi}`} className={`flex flex-col gap-0.5 ${gi === 0 ? "" : "mt-4"}`}>
            {group.title && (
              <div className="mb-1 px-3 text-[0.68rem] font-bold uppercase tracking-wide" style={{ color: "var(--mf-muted)" }}>
                {group.title}
              </div>
            )}
            {group.items.map((item) => {
              const active = isActive(pathname, item);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  onClick={onClose}
                  aria-current={active ? "page" : undefined}
                  title={item.title}
                  className="flex items-center gap-2.5 rounded-lg px-3 py-1.5 text-sm transition-colors hover:bg-black/5 dark:hover:bg-white/5"
                  style={{
                    background: active ? "var(--mf-accent-bg)" : undefined,
                    color: active ? "var(--mf-accent)" : "var(--mf-fg)",
                    fontWeight: active ? 600 : 400,
                  }}
                >
                  <span style={{ color: active ? "var(--mf-accent)" : "var(--mf-muted)" }}>
                    <NavIcon name={item.icon} />
                  </span>
                  <span className="flex-1">{item.label}</span>
                  {item.href === "/holdings" && unackedAlerts > 0 && (
                    <span
                      className="rounded-full px-1.5 py-0.5 text-[0.65rem] font-bold"
                      style={{ background: "var(--mf-danger)", color: "#fff" }}
                      title={`${unackedAlerts} unread holdings alert${unackedAlerts === 1 ? "" : "s"}`}
                      aria-label={`${unackedAlerts} unread holdings alert${unackedAlerts === 1 ? "" : "s"}`}
                    >
                      {unackedAlerts}
                    </span>
                  )}
                </Link>
              );
            })}
          </div>
        ))}
      </nav>

      <div className="mt-auto">
        <NonAdviceNotice />
      </div>
    </aside>
  );
}
