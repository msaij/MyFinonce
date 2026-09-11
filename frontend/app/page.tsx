"use client";

import { useEffect, useState } from "react";

// Phase 0 scaffold placeholder -- proves the frontend container can reach
// the backend container through the /api/* rewrite in next.config.js.
// Replaced by the real Overview page in Phase 3 of the migration plan.
export default function ScaffoldHome() {
  const [health, setHealth] = useState<string>("checking...");

  useEffect(() => {
    fetch("/api/health")
      .then((r) => r.json())
      .then((data) => setHealth(JSON.stringify(data)))
      .catch((err) => setHealth(`error: ${String(err)}`));
  }, []);

  return (
    <main className="p-8">
      <h1 className="text-2xl font-semibold text-accent">
        Indian Mutual Funds -- webstack scaffold
      </h1>
      <p className="mt-2 text-muted">
        Phase 0: this page and the FastAPI backend are both up. Nothing else
        is built yet -- see the migration plan for what comes next.
      </p>
      <p className="mt-4 font-mono text-sm">Backend /api/health -&gt; {health}</p>
    </main>
  );
}
