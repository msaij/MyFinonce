/**
 * Arithmetic for the basic calculator: + − × ÷, powers (^), square roots (√), brackets, unary
 * minus and percent, with the usual precedence (powers, then × ÷, then + −; −2^2 = −4).
 * A small recursive-descent parser -- never `eval`.
 *
 * Percent follows the phone-calculator convention: after + or − it is a share of what came
 * before it (200 + 10% = 220); anywhere else it is a hundredth (50 × 10% = 5, 10% = 0.1).
 */

export type EvalResult = { ok: true; value: number } | { ok: false; error: string };

type Token = { t: "num"; v: number } | { t: "op"; v: string };

/** Operator glyphs as typed or as shown: * / - are accepted alongside × ÷ −. */
const NORMALISE: Record<string, string> = { "×": "*", "÷": "/", "−": "-", x: "*" };

function tokenize(src: string): Token[] {
  const out: Token[] = [];
  let i = 0;
  while (i < src.length) {
    const c = NORMALISE[src[i]] ?? src[i];
    if (c === " " || c === ",") {
      i++;
    } else if (/[0-9.]/.test(c)) {
      let j = i;
      while (j < src.length && /[0-9.,]/.test(src[j])) j++;
      const text = src.slice(i, j).replace(/,/g, "");
      if ((text.match(/\./g) ?? []).length > 1 || text === ".") throw new Error("Invalid number");
      out.push({ t: "num", v: Number(text) });
      i = j;
    } else if ("+-*/%()^√".includes(c)) {
      out.push({ t: "op", v: c });
      i++;
    } else {
      throw new Error(`Unexpected "${src[i]}"`);
    }
  }
  return out;
}

/** The sum or difference `x` of `a` and `b`, kept only to the digits the larger operand
 *  carries (15 significant). Without it, 10250.30 − 10000 is 250.299999999999 and
 *  12345.67 − 12345.60 is 0.0699999999997: the operands' representation error, not maths. */
function snap(x: number, a: number, b: number): number {
  const m = Math.max(Math.abs(a), Math.abs(b));
  if (!x || !m || !Number.isFinite(x) || !Number.isFinite(m)) return x;
  const d = 15 - (Math.floor(Math.log10(m)) - Math.floor(Math.log10(Math.abs(x))));
  return d < 1 ? 0 : Number(x.toPrecision(Math.min(15, d)));
}

/** A value, and whether it was written as a percentage (kept raw until context decides). */
type Val = { v: number; pct: boolean };
const resolve = (x: Val) => (x.pct ? x.v / 100 : x.v);

function parse(tokens: Token[]): number {
  let pos = 0;
  const peek = () => tokens[pos];
  const isOp = (v: string) => peek()?.t === "op" && peek().v === v;

  function expr(): number {
    let left = resolve(term());
    while (isOp("+") || isOp("-")) {
      const op = tokens[pos++].v;
      const right = term();
      const r = right.pct ? (left * right.v) / 100 : right.v;
      left = snap(op === "+" ? left + r : left - r, left, r);
    }
    return left;
  }

  function term(): Val {
    let left = unary();
    while (isOp("*") || isOp("/")) {
      const op = tokens[pos++].v;
      const right = resolve(unary());
      const l = resolve(left);
      if (op === "/" && right === 0) throw new Error("Can't divide by zero");
      left = { v: op === "*" ? l * right : l / right, pct: false };
    }
    return left;
  }

  function unary(): Val {
    if (isOp("-")) {
      pos++;
      const inner = unary();
      return { v: -inner.v, pct: inner.pct };
    }
    if (isOp("+")) {
      pos++;
      return unary();
    }
    if (isOp("√")) {
      pos++;
      const v = resolve(unary());
      if (v < 0) throw new Error("No square root of a negative number");
      return { v: Math.sqrt(v), pct: false };
    }
    return power();
  }

  /** Right-associative, and binds tighter than a leading minus: −2^2 = −4, 2^3^2 = 512. */
  function power(): Val {
    const base = postfix();
    if (!isOp("^")) return base;
    pos++;
    const exp = resolve(unary());
    const v = Math.pow(resolve(base), exp);
    if (Number.isNaN(v)) throw new Error("Not a real number");
    return { v, pct: false };
  }

  function postfix(): Val {
    const v = primary();
    if (isOp("%")) {
      pos++;
      return { v, pct: true };
    }
    return { v, pct: false };
  }

  function primary(): number {
    const tok = peek();
    if (!tok) throw new Error("Incomplete expression");
    if (tok.t === "num") {
      pos++;
      return tok.v;
    }
    if (tok.v === "(") {
      pos++;
      const v = expr();
      if (!isOp(")")) throw new Error("Missing )");
      pos++;
      return v;
    }
    throw new Error("Incomplete expression");
  }

  const value = expr();
  if (pos < tokens.length) throw new Error(tokens[pos].v === ")" ? "Unmatched )" : "Unexpected input");
  return value;
}

/** Trims binary floating-point noise: 0.1 + 0.2 shows as 0.3, not 0.30000000000000004.
 *  15 digits keeps everything typed (1234567890123 stays as it is); the noise a subtraction
 *  leaves in its last digits is removed by `snap` first. */
const tidy = (v: number) => Number(v.toPrecision(15));

export function evaluate(src: string): EvalResult {
  if (!src.trim()) return { ok: false, error: "" };
  try {
    const value = parse(tokenize(src));
    if (!Number.isFinite(value)) return { ok: false, error: "Result too large" };
    return { ok: true, value: tidy(value) };
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
}

const OPS = ["+", "−", "×", "÷", "^"];
const isOperator = (c: string | undefined) => !!c && OPS.includes(c);
const endsWithValue = (s: string) => /[0-9)%]$/.test(s);

/** ± on the number being typed: 5 → −5, 5+3 → 5+(−3), and back again. The number may
 *  carry grouping commas (1,000), as the display stores it. */
function toggleSign(base: string): string {
  const wrapped = base.match(/^(.*)\(−([0-9.,]+)\)$/);
  if (wrapped) return wrapped[1] + wrapped[2];
  const m = base.match(/^(.*?)([0-9.,]+)$/);
  if (!m) return base === "" || /[+−×÷^(√]$/.test(base) ? applyKey(base, "−", false) : base;
  const [, prefix, num] = m;
  // Already negative (a leading or unary minus): drop it.
  if (prefix === "−" || /[×÷^(√]−$/.test(prefix)) return prefix.slice(0, -1) + num;
  if (prefix === "" || /[×÷^(√]$/.test(prefix)) return `${prefix}−${num}`;
  return `${prefix}(−${num})`; // after + or −, brackets keep it readable
}

/** The expression after one key press on the calculator. `afterEquals` means the display
 *  holds a result just produced by "=": a digit then starts afresh, while an operator, %, ±
 *  or x² carries on from the result. Keys: digits, ".", + − × ÷ ^, %, ( ), √, x², ±. */
export function applyKey(cur: string, key: string, afterEquals: boolean): string {
  const continues = isOperator(key) || key === "%" || key === "±" || key === "x²";
  const base = afterEquals && !continues ? "" : cur;
  const last = base.slice(-1);
  if (key === "±") return toggleSign(base);
  if (key === "x²") return endsWithValue(base) ? base + "^2" : base;
  if (key === "√") return base + (endsWithValue(base) ? "×√" : "√");
  if (isOperator(key)) {
    if (!base) return key === "−" ? "−" : base;
    if (last === "√") return key === "−" ? base + key : base;
    // A minus after × ÷ or ^ is a negative sign; any other operator replaces the previous one.
    if (isOperator(last)) return key === "−" && (last === "×" || last === "÷" || last === "^") ? base + key : base.slice(0, -1) + key;
    if (last === "(") return key === "−" ? base + key : base;
    return base + key;
  }
  if (key === ".") {
    // Like a digit, a point after ")" or "%" starts a new number multiplied in.
    if (last === ")" || last === "%") return base + "×0.";
    const lastNumber = base.split(/[+−×÷^√()%]/).pop() ?? "";
    if (lastNumber.includes(".")) return base;
    return base + (lastNumber === "" ? "0." : ".");
  }
  if (key === "%") return base && /[0-9)]/.test(last) ? base + "%" : base;
  if (key === "(") return base + (endsWithValue(base) ? "×(" : "(");
  if (key === ")") {
    const open = (base.match(/\(/g) ?? []).length - (base.match(/\)/g) ?? []).length;
    return open > 0 && endsWithValue(base) ? base + ")" : base;
  }
  // A digit straight after ")" or "%" implies multiplication.
  return base + (last === ")" || last === "%" ? "×" : "") + key;
}

/** A key pressed with the caret mid-expression (or text selected): the same rules as
 *  applyKey on the text before the caret, so 5+|3 then × gives 5×3, not 5+×3. Returns the
 *  regrouped text and where the caret goes, or null for a key that would break it there:
 *  ±, or a second "." in the number at the caret. */
export function applyKeyAt(expr: string, start: number, end: number, key: string): { text: string; caret: number } | null {
  const before = expr.slice(0, start);
  const after = expr.slice(end);
  if (key === "±") return null;
  if (key === "." && ((before.match(/[\d,.]*$/)?.[0] ?? "") + (after.match(/^[\d,.]*/)?.[0] ?? "")).includes(".")) return null;
  const head = applyKey(before, key, false);
  const text = formatExpression(head + after);
  return { text, caret: calculateNewCursor(head, text, head.length) };
}

/** Indian digit grouping (12,34,567.89); scientific notation beyond what grouping can show. */
export function formatNumber(v: number): string {
  if (Math.abs(v) >= 1e15 || (v !== 0 && Math.abs(v) < 1e-9)) return v.toExponential(8).replace(/\.?0+e/, "e");
  return v.toLocaleString("en-IN", { maximumFractionDigits: 10 });
}

/**
 * Formats an integer string with Indian digit grouping (e.g. "1234567" -> "12,34,567").
 * The last 3 digits form a group, and every 2 digits preceding form a group.
 */
export function formatIndianInteger(intStr: string): string {
  if (intStr.length <= 3) return intStr;
  const last3 = intStr.slice(-3);
  const rest = intStr.slice(0, -3);
  const withCommas = rest.replace(/\B(?=(\d{2})+(?!\d))/g, ",");
  return withCommas + "," + last3;
}

/**
 * Formats all numbers within an in-progress or completed calculator expression with Indian commas.
 * Preserves decimal points, fractional digits, operators, brackets, functions, and signs.
 * e.g. "100000+5000.5" -> "10,00,000+5,000.5", "1000." -> "1,000."
 */
export function formatExpression(expr: string): string {
  if (!expr) return "";
  return expr.replace(/(?:\d[\d,]*(?:\.\d*)?|\.\d+)/g, (match) => {
    const cleanMatch = match.replace(/,/g, "");
    if (!cleanMatch || cleanMatch === ".") return match;
    const [intPart, decPart] = cleanMatch.split(".");
    const formattedInt = formatIndianInteger(intPart);
    return decPart !== undefined ? `${formattedInt}.${decPart}` : formattedInt;
  });
}

const PASTE_CHARS = "0123456789.+-−×÷^%()√";

/**
 * Cleans and sanitizes pasted text into a valid calculator expression string.
 * Strips currency symbols (₹, $, €, £, etc.), labels ("Rs.", "INR", "USD"),
 * normalizes operators (* -> ×, / -> ÷, - -> −), removes grouping commas, and strips extra whitespace.
 */
export function sanitizePaste(raw: string): string {
  const s = pasteCleaned(raw).replace(/\s+/g, "");
  return s.split("").filter((c) => PASTE_CHARS.includes(c)).join("");
}

/** sanitizePaste up to, not including, dropping the whitespace and anything else left over. */
function pasteCleaned(raw: string): string {
  let s = raw.trim();
  if (s.includes("=")) {
    const parts = s.split("=");
    const left = parts[0].trim();
    const right = parts[1] ? parts[1].trim() : "";
    if (left && /\d/.test(left)) s = left;
    else if (right && /\d/.test(right)) s = right;
  }
  s = s.replace(/Rs\./gi, "").replace(/Rs/gi, "");
  s = s.replace(/\b(inr|usd|eur|gbp)\b/gi, "");
  s = s.replace(/[₹$€£¥]/g, "");
  s = s.replace(/\*/g, "×").replace(/\//g, "÷").replace(/[-–—]/g, "−");
  return s.replace(/,/g, "");
}

/**
 * Pasted text as a calculator expression, or why it can't be one. Unlike sanitizePaste on
 * its own, anything it would have to guess at is refused rather than quietly dropped:
 * "₹2.5 Cr" is not 2.5, "50k" is not 50, "1e6" is not 16, and "100\n200" is not 100200.
 */
export function readPaste(raw: string): { ok: true; text: string } | { ok: false; error: string } {
  if (raw.length > 500) return { ok: false, error: "Too long to paste" };
  const s = pasteCleaned(raw);
  if (/\d\s+[\d.]/.test(s)) return { ok: false, error: "More than one number" };
  if (s.split("").some((c) => !/\s/.test(c) && !PASTE_CHARS.includes(c))) return { ok: false, error: "Couldn't read a number" };
  const text = sanitizePaste(raw);
  return text ? { ok: true, text } : { ok: false, error: "Nothing to paste" };
}

/**
 * A number as plain text: digits with no exponent (1e-7 -> "0.0000001",
 * 1e21 -> "1000000000000000000000"), no grouping, and an ASCII "-" when negative, so any
 * other app (or a paste back into the calculator) reads it. Keeps every digit String() would give.
 */
export function plainNumber(v: number): string {
  let s = String(Math.abs(v));
  const m = s.match(/^(\d)(?:\.(\d+))?e([+-]\d+)$/);
  if (m) {
    const digits = m[1] + (m[2] ?? "");
    const exp = Number(m[3]);
    // String() only uses an exponent below 1e-6 or from 1e21, so a positive exponent
    // always has more places than digits.
    s = exp < 0 ? "0." + "0".repeat(-exp - 1) + digits : digits.padEnd(exp + 1, "0");
  }
  return v < 0 ? `-${s}` : s;
}

/**
 * A number as the calculator writes it into an expression: plainNumber's digits, minus as
 * "−", bracketed when negative.
 */
export function asOperand(v: number): string {
  const s = plainNumber(Math.abs(v));
  return v < 0 ? `(−${s})` : s;
}

/**
 * Filters text typed into the calculator display: maps * / and hyphens or dashes to × ÷ −,
 * then keeps only digits, ".", "," (regrouped by formatExpression), + − × ÷ ^ % ( ) and √.
 * Works character by character, so filtering a prefix gives a prefix of the result.
 */
export function filterTyped(raw: string): string {
  return raw
    .replace(/\*/g, "×")
    .replace(/\//g, "÷")
    .replace(/[-–—]/g, "−")
    .replace(/[^0-9.,+−×÷^%()√]/g, "");
}

/**
 * Formats a raw numeric string with Indian digit grouping as it is typed into input fields.
 * Preserves decimals and trailing dots (e.g. "2500000" -> "25,00,000", "1000." -> "1,000.").
 */
export function formatIndianNumberString(raw: string): string {
  if (!raw) return "";
  const clean = raw.replace(/[^0-9.]/g, "");
  if (!clean) return "";
  if (clean.startsWith(".")) return clean;
  const [intPart, decPart] = clean.split(".");
  const formattedInt = formatIndianInteger(intPart);
  return decPart !== undefined ? `${formattedInt}.${decPart}` : formattedInt;
}

/**
 * Preserves cursor position in an input field when grouping commas are inserted dynamically.
 */
export function calculateNewCursor(oldVal: string, newValFormatted: string, oldCursorPos: number): number {
  const digitsBefore = oldVal.slice(0, oldCursorPos).replace(/,/g, "").length;
  if (digitsBefore === 0) return 0;
  let count = 0;
  for (let i = 0; i < newValFormatted.length; i++) {
    if (newValFormatted[i] !== ",") {
      count++;
    }
    if (count === digitsBefore) {
      return i + 1;
    }
  }
  return newValFormatted.length;
}


