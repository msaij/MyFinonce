"use client";

import { errorText } from "@/lib/api/client";

import { Banner } from "./Banner";

/** The parts of a react-query result this needs. */
export interface QueryLike {
  error: unknown;
  data: unknown;
  isFetching?: boolean;
  refetch: () => unknown;
}

/**
 * Says when a query failed, so a failure never passes for an empty market. Nothing when it
 * has not failed. Failed with nothing to show: a danger banner. Failed on a refresh while
 * older figures are still on screen (react-query keeps them): a warning that they are the
 * last ones that loaded. Both offer a retry.
 */
export function QueryStatus({ query, what }: { query: QueryLike; what: string }) {
  if (!query.error) return null;
  const hasData = query.data !== undefined && query.data !== null;
  const retry = (
    <button
      type="button"
      onClick={() => void query.refetch()}
      disabled={query.isFetching}
      className="ml-2 rounded-lg border px-2 py-0.5 text-xs font-semibold"
      style={{ borderColor: "var(--mf-border)" }}
    >
      {query.isFetching ? "Retrying…" : "Retry"}
    </button>
  );
  return hasData ? (
    <Banner level="warning">
      Showing the last {what} that loaded; the refresh failed: {errorText(query.error)}
      {retry}
    </Banner>
  ) : (
    <Banner level="danger">
      Couldn&apos;t load {what}: {errorText(query.error)}
      {retry}
    </Banner>
  );
}
