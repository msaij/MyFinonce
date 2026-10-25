"use client";

import { useEffect } from "react";

/** The Calculator page's last line of defence: an error anywhere on it shows this instead of
 *  Next's blank "Application error". "Try again" re-renders the page; "Reload" fetches it
 *  afresh, which is what a ChunkLoadError after a rebuild needs. */
export default function CalculatorError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.warn("Calculator page failed", error);
  }, [error]);

  return (
    <div className="flex min-h-screen items-center justify-center p-6" style={{ background: "var(--mf-bg)", color: "var(--mf-fg)" }}>
      <div className="mf-banner mf-banner-danger max-w-md space-y-3" role="alert">
        <div className="text-sm font-semibold">The calculator hit an error and couldn&apos;t show this page.</div>
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
