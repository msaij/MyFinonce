import { describe, expect, it } from "vitest";

import { SHORTCUTS, interpretKey, isTypingTarget, shortcutFor } from "./shortcuts";

const key = (k: string, over: Partial<{ ctrlKey: boolean; metaKey: boolean; altKey: boolean; shiftKey: boolean; target: EventTarget | null }> = {}) => ({
  key: k,
  ctrlKey: false,
  metaKey: false,
  altKey: false,
  shiftKey: false,
  target: null,
  ...over,
});
const field = (tagName: string, isContentEditable = false) => ({ tagName, isContentEditable }) as unknown as EventTarget;

describe("keyboard navigation", () => {
  it("F then a page letter goes to that page", () => {
    expect(interpretKey(key("f"), false)).toEqual({ type: "arm" });
    expect(interpretKey(key("h"), true)).toEqual({ type: "go", href: "/holdings" });
    expect(interpretKey(key("k"), true)).toEqual({ type: "go", href: "/calculator" });
    // Caps Lock on: "F" and "H" without Shift still work.
    expect(interpretKey(key("F"), false)).toEqual({ type: "arm" });
    expect(interpretKey(key("H"), true)).toEqual({ type: "go", href: "/holdings" });
  });

  it("a letter on its own does nothing: only F starts a sequence", () => {
    expect(interpretKey(key("h"), false)).toBeNull();
    expect(interpretKey(key("g"), false)).toBeNull();
  });

  it("any other key after F cancels and is left for whoever else listens", () => {
    // The Calculator's keys: a digit, x and Enter right after F still reach it.
    expect(interpretKey(key("7"), true)).toEqual({ type: "cancel" });
    expect(interpretKey(key("x"), true)).toEqual({ type: "cancel" });
    expect(interpretKey(key("Enter"), true)).toEqual({ type: "cancel" });
  });

  it("never fires while typing, so f and ? can be typed anywhere", () => {
    for (const t of [field("INPUT"), field("TEXTAREA"), field("SELECT"), field("DIV", true)]) {
      expect(interpretKey(key("f", { target: t }), false)).toBeNull();
      expect(interpretKey(key("?", { shiftKey: true, target: t }), false)).toBeNull();
    }
    expect(isTypingTarget(field("BUTTON"))).toBe(false);
  });

  it("leaves every browser shortcut alone: any modifier is not ours", () => {
    expect(interpretKey(key("f", { ctrlKey: true }), false)).toBeNull(); // Ctrl+F: find
    expect(interpretKey(key("f", { altKey: true }), false)).toBeNull(); // Alt+F: browser menu
    expect(interpretKey(key("f", { metaKey: true }), false)).toBeNull();
    expect(interpretKey(key("F", { shiftKey: true }), false)).toBeNull(); // Shift+F is not the prefix
    expect(interpretKey(key("h", { ctrlKey: true }), true)).toEqual({ type: "cancel" });
  });

  it("? opens the list; Shift+/ is not the Calculator's plain /", () => {
    expect(interpretKey(key("?", { shiftKey: true }), false)).toEqual({ type: "help" });
    expect(interpretKey(key("/"), false)).toBeNull();
  });

  it("every page in the list has its own letter, none of them the Calculator's", () => {
    const letters = SHORTCUTS.map((s) => s.key);
    expect(new Set(letters).size).toBe(letters.length);
    expect(letters.some((l) => ["x", "f"].includes(l) || /\d/.test(l))).toBe(false);
    expect(shortcutFor("/compare")).toBe("c");
    expect(shortcutFor("/nowhere")).toBeUndefined();
  });
});
