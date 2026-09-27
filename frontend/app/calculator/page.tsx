"use client";

import { Fragment, Suspense, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";

import { AppShell } from "@/components/layout/AppShell";
import { CALCULATORS } from "@/components/calculator/registry";
import { useUrlSync } from "@/lib/hooks";

/**
 * Calculator: a standalone page in the left menu, outside any group. The calculators sit in a
 * tab bar beside the page title, grouped by category, so each one gets the full page width;
 * the choice is kept in the URL (?tool=emi). Calculators are listed in
 * components/calculator/registry.ts. Nothing entered here is stored.
 */
export default function CalculatorPage() {
  return (
    <Suspense fallback={<div className="p-6 text-sm">Loading calculators...</div>}>
      <CalculatorContent />
    </Suspense>
  );
}

function CalculatorContent() {
  const searchParams = useSearchParams();
  const [toolId, setToolId] = useState(() => {
    const requested = searchParams.get("tool");
    return CALCULATORS.some((c) => c.id === requested) ? (requested as string) : CALCULATORS[0].id;
  });
  useUrlSync({ tool: toolId !== CALCULATORS[0].id ? toolId : undefined });

  const groups = useMemo(() => {
    const out: { category: string; items: typeof CALCULATORS }[] = [];
    for (const c of CALCULATORS) {
      const g = out.find((x) => x.category === c.category);
      if (g) g.items.push(c);
      else out.push({ category: c.category, items: [c] });
    }
    return out;
  }, []);

  const active = CALCULATORS.find((c) => c.id === toolId) ?? CALCULATORS[0];
  const Active = active.Component;

  return (
    <AppShell hideDateRange>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3 border-b pb-3" style={{ borderColor: "var(--mf-border)" }}>
        <h1 className="mf-page-title !mb-0">Calculator</h1>
        <nav aria-label="Calculators" className="flex flex-wrap items-center gap-x-3 gap-y-2">
          {groups.map((g, gi) => (
            <Fragment key={g.category}>
              {gi > 0 && <span className="h-5 w-px" style={{ background: "var(--mf-border)" }} aria-hidden />}
              <div className="flex items-center gap-1.5" role="group" aria-label={g.category}>
                <span className="text-[0.65rem] font-bold uppercase tracking-wide" style={{ color: "var(--mf-muted)" }}>
                  {g.category}
                </span>
                {g.items.map((c) => {
                  const on = c.id === active.id;
                  return (
                    <button
                      key={c.id}
                      type="button"
                      onClick={() => setToolId(c.id)}
                      aria-current={on ? "page" : undefined}
                      title={c.description}
                      className="rounded-full border px-3.5 py-1 text-sm font-semibold transition-colors"
                      style={{
                        borderColor: on ? "var(--mf-accent)" : "var(--mf-border)",
                        background: on ? "var(--mf-accent)" : "var(--mf-card-bg)",
                        color: on ? "#fff" : "var(--mf-fg)",
                      }}
                    >
                      {c.label}
                    </button>
                  );
                })}
              </div>
            </Fragment>
          ))}
        </nav>
      </div>
      <p className="mb-4 mt-2 text-sm" style={{ color: "var(--mf-muted)" }}>
        {active.description} Worked out in your browser; nothing you enter is saved.
      </p>

      <Active key={active.id} />
    </AppShell>
  );
}
