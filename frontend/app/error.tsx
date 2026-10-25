"use client";

import { useEffect } from "react";

/** Every page's last line of defence (the Calculator has its own, app/calculator/error.tsx):
 *  an error anywhere outside a chart shows this instead of Next's blank "Application error".
 *  "Try again" re-renders the page; "Reload" fetches it afresh, which is what a
 *  ChunkLoadError after a rebuild needs. */
export default function AppError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.warn("Page failed", error);
  }, [error]);

  return (
    <div className="flex min-h-screen items-center justify-center p-6" style={{ background: "var(--mf-bg)", color: "var(--mf-fg)" }}>
      <div className="mf-banner mf-banner-danger max-w-md space-y-3" role="alert">
        <div className="text-sm font-semibold">This page hit an error and couldn&apos;t be shown.</div>
        <div className="text-xs">{error.message}</div>
        <div className="flex gap-2">
          <button type="button" onClick={reset} className="rounded-lg border px-3 py-1 text-xs font-semibold" style={{ borderColor: "var(--mf-border)" }}>
            Try again
          </button>
          <button type="button" onClick={() => window.location.reload()} className="rounded-lg border px-3 py-1 text-xs font-semibold" style={{ borderColor: "var(--mf-border)" }}>
            Reload
          </button>
        </div>
      </div>
    </div>
  );
}
