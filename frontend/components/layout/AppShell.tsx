"use client";

import { Suspense } from "react";
import { DateRangePicker } from "./DateRangePicker";
import { Sidebar } from "./Sidebar";

export interface PageContext {
  label: string;
  value: string;
  sub?: string;
}

export function AppShell({
  children,
  pageContext,
  hideDateRange = false,
}: {
  children: React.ReactNode;
  pageContext?: PageContext;
  /** For pages outside the mutual-fund analytics (NSE IPO, Calculator), where the NAV date window
   *  means nothing: hides the date-range picker and the sidebar's Active Scope card. */
  hideDateRange?: boolean;
}) {
  return (
    <div className="flex min-h-screen" style={{ background: "var(--mf-bg)", color: "var(--mf-fg)" }}>
      <Sidebar pageContext={pageContext} showScope={!hideDateRange} />
      <div className="flex-1 ml-64 min-w-0 overflow-x-hidden">
        {!hideDateRange && (
          <div className="flex justify-end border-b p-3" style={{ borderColor: "var(--mf-border)" }}>
            <Suspense fallback={<div className="text-xs" style={{ color: "var(--mf-muted)" }}>Loading controls...</div>}>
              <DateRangePicker />
            </Suspense>
          </div>
        )}
        <main className="p-6">{children}</main>
      </div>
    </div>
  );
}
