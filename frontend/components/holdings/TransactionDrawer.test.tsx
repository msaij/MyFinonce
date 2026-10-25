import React from "react";
import { act } from "react";
import { createRoot, Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PreviewResult, Transaction, TransactionDraft } from "@/lib/api/holdings";
import { ApiError } from "@/lib/api/client";

(globalThis as { React?: typeof React }).React = React;

vi.mock("@/components/shared/SearchCombobox", () => ({ SearchCombobox: () => <input aria-label="fund search" /> }));

const api = vi.hoisted(() => ({
  previewTransaction: vi.fn(),
  addTransaction: vi.fn(),
  editTransaction: vi.fn(),
}));
vi.mock("@/lib/api/holdings", () => api);

import { TransactionDrawer } from "./TransactionDrawer";

const editing = {
  id: 42, portfolio_id: 1, scheme_code: 1001, txn_type: "BUY", trade_date: "2024-03-01", amount: "10000.00",
  units: "123.457000", nav: "80.9970", nav_source: "amfi_auto", stamp_duty: "0.50", switch_group: null, notes: null,
  deleted_at: null, display_name: "Alpha Flexi Cap Fund (Direct - Growth) [1001]",
} as Transaction;

const okPreview = (draft: TransactionDraft): PreviewResult => ({
  ok: true, error: null, errors: [], warnings: [],
  rows: [{ ...editing, amount: String(draft.amount ?? editing.amount) } as Transaction],
});

const portfolios = [{ id: 1, name: "Self", archived: false }] as never[];

describe("TransactionDrawer", () => {
  let container: HTMLDivElement;
  let root: Root;
  let onClose: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    onClose = vi.fn();
    Object.values(api).forEach((f) => f.mockReset());
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  const render = async (props: Partial<React.ComponentProps<typeof TransactionDrawer>> = {}) => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <TransactionDrawer open onClose={onClose} portfolios={portfolios} defaultPortfolioId={1} editing={editing} {...props} />
        </QueryClientProvider>
      );
    });
  };
  // Waits out the draft's 350 ms debounce and the preview / save round trips.
  const waitFor = async (cond: () => boolean, what: string, ms = 4000) => {
    const start = Date.now();
    while (!cond()) {
      if (Date.now() - start > ms) throw new Error(`timed out waiting for ${what}`);
      await act(async () => {
        await new Promise((r) => setTimeout(r, 25));
      });
    }
  };
  const saveButton = () => [...container.querySelectorAll("button")].find((b) => /Save changes|Add to ledger|Saving/.test(b.textContent ?? ""))!;
  const text = () => container.textContent ?? "";

  it("previews an edit against the row it replaces and saves it as an edit, once the preview passed", async () => {
    api.previewTransaction.mockImplementation(async (d: TransactionDraft) => okPreview(d));
    api.editTransaction.mockResolvedValue({ transaction: editing, warnings: [] });
    await render();
    expect(saveButton().disabled).toBe(true);           // nothing previewed yet
    await waitFor(() => !saveButton().disabled, "the preview");
    expect(api.previewTransaction).toHaveBeenCalledWith(expect.objectContaining({ amount: 10000, scheme_code: 1001 }), 42, expect.anything());
    await act(async () => saveButton().click());
    await waitFor(() => onClose.mock.calls.length > 0, "the save");
    expect(api.editTransaction).toHaveBeenCalledWith(42, expect.objectContaining({ portfolio_id: 1, txn_type: "BUY" }));
    expect(api.addTransaction).not.toHaveBeenCalled();
  });

  it("never offers Save past a failed preview, and says why", async () => {
    api.previewTransaction.mockResolvedValue({ ok: false, error: "Redeems 60 units but only 0 were held", errors: [], rows: [], warnings: [] });
    await render();
    await waitFor(() => text().includes("only 0 were held"), "the refusal");
    expect(saveButton().disabled).toBe(true);
  });

  it("keeps the drawer open with the server's reason when the save is refused", async () => {
    api.previewTransaction.mockImplementation(async (d: TransactionDraft) => okPreview(d));
    api.editTransaction.mockRejectedValue(new ApiError(422, "This portfolio is archived; unarchive it to add or change its transactions."));
    await render();
    await waitFor(() => !saveButton().disabled, "the preview");
    await act(async () => saveButton().click());
    await waitFor(() => text().includes("This portfolio is archived"), "the refusal");
    expect(onClose).not.toHaveBeenCalled();
  });

  it("asks the owner to confirm a typed NAV the ledger refused, and sends the confirmation", async () => {
    api.previewTransaction.mockImplementation(async (d: TransactionDraft) =>
      d.nav_confirmed
        ? okPreview(d)
        : { ok: false, error: "The NAV you typed is 3 times AMFI's", rows: [], warnings: [],
            errors: [{ txn_id: null, scheme_code: 1001, trade_date: "2024-03-01", message: "x", code: "nav_unconfirmed" }] });
    await render({ editing: { ...editing, nav_source: "user", nav: "242.9910" } as Transaction });
    const box = () => [...container.querySelectorAll("label")].find((l) => l.textContent?.includes("My statement shows this NAV"));
    await waitFor(() => !!box(), "the confirmation");
    expect(saveButton().disabled).toBe(true);
    await act(async () => box()!.querySelector("input")!.click());
    await waitFor(() => !saveButton().disabled, "the confirmed preview");
    expect(api.previewTransaction).toHaveBeenLastCalledWith(expect.objectContaining({ nav: 242.991, nav_confirmed: true }), 42, expect.anything());
  });
});
