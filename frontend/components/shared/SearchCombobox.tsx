"use client";

import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { useId, useState, useEffect, useRef } from "react";

import Link from "next/link";

import { apiGet } from "@/lib/api/client";
import { useDebouncedValue } from "@/lib/hooks";

export interface SchemeResult {
  scheme_code: number;
  scheme_name: string;
  plan_type?: string;
  option_type?: string;
  fund_house?: string;
  category?: string;
  display_label?: string;
  [key: string]: unknown;
}

export interface SearchComboboxProps {
  placeholder?: string;
  onSelect: (scheme: SchemeResult) => void;
  extraParams?: Record<string, string | undefined>;
  value?: string;
  onChangeQuery?: (query: string) => void;
  onClear?: () => void;
  className?: string;
}

export function SearchCombobox({
  placeholder = "Search fund name (words in any order) or AMFI code...",
  onSelect,
  extraParams,
  value: controlledValue,
  onChangeQuery,
  onClear,
  className = "",
}: SearchComboboxProps) {
  const [internalQuery, setInternalQuery] = useState("");
  const isControlled = controlledValue !== undefined;
  const currentQuery = isControlled ? controlledValue : internalQuery;

  const [open, setOpen] = useState(false);
  const [highlightIndex, setHighlightIndex] = useState(-1);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const debounced = useDebouncedValue(currentQuery, 150);
  const inputId = useId();

  const handleQueryChange = (val: string) => {
    if (!isControlled) {
      setInternalQuery(val);
    }
    onChangeQuery?.(val);
    setOpen(true);
    setHighlightIndex(-1);
  };

  const handleClear = () => {
    if (!isControlled) {
      setInternalQuery("");
    }
    onChangeQuery?.("");
    onClear?.();
    setOpen(false);
    inputRef.current?.focus();
  };

  const { data, isFetching } = useQuery({
    queryKey: ["schemes-search", debounced, extraParams],
    queryFn: () =>
      apiGet<SchemeResult[]>("/api/schemes/search", {
        q: debounced,
        limit: 30,
        ...extraParams,
      }),
    enabled: debounced.trim().length >= 1,
    placeholderData: keepPreviousData,
  });

  const results = data ?? [];

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!open && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      setOpen(true);
      return;
    }
    if (!open) return;

    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlightIndex((prev) => (prev + 1 < results.length ? prev + 1 : 0));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlightIndex((prev) => (prev - 1 >= 0 ? prev - 1 : results.length - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (highlightIndex >= 0 && highlightIndex < results.length) {
        handleSelect(results[highlightIndex]);
      } else if (results.length > 0) {
        handleSelect(results[0]);
      }
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  const handleSelect = (scheme: SchemeResult) => {
    onSelect(scheme);
    if (!isControlled) {
      setInternalQuery(scheme.scheme_name);
    }
    setOpen(false);
  };

  return (
    <div ref={containerRef} className={`relative ${open ? "z-50" : "z-10"} ${className}`}>
      <div className="relative flex items-center">
        <input
          ref={inputRef}
          id={inputId}
          type="text"
          className="w-full rounded-lg border px-3 py-2 pr-9 text-sm placeholder:text-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500/20"
          style={{
            borderColor: "var(--mf-border)",
            background: "var(--mf-card-bg)",
            color: "var(--mf-fg)",
          }}
          placeholder={placeholder}
          value={currentQuery}
          onChange={(e) => handleQueryChange(e.target.value)}
          onFocus={() => {
            if (currentQuery.trim().length >= 1) setOpen(true);
          }}
          onKeyDown={handleKeyDown}
          autoComplete="off"
        />
        {isFetching && (
          <span
            className={`absolute ${currentQuery ? "right-8" : "right-2.5"} flex h-4 w-4 items-center justify-center pointer-events-none`}
            title="Searching funds..."
          >
            <span className="h-3 w-3 animate-spin rounded-full border-2 border-blue-500 border-t-transparent" />
          </span>
        )}
        {currentQuery && (
          <button
            type="button"
            onClick={handleClear}
            className="absolute right-2.5 flex h-5 w-5 items-center justify-center rounded-full text-xs font-bold text-slate-500 hover:bg-slate-100 hover:text-slate-800"
            title="Clear search"
          >
            ✕
          </button>
        )}
      </div>

      {open && currentQuery.trim().length >= 1 && (
        <div
          className="absolute left-0 top-full z-[100] mt-1 max-h-80 min-h-[140px] w-full min-w-full overflow-y-auto overscroll-contain rounded-lg border shadow-2xl backdrop-blur-sm"
          style={{
            borderColor: "var(--mf-border)",
            backgroundColor: "var(--mf-card-bg)",
          }}
        >
          {isFetching && results.length === 0 && (
            <div className="p-4 text-xs" style={{ color: "var(--mf-muted)" }}>
              Searching funds...
            </div>
          )}
          {!isFetching && results.length === 0 && (
            <div className="p-4 text-xs" style={{ color: "var(--mf-muted)" }}>
              No mutual funds matching &ldquo;{currentQuery}&rdquo;
            </div>
          )}
          {results.map((scheme, idx) => (
            <button
              key={scheme.scheme_code}
              type="button"
              className={`block w-full border-b px-3.5 py-2.5 text-left transition-colors last:border-b-0 ${
                idx === highlightIndex ? "!bg-blue-50/80 dark:!bg-blue-950/60" : "hover:bg-slate-100/60 dark:hover:bg-slate-800/60"
              }`}
              style={{ borderColor: "var(--mf-border)" }}
              onMouseEnter={() => setHighlightIndex(idx)}
              onClick={() => handleSelect(scheme)}
            >
              <div className="flex items-start justify-between gap-3">
                <span className="text-xs font-semibold leading-snug break-words" style={{ color: "var(--mf-fg)" }}>
                  {scheme.scheme_name}
                </span>
                <span className="flex shrink-0 items-center gap-1">
                  <Link
                    href={`/scheme/${scheme.scheme_code}`}
                    className="rounded px-1.5 py-0.5 text-[0.68rem] font-semibold"
                    style={{ color: "var(--mf-accent)" }}
                    onClick={(e) => e.stopPropagation()}
                  >
                    view
                  </Link>
                  <span className="rounded px-1.5 py-0.5 text-[0.68rem] font-mono font-semibold" style={{ background: "var(--mf-accent-bg)", color: "var(--mf-accent)" }}>
                    {scheme.scheme_code}
                  </span>
                </span>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[0.72rem]" style={{ color: "var(--mf-muted)" }}>
                {scheme.fund_house && <span>{scheme.fund_house}</span>}
                {scheme.category && <span>• {scheme.category}</span>}
                {scheme.plan_type && <span>• {scheme.plan_type}</span>}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
