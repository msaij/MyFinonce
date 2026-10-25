"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { PREFIX, SEQUENCE_MS, SHORTCUTS, interpretKey } from "@/lib/shortcuts";

const kbd = "inline-flex min-w-[1.4rem] justify-center rounded border px-1.5 py-0.5 font-mono text-[0.7rem] font-semibold";
const kbdStyle = { borderColor: "var(--mf-border)", background: "var(--mf-bg)" };

/** F then a page letter to go there, "?" for the list (see lib/shortcuts.ts for the rules). */
export function KeyboardShortcuts() {
  const router = useRouter();
  const [armed, setArmed] = useState(false);
  const [help, setHelp] = useState(false);
  const armedRef = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const disarm = () => {
      armedRef.current = false;
      setArmed(false);
      if (timer.current) clearTimeout(timer.current);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.repeat) return;
      const action = interpretKey(e, armedRef.current);
      if (!action) return;
      if (action.type === "cancel") return disarm(); // the key itself goes on untouched
      e.preventDefault();
      if (action.type === "arm") {
        armedRef.current = true;
        setArmed(true);
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(disarm, SEQUENCE_MS);
      } else if (action.type === "go") {
        disarm();
        setHelp(false);
        router.push(action.href);
      } else if (action.type === "help") {
        setHelp((h) => !h);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      if (timer.current) clearTimeout(timer.current);
    };
  }, [router]);

  useEffect(() => {
    if (!help) return;
    const onEsc = (e: KeyboardEvent) => e.key === "Escape" && setHelp(false);
    window.addEventListener("keydown", onEsc);
    return () => window.removeEventListener("keydown", onEsc);
  }, [help]);

  return (
    <>
      {armed && (
        <div
          className="fixed bottom-4 right-4 z-50 rounded-full border px-3 py-1.5 text-xs shadow-lg"
          style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
          role="status"
        >
          <span className={kbd} style={kbdStyle}>{PREFIX.toUpperCase()}</span> then a page key… <span style={{ color: "var(--mf-muted)" }}>(? for the list)</span>
        </div>
      )}
      {help && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={() => setHelp(false)}>
          <div
            role="dialog"
            aria-modal="true"
            aria-label="Keyboard shortcuts"
            className="w-full max-w-sm rounded-xl border p-5 shadow-xl"
            style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <h2 className="text-base font-bold">Keyboard shortcuts</h2>
              <button type="button" onClick={() => setHelp(false)} className="px-1 text-sm" style={{ color: "var(--mf-muted)" }} aria-label="Close">
                ✕
              </button>
            </div>
            <p className="mt-1 text-xs" style={{ color: "var(--mf-muted)" }}>
              Press <span className={kbd} style={kbdStyle}>F</span>, then the page&apos;s key. They do nothing while you are typing in a field.
            </p>
            <ul className="mt-3 flex flex-col gap-1.5 text-sm">
              {SHORTCUTS.map((s) => (
                <li key={s.key} className="flex items-center justify-between">
                  <span>{s.label}</span>
                  <span className="flex items-center gap-1">
                    <span className={kbd} style={kbdStyle}>F</span>
                    <span className={kbd} style={kbdStyle}>{s.key.toUpperCase()}</span>
                  </span>
                </li>
              ))}
              <li className="mt-1 flex items-center justify-between border-t pt-2" style={{ borderColor: "var(--mf-border)" }}>
                <span>Show this list</span>
                <span className={kbd} style={kbdStyle}>?</span>
              </li>
            </ul>
          </div>
        </div>
      )}
    </>
  );
}
