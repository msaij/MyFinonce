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
}: {
  children: React.ReactNode;
  pageContext?: PageContext;
}) {
  return (
    <div className="flex min-h-screen" style={{ background: "var(--mf-bg)", color: "var(--mf-fg)" }}>
      <Sidebar pageContext={pageContext} />
      <div className="flex-1 ml-64 min-w-0 overflow-x-hidden">
        <div className="flex justify-end border-b p-3" style={{ borderColor: "var(--mf-border)" }}>
          <Suspense fallback={<div className="text-xs" style={{ color: "var(--mf-muted)" }}>Loading controls...</div>}>
            <DateRangePicker />
          </Suspense>
        </div>
        <main className="p-6">{children}</main>
      </div>
    </div>
  );
}
