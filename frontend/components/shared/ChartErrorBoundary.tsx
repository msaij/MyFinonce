"use client";

import { Component, type ErrorInfo, type ReactNode } from "react";

/**
 * Keeps a chart that fails to load from taking the page down with it: a ChunkLoadError
 * after a rebuild, or a dropped connection while the chart library downloads, shows a note
 * in the chart's place and leaves the rest of the page working.
 *
 * The fallback reloads the page rather than retrying in place: next/dynamic wraps
 * React.lazy, which caches the failed import, so re-rendering would only fail again.
 */
export class ChartErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.warn("Chart failed to load", error, info.componentStack);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="flex h-full min-h-[200px] w-full flex-col items-center justify-center gap-2 rounded-lg text-sm" style={{ color: "var(--mf-muted)" }} role="alert">
        <span>Chart couldn&apos;t load.</span>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="rounded-lg border px-3 py-1 text-xs font-semibold"
          style={{ borderColor: "var(--mf-border)", color: "var(--mf-accent)" }}
        >
          Reload
        </button>
      </div>
    );
  }
}
