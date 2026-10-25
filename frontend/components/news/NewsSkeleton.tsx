import React from "react";

export function NewsSkeleton({ count = 5 }: { count?: number }) {
  return (
    <div className="space-y-2" aria-label="Loading news feed" role="status">
      <span className="sr-only">Loading latest financial news...</span>
      {Array.from({ length: count }).map((_, i) => (
        <div
          key={i}
          className="flex min-h-[54px] flex-col justify-between rounded-lg border p-2.5 motion-safe:animate-pulse"
          style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }}
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1.5">
              <div className="h-3.5 w-3.5 rounded-full bg-slate-200" />
              <div className="h-2.5 w-20 rounded bg-slate-200" />
              <div className="h-1.5 w-1.5 rounded-full bg-slate-300" />
            </div>
            <div className="h-2 w-12 rounded bg-slate-200" />
          </div>
          <div className="mt-1 space-y-1">
            <div className="h-3 w-11/12 rounded bg-slate-200" />
          </div>
        </div>
      ))}
    </div>
  );
}
