import type { ComponentType } from "react";

import { BasicCalculator } from "./BasicCalculator";
import { EmiCalculator } from "./EmiCalculator";

/**
 * Every calculator on the Calculator page. To add one: build its component in this folder
 * and add an entry here. The page's left rail groups entries by `category` (in the order
 * categories first appear), and `id` is what the URL's ?tool= holds.
 */
export interface CalculatorDef {
  id: string;
  label: string;
  category: string;
  description: string;
  Component: ComponentType;
}

export const CALCULATORS: CalculatorDef[] = [
  {
    id: "basic",
    label: "Calculator",
    category: "General",
    description: "Everyday arithmetic with brackets, percent and a running history.",
    Component: BasicCalculator,
  },
  {
    id: "emi",
    label: "EMI",
    category: "Loans",
    description: "Monthly instalment, total interest and the full repayment schedule for a home, car or personal loan.",
    Component: EmiCalculator,
  },
];
