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
      left = op === "+" ? left + r : left - r;
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

/** Trims binary floating-point noise: 0.1 + 0.2 shows as 0.3, not 0.30000000000000004. */
export const tidy = (v: number) => Number(v.toPrecision(12));

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
export const isOperator = (c: string | undefined) => !!c && OPS.includes(c);
const endsWithValue = (s: string) => /[0-9)%]$/.test(s);

/** ± on the number being typed: 5 → −5, 5+3 → 5+(−3), and back again. */
function toggleSign(base: string): string {
  const wrapped = base.match(/^(.*)\(−([0-9.]+)\)$/);
  if (wrapped) return wrapped[1] + wrapped[2];
  const m = base.match(/^(.*?)([0-9.]+)$/);
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

/** Indian digit grouping (12,34,567.89); scientific notation beyond what grouping can show. */
export function formatNumber(v: number): string {
  if (Math.abs(v) >= 1e15 || (v !== 0 && Math.abs(v) < 1e-9)) return v.toExponential(8).replace(/\.?0+e/, "e");
  return v.toLocaleString("en-IN", { maximumFractionDigits: 10 });
}
