/**
 * Keyboard navigation: press F, then a page's letter (F then H opens Holdings); "?" lists them.
 *
 * Chosen not to collide with anything else listening for keys:
 * - The browser binds plain letters to nothing; Ctrl/Alt/Cmd combinations (Ctrl+F find,
 *   Ctrl+1-9 tabs, Alt+letter menus) are left alone because any modifier cancels a sequence.
 * - The app's own key users: the Calculator takes digits, x, + - * / % ^ ( ) . , Enter,
 *   Backspace, Escape and Delete; drawers and menus take Escape; tables and search boxes take
 *   Enter, Space and the arrows. F and the page letters below are none of those, and "?" is
 *   Shift+/ -- the Calculator acts on a plain "/" only.
 * - Nothing fires while typing in a field, so "f" can still be typed anywhere.
 */

export const PREFIX = "f";

/** How long after F the page letter may come. */
export const SEQUENCE_MS = 1500;

export const SHORTCUTS: readonly { key: string; href: string; label: string }[] = [
  { key: "o", href: "/", label: "Overview" },
  { key: "h", href: "/holdings", label: "Holdings" },
  { key: "s", href: "/screener", label: "Scheme Screener" },
  { key: "c", href: "/compare", label: "Compare & Simulate" },
  { key: "q", href: "/quant", label: "Quant Analysis" },
  { key: "d", href: "/admin", label: "Data Management" },
  { key: "k", href: "/calculator", label: "Calculator" },
  { key: "i", href: "/nse-ipo", label: "NSE IPO" },
];

export function shortcutFor(href: string): string | undefined {
  return SHORTCUTS.find((s) => s.href === href)?.key;
}

/** A text box, number field, dropdown or editable area: keys there are typing, not commands. */
export function isTypingTarget(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el || typeof el.tagName !== "string") return false;
  return el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable === true;
}

export type ShortcutAction = { type: "arm" } | { type: "go"; href: string } | { type: "help" } | { type: "cancel" } | null;

interface KeyLike {
  key: string;
  ctrlKey: boolean;
  metaKey: boolean;
  altKey: boolean;
  shiftKey: boolean;
  target: EventTarget | null;
}

/** What a key press means, given whether F was pressed within the last SEQUENCE_MS.
 *  null = not ours: the key goes on to whatever else is listening, untouched. */
export function interpretKey(e: KeyLike, armed: boolean): ShortcutAction {
  if (isTypingTarget(e.target) || e.ctrlKey || e.metaKey || e.altKey) return armed ? { type: "cancel" } : null;
  // Caps Lock gives "F" without Shift; Shift+F is deliberately not a prefix.
  const key = e.shiftKey ? e.key : e.key.toLowerCase();
  if (armed) {
    const hit = SHORTCUTS.find((s) => s.key === key);
    return hit ? { type: "go", href: hit.href } : { type: "cancel" };
  }
  if (key === PREFIX) return { type: "arm" };
  if (e.key === "?") return { type: "help" };
  return null;
}
