/**
 * Equated monthly instalment (EMI) and the loan's month-by-month schedule, on the reducing
 * balance method Indian lenders use: interest each month is charged on what is still owed.
 *
 *   EMI = P · r · (1 + r)^n / ((1 + r)^n − 1),   r = annual rate / 12 / 100,  n = months
 *
 * Prepayments are paid on top of that month's EMI and go straight to principal. Afterwards
 * the lender either keeps the EMI and shortens the loan ("tenure"), or keeps the end date
 * and lowers the EMI ("emi").
 */

export interface EmiRow {
  /** 1-based instalment number. */
  n: number;
  /** "YYYY-MM" of the instalment. */
  month: string;
  /** The EMI actually paid this month (the last one absorbs rounding). */
  emi: number;
  principal: number;
  interest: number;
  /** Extra paid towards principal this month, beyond the EMI. */
  prepayment: number;
  /** Still owed after this month's EMI and prepayment. */
  balance: number;
}

export interface EmiResult {
  /** The EMI at the start of the loan. */
  emi: number;
  /** The EMI in force when the loan ends (differs with a step-up, or with prepayments that reduce the EMI). */
  finalEmi: number;
  totalInterest: number;
  totalPrepaid: number;
  totalPayment: number;
  rows: EmiRow[];
}

export interface Prepayments {
  /** "YYYY-MM" from which the recurring prepayments (monthly, yearly, EMI step-up) begin,
   *  e.g. once a lender's lock-in ends. Unset: from the first EMI. One-time prepayments
   *  carry their own month and are not affected. */
  startMonth?: string;
  /** Extra paid every month, from `startMonth`. */
  monthly?: number;
  /** Extra paid once a year, in `yearlyMonth` (1-12), e.g. from an annual bonus. */
  yearly?: number;
  yearlyMonth?: number;
  /** EMI raised by this % on each loan anniversary (only with the "tenure" effect). */
  stepUpPct?: number;
  /** One-off payments. */
  oneTime?: { month: string; amount: number }[];
  /** What the lender does after a prepayment. */
  effect?: "tenure" | "emi";
}

export function monthlyEmi(principal: number, annualRatePct: number, months: number): number {
  if (principal <= 0 || months <= 0) return 0;
  const r = annualRatePct / 1200;
  if (r === 0) return principal / months;
  const f = Math.pow(1 + r, months);
  return (principal * r * f) / (f - 1);
}

export function addMonths(ym: string, k: number): string {
  const [y, m] = ym.split("-").map(Number);
  const idx = y * 12 + (m - 1) + k;
  return `${Math.floor(idx / 12)}-${String((idx % 12) + 1).padStart(2, "0")}`;
}

/** Months from `a` to `b` ("YYYY-MM"); negative when b is earlier. */
export function monthsBetween(a: string, b: string): number {
  const [ay, am] = a.split("-").map(Number);
  const [by, bm] = b.split("-").map(Number);
  return by * 12 + bm - (ay * 12 + am);
}

const EPS = 0.005; // half a paisa: anything smaller is rounding, not debt

/** The full schedule, with or without prepayments. The balance always ends at exactly zero. */
export function emiSchedule(principal: number, annualRatePct: number, months: number, startMonth: string, prepay: Prepayments = {}): EmiResult {
  const r = annualRatePct / 1200;
  const effect = prepay.effect ?? "tenure";
  const oneTime = new Map<string, number>();
  for (const p of prepay.oneTime ?? []) if (p.amount > 0) oneTime.set(p.month, (oneTime.get(p.month) ?? 0) + p.amount);

  const startEmi = monthlyEmi(principal, annualRatePct, months);
  let emi = startEmi;
  let balance = principal;
  const rows: EmiRow[] = [];

  for (let n = 1; balance > EPS && n <= 1200; n++) {
    const month = addMonths(startMonth, n - 1);
    // "YYYY-MM" strings compare correctly as text.
    const recurring = !prepay.startMonth || month >= prepay.startMonth;
    // A step-up still lands on a loan anniversary, the first one on or after the start month.
    if (effect === "tenure" && prepay.stepUpPct && recurring && n > 1 && (n - 1) % 12 === 0) emi *= 1 + prepay.stepUpPct / 100;

    const interest = balance * r;
    let pay = emi;
    let principalPart = emi - interest;
    // The last scheduled month, or an EMI that would overpay: settle exactly what is owed.
    if ((effect === "emi" && n >= months) || principalPart >= balance - EPS) {
      principalPart = balance;
      pay = balance + interest;
    }
    balance -= principalPart;

    let extra = 0;
    if (balance > EPS) {
      extra = (recurring ? prepay.monthly ?? 0 : 0) + (oneTime.get(month) ?? 0);
      if (recurring && prepay.yearly && Number(month.slice(5)) === (prepay.yearlyMonth ?? 3)) extra += prepay.yearly;
      extra = Math.min(Math.max(0, extra), balance);
      balance -= extra;
      // Same end date, smaller EMI: re-spread what is left over the months that remain.
      if (extra > 0 && effect === "emi" && balance > EPS) emi = monthlyEmi(balance, annualRatePct, Math.max(1, months - n));
    }
    if (balance < EPS) balance = 0;
    rows.push({ n, month, emi: pay, principal: principalPart, interest, prepayment: extra, balance });
  }

  const totalInterest = rows.reduce((s, x) => s + x.interest, 0);
  const totalPrepaid = rows.reduce((s, x) => s + x.prepayment, 0);
  return { emi: startEmi, finalEmi: emi, totalInterest, totalPrepaid, totalPayment: principal + totalInterest, rows };
}

export interface EmiYear {
  /** "2026" (calendar) or "FY 2026-27" (April to March). */
  label: string;
  principal: number;
  interest: number;
  prepayment: number;
  /** Owed at the end of the year. */
  balance: number;
  rows: EmiRow[];
}

/** Groups the schedule by calendar year, or by Indian financial year (April to March) --
 *  the one home-loan tax deductions are claimed in. */
export function groupByYear(rows: EmiRow[], basis: "calendar" | "financial"): EmiYear[] {
  const out: EmiYear[] = [];
  for (const row of rows) {
    const [y, m] = row.month.split("-").map(Number);
    const fyStart = m >= 4 ? y : y - 1;
    const label = basis === "calendar" ? String(y) : `FY ${fyStart}-${String((fyStart + 1) % 100).padStart(2, "0")}`;
    let g = out[out.length - 1];
    if (!g || g.label !== label) {
      g = { label, principal: 0, interest: 0, prepayment: 0, balance: 0, rows: [] };
      out.push(g);
    }
    g.principal += row.principal;
    g.interest += row.interest;
    g.prepayment += row.prepayment;
    g.balance = row.balance;
    g.rows.push(row);
  }
  return out;
}

/** "4 yr 3 mo" */
export function durationLabel(months: number): string {
  const y = Math.floor(months / 12);
  const m = months % 12;
  return [y ? `${y} yr` : "", m ? `${m} mo` : ""].filter(Boolean).join(" ") || "0 mo";
}
