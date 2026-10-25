"use client";

import { useEffect } from "react";

/** The root layout itself failed (app/error.tsx renders inside it, so it cannot catch that).
 *  This replaces the whole document, so it brings its own <html> and <body> and inline styles:
 *  the layout's globals.css may not be there. */
export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.warn("App failed", error);
  }, [error]);

  const button = { border: "1px solid #cbd5e1", borderRadius: 8, padding: "4px 12px", fontSize: 12, fontWeight: 600, background: "#fff", cursor: "pointer" };
  return (
    <html lang="en">
      <body style={{ margin: 0, fontFamily: "system-ui, sans-serif", background: "#f8fafc", color: "#0f172a" }}>
        <div style={{ display: "flex", minHeight: "100vh", alignItems: "center", justifyContent: "center", padding: 24 }}>
          <div role="alert" style={{ maxWidth: 448, border: "1px solid #fecaca", background: "#fef2f2", borderRadius: 12, padding: 16 }}>
            <div style={{ fontSize: 14, fontWeight: 600 }}>myFinonce hit an error and couldn&apos;t start.</div>
            <div style={{ fontSize: 12, marginTop: 8 }}>{error.message}</div>
            <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
              <button type="button" onClick={reset} style={button}>
                Try again
              </button>
              <button type="button" onClick={() => window.location.reload()} style={button}>
                Reload
              </button>
            </div>
          </div>
        </div>
      </body>
    </html>
  );
}
