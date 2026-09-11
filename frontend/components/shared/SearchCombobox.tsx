"use client";

import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";

import { apiGet } from "@/lib/api/client";
import { useDebouncedValue } from "@/lib/hooks";

/**
 * Replaces st_searchbox (used on 5 of 6 original pages -- Overview, Screener,
 * Compare & Simulate, Leaders & Laggards, Quant Analysis). No native React
 * equivalent exists for a live-suggest combobox; this is a genuine rebuild,
 * backed by the same GET /api/schemes/search?q= endpoint each page's
 * db.get_schemes_for_dropdown(search_term=...) call already used. See the
 * migration plan's "SearchCombobox" section.
 */

interface SchemeResult {
  scheme_code: number;
  scheme_name: string;
  [key: string]: unknown;
}

export interface SearchComboboxProps {
  placeholder?: string;
  onSelect: (scheme: SchemeResult) => void;
  extraParams?: Record<string, string | undefined>;
}

export function SearchCombobox({ placeholder = "Search fund name or AMFI code...", onSelect, extraParams }: SearchComboboxProps) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const debounced = useDebouncedValue(query, 250);
  const inputId = useId();

  const { data, isFetching } = useQuery({
    queryKey: ["schemes-search", debounced, extraParams],
    queryFn: () => apiGet<SchemeResult[]>("/api/schemes/search", { q: debounced, limit: 25, ...extraParams }),
    enabled: debounced.length >= 2,
  });

  return (
    <div className="relative">
      <input
        id={inputId}
        type="text"
        className="w-full rounded-lg border px-3 py-2 text-sm"
        style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
        placeholder={placeholder}
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
      />
      {open && debounced.length >= 2 && (
        <div
          className="absolute z-50 mt-1 max-h-72 w-full overflow-y-auto rounded-lg border shadow-lg"
          style={{ borderColor: "var(--mf-border)", background: "var(--mf-bg)" }}
        >
          {isFetching && <div className="p-2 text-sm" style={{ color: "var(--mf-muted)" }}>Searching...</div>}
          {!isFetching && (data?.length ?? 0) === 0 && (
            <div className="p-2 text-sm" style={{ color: "var(--mf-muted)" }}>No results</div>
          )}
          {data?.map((scheme) => (
            <button
              key={scheme.scheme_code}
              type="button"
              className="block w-full px-3 py-2 text-left text-sm hover:opacity-80"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => {
                onSelect(scheme);
                setQuery(scheme.scheme_name);
                setOpen(false);
              }}
            >
              {scheme.scheme_name} <span style={{ color: "var(--mf-muted)" }}>[{scheme.scheme_code}]</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
