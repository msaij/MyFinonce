import React from "react";
import { act } from "react";
import { createRoot, Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { NumInput } from "./fields";

// vitest compiles JSX with the classic runtime, and the app's components (written for
// Next's automatic runtime) do not import React themselves.
(globalThis as { React?: typeof React }).React = React;

describe("NumInput", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  const box = () => container.querySelector("input") as HTMLInputElement;

  it("shows a tiny number in plain digits, not as 17", () => {
    act(() => root.render(<NumInput label="Rate" value={1e-7} onChange={() => {}} />));
    expect(box().value).toBe("0.0000001");
    act(() => box().focus());
    expect(box().value).toBe("0.0000001");
  });

  it("a stripped character typed mid-number does not move the caret", async () => {
    const onChange = vi.fn((v: number) => v);
    act(() => root.render(<NumInput label="Amount" value={0} onChange={onChange} />));
    const input = box();
    act(() => input.focus());
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "1a23");
      input.setSelectionRange(2, 2); // just after the "a"
      input.dispatchEvent(new Event("input", { bubbles: true }));
      await new Promise((r) => requestAnimationFrame(r)); // the caret is restored a frame later
    });
    expect(input.value).toBe("123");
    expect(onChange).toHaveBeenLastCalledWith(123);
    expect(input.selectionStart).toBe(1);
  });

  it("can be disabled", () => {
    act(() => root.render(<NumInput label="Step-up" value={5} onChange={() => {}} disabled />));
    expect(box().disabled).toBe(true);
  });
});
