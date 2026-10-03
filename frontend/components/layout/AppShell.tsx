"use client";

import { Suspense, useEffect, useState } from "react";
import { usePathname } from "next/navigation";

import { DateRangePicker } from "./DateRangePicker";
import { KeyboardShortcuts } from "./KeyboardShortcuts";
import { Sidebar } from "./Sidebar";

/**
 * Every page's frame: the left menu, the mutual-fund controls bar (Plan / Option / Window)
 * where a page uses them, and the page itself. At laptop width and above the menu is fixed
 * on the left; below that it becomes a drawer behind a menu button, so the page gets the
 * full width of a small screen.
 */
export function AppShell({
  children,
  hideDateRange = false,
}: {
  children: React.ReactNode;
  /** For pages outside the mutual-fund analytics (NSE IPO, Calculator), where the NAV date window
   *  means nothing: hides the controls bar, and with it the NAV freshness line. */
  hideDateRange?: boolean;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const pathname = usePathname();

  // A drawer left open would cover the next page.
  useEffect(() => setMenuOpen(false), [pathname]);
  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setMenuOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  return (
    <div className="flex min-h-screen" style={{ background: "var(--mf-bg)", color: "var(--mf-fg)" }}>
      <Sidebar open={menuOpen} onClose={() => setMenuOpen(false)} />
      <KeyboardShortcuts />
      {menuOpen && <div className="fixed inset-0 z-30 bg-black/40 lg:hidden" onClick={() => setMenuOpen(false)} aria-hidden />}

      <div className="min-w-0 flex-1 overflow-x-hidden lg:ml-64">
        <div className="sticky top-0 z-20 flex items-center gap-3 border-b px-4 py-2 lg:hidden" style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }}>
          <button
            type="button"
            onClick={() => setMenuOpen(true)}
            className="rounded-lg border px-2.5 py-1 text-sm"
            style={{ borderColor: "var(--mf-border)" }}
            aria-label="Open menu"
            aria-expanded={menuOpen}
          >
            ☰
          </button>
          <span className="font-bold tracking-tight">myFinonce</span>
        </div>
        {!hideDateRange && (
          <div className="flex justify-end border-b p-3" style={{ borderColor: "var(--mf-border)" }}>
            <Suspense fallback={<div className="text-xs" style={{ color: "var(--mf-muted)" }}>Loading controls...</div>}>
              <DateRangePicker />
            </Suspense>
          </div>
        )}
        <main className="p-4 sm:p-6">{children}</main>
      </div>
    </div>
  );
}
