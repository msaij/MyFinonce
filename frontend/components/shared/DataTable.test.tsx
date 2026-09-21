import React from "react";
import { createRoot, Root } from "react-dom/client";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  DataTable,
  ColumnConfig,
  compareTableValues,
  isMissingValue,
  parseNumericValue,
  parseDateValue,
  isMissingOrInvalid,
  inferColumnFormat,
  sortTableRows,
} from "./DataTable";

describe("DataTable - Unit Tests (Comparator & Row Sorter)", () => {
  const sampleColumns: ColumnConfig[] = [
    { key: "scheme_code", label: "AMFI Code", format: "number", decimals: 0 },
    { key: "scheme_name", label: "Scheme Name" },
    { key: "nav", label: "NAV", format: "inr" },
    { key: "return_1y", label: "1Y Return", format: "signed_pct" },
    { key: "nav_date", label: "Date", format: "date" },
  ];

  describe("isMissingValue", () => {
    it("identifies null, undefined, NaN, invalid Date, and placeholder strings as missing", () => {
      expect(isMissingValue(null)).toBe(true);
      expect(isMissingValue(undefined)).toBe(true);
      expect(isMissingValue(NaN)).toBe(true);
      expect(isMissingValue(new Date("invalid"))).toBe(true);
      expect(isMissingValue("")).toBe(true);
      expect(isMissingValue("   ")).toBe(true);
      expect(isMissingValue("-")).toBe(true);
      expect(isMissingValue("--")).toBe(true);
      expect(isMissingValue("---")).toBe(true);
      expect(isMissingValue("—")).toBe(true); // em dash
      expect(isMissingValue("–")).toBe(true); // en dash
      expect(isMissingValue("−")).toBe(true); // Unicode minus
      expect(isMissingValue(" - ")).toBe(true);
      expect(isMissingValue("N/A")).toBe(true);
      expect(isMissingValue("n/a")).toBe(true);
      expect(isMissingValue("N / A")).toBe(true);
      expect(isMissingValue("N.A.")).toBe(true);
      expect(isMissingValue("NA")).toBe(true);
      expect(isMissingValue("na")).toBe(true);
      expect(isMissingValue("ND")).toBe(true); // Not Disclosed
      expect(isMissingValue("nd")).toBe(true);
      expect(isMissingValue("n/d")).toBe(true);
      expect(isMissingValue("null")).toBe(true);
      expect(isMissingValue("None")).toBe(true);
      expect(isMissingValue("none")).toBe(true);
      expect(isMissingValue("undefined")).toBe(true);
      expect(isMissingValue("NaN")).toBe(true);
      expect(isMissingValue("nan")).toBe(true);
      expect(isMissingValue("nil")).toBe(true);
      expect(isMissingValue("not available")).toBe(true);
      expect(isMissingValue("no data")).toBe(true);
      expect(isMissingValue(Infinity)).toBe(true);
      expect(isMissingValue(-Infinity)).toBe(true);
      expect(isMissingValue("inf")).toBe(true);
      expect(isMissingValue("-inf")).toBe(true);
      expect(isMissingValue("+inf")).toBe(true);
      expect(isMissingValue("infinity")).toBe(true);
      expect(isMissingValue("-infinity")).toBe(true);
      expect(isMissingValue("+infinity")).toBe(true);
    });

    it("does not treat 0, false, or valid numbers as missing", () => {
      expect(isMissingValue(0)).toBe(false);
      expect(isMissingValue(false)).toBe(false);
      expect(isMissingValue(-10.5)).toBe(false);
      expect(isMissingValue("0")).toBe(false);
      expect(isMissingValue("Valid Text")).toBe(false);
      expect(isMissingValue(new Date("2024-01-01"))).toBe(false);
    });
  });

  describe("parseNumericValue", () => {
    it("correctly parses raw numbers and bigints", () => {
      expect(parseNumericValue(123)).toBe(123);
      expect(parseNumericValue(-45.67)).toBe(-45.67);
      expect(parseNumericValue(0)).toBe(0);
      expect(parseNumericValue(BigInt(500))).toBe(500);
      expect(parseNumericValue(NaN)).toBeNull();
      expect(parseNumericValue(Infinity)).toBeNull();
      expect(parseNumericValue(-Infinity)).toBeNull();
      expect(parseNumericValue("Infinity")).toBeNull();
      expect(parseNumericValue("-Infinity")).toBeNull();
    });

    it("rejects hexadecimal, binary, and octal numeric prefixes", () => {
      expect(parseNumericValue("0x10")).toBeNull();
      expect(parseNumericValue("0xABCD")).toBeNull();
      expect(parseNumericValue("0b1010")).toBeNull();
      expect(parseNumericValue("0o77")).toBeNull();
    });

    it("robustly parses negative INR currency strings produced by Intl.NumberFormat and Unicode minus", () => {
      expect(parseNumericValue("-₹500.00")).toBe(-500);
      expect(parseNumericValue("₹-500.00")).toBe(-500);
      expect(parseNumericValue("- ₹500.00")).toBe(-500);
      expect(parseNumericValue("−₹500.00")).toBe(-500); // Unicode mathematical minus
      expect(parseNumericValue("− 12.5%")).toBe(-12.5);
      expect(parseNumericValue("(₹500.00)")).toBe(-500);
      expect(parseNumericValue("+₹500.00")).toBe(500);
      expect(parseNumericValue("₹ 1,23,456.78")).toBe(123456.78);
      expect(parseNumericValue("Rs. 500.00")).toBe(500);
      expect(parseNumericValue("INR 1000")).toBe(1000);
      expect(parseNumericValue("-Rs. 250")).toBe(-250);
    });

    it("parses Indian magnitude multipliers (Cr / Lakh)", () => {
      expect(parseNumericValue("10 Lakh")).toBe(1000000);
      expect(parseNumericValue("2 Cr")).toBe(20000000);
      expect(parseNumericValue("₹ 1,234.56 Cr")).toBe(12345600000);
      expect(parseNumericValue("-5 Cr")).toBe(-50000000);
    });

    it("parses signed percentages and accounting parenthesis formats", () => {
      expect(parseNumericValue("+12.5%")).toBe(12.5);
      expect(parseNumericValue("-2.4%")).toBe(-2.4);
      expect(parseNumericValue("(15.0%)")).toBe(-15.0);
      expect(parseNumericValue(" 0.00% ")).toBe(0);
    });

    it("returns null for non-numeric, empty, or placeholder strings", () => {
      expect(parseNumericValue("")).toBeNull();
      expect(parseNumericValue("-")).toBeNull();
      expect(parseNumericValue("—")).toBeNull();
      expect(parseNumericValue("N/A")).toBeNull();
      expect(parseNumericValue("Corrupt Data")).toBeNull();
      expect(parseNumericValue(null)).toBeNull();
      expect(parseNumericValue(undefined)).toBeNull();
    });
  });

  describe("parseDateValue", () => {
    it("parses Date objects and ISO strings", () => {
      const d = new Date("2024-03-15");
      expect(parseDateValue(d)).toBe(d.getTime());
      expect(parseDateValue("2024-03-15")).toBe(d.getTime());
      expect(parseDateValue(new Date("invalid"))).toBeNull();
    });

    it("parses Indian and European DD-MM-YYYY and DD/MM/YYYY date strings", () => {
      const expectedUtc = Date.UTC(2024, 2, 15); // 2024-03-15
      expect(parseDateValue("15/03/2024")).toBe(expectedUtc);
      expect(parseDateValue("15-03-2024")).toBe(expectedUtc);
      expect(parseDateValue("15 Mar 2024")).not.toBeNull();
    });

    it("strictly rejects calendar rollover dates (e.g. 31/02/2024, 29/02/2023, 99/99/2024)", () => {
      expect(parseDateValue("31/02/2024")).toBeNull(); // Feb 31 does not exist
      expect(parseDateValue("29/02/2023")).toBeNull(); // 2023 is not a leap year
      expect(parseDateValue("29/02/2024")).not.toBeNull(); // 2024 is a leap year
      expect(parseDateValue("99/99/2024")).toBeNull();
      expect(parseDateValue("2024-02-31")).toBeNull();
      expect(parseDateValue("2024-99-99")).toBeNull();
    });

    it("does NOT parse decimal numbers, pure numeric strings, or hyphenated ranges/scores as dates", () => {
      expect(parseDateValue("1.5")).toBeNull();
      expect(parseDateValue("2.5")).toBeNull();
      expect(parseDateValue("123")).toBeNull();
      expect(parseDateValue("-10.5")).toBeNull();
      expect(parseDateValue("1-2")).toBeNull(); // ranges or scores must not parse as year 2001
      expect(parseDateValue("5/10")).toBeNull();
      expect(parseDateValue("10-20")).toBeNull();
      expect(parseDateValue("Large & Mid-Cap")).toBeNull();
    });

    it("parses numeric epoch timestamps when format is explicitly 'date'", () => {
      const ts = 1710460800000;
      expect(parseDateValue(ts, "date")).toBe(ts);
      expect(parseDateValue(ts)).toBeNull(); // not treated as date when format is unspecified
    });
  });

  describe("inferColumnFormat", () => {
    it("infers 'number' for numeric columns with numbers or formatted currency (including Rs. and INR)", () => {
      const rows = [
        { id: 1, val: 50 },
        { id: 2, val: "Corrupt" },
        { id: 3, val: 20 },
      ];
      expect(inferColumnFormat(rows, "val")).toBe("number");

      const inrRows = [
        { id: 1, val: "Rs. 500" },
        { id: 2, val: "Rs. 1000" },
        { id: 3, val: "Rs. 250" },
      ];
      expect(inferColumnFormat(inrRows, "val")).toBe("number");

      const inrPrefixRows = [
        { id: 1, val: "INR 2000" },
        { id: 2, val: "INR 500" },
      ];
      expect(inferColumnFormat(inrPrefixRows, "val")).toBe("number");
    });

    it("infers 'text' for columns where numbers and text strings are equally split (50/50)", () => {
      const splitRows = [
        { id: 1, val: 100 },
        { id: 2, val: "Beta" },
      ];
      expect(inferColumnFormat(splitRows, "val")).toBe("text");
    });

    it("infers 'date' for columns with Date objects or ISO date strings", () => {
      const rows = [
        { id: 1, d: "2024-03-15" },
        { id: 2, d: "invalid-date" },
        { id: 3, d: "2024-01-10" },
      ];
      expect(inferColumnFormat(rows, "d")).toBe("date");
    });

    it("infers 'text' for generic string columns with names or category labels", () => {
      const rows = [
        { id: 1, name: "Axis Bluechip" },
        { id: 2, name: "HDFC Top 100" },
        { id: 3, name: "SBI Mutual" },
      ];
      expect(inferColumnFormat(rows, "name")).toBe("text");
    });
  });

  describe("Numeric Sorting", () => {
    const rows = [
      { scheme_code: 102, nav: 125.5, return_1y: -2.4 },
      { scheme_code: 101, nav: 45.2, return_1y: 18.5 },
      { scheme_code: 104, nav: 210.0, return_1y: 0.0 },
      { scheme_code: 103, nav: 12.0, return_1y: -15.8 },
    ];

    it("sorts raw numbers ascending and descending", () => {
      const asc = sortTableRows(rows, "scheme_code", "asc", sampleColumns);
      expect(asc.map((r) => r.scheme_code)).toEqual([101, 102, 103, 104]);

      const desc = sortTableRows(rows, "scheme_code", "desc", sampleColumns);
      expect(desc.map((r) => r.scheme_code)).toEqual([104, 103, 102, 101]);
    });

    it("sorts currency and negative percentage returns numerically", () => {
      const ascNav = sortTableRows(rows, "nav", "asc", sampleColumns);
      expect(ascNav.map((r) => r.nav)).toEqual([12.0, 45.2, 125.5, 210.0]);

      const descReturns = sortTableRows(rows, "return_1y", "desc", sampleColumns);
      expect(descReturns.map((r) => r.return_1y)).toEqual([18.5, 0.0, -2.4, -15.8]);
    });

    it("properly sorts formatted negative INR currency without producing NaN", () => {
      const inrRows = [
        { id: 1, inrVal: "₹ 100.00" },
        { id: 2, inrVal: "-₹500.00" },
        { id: 3, inrVal: "₹ 0.00" },
        { id: 4, inrVal: "- ₹50.00" },
      ];
      const cols: ColumnConfig[] = [{ key: "inrVal", label: "INR", format: "inr" }];

      const asc = sortTableRows(inrRows, "inrVal", "asc", cols);
      expect(asc.map((r) => r.id)).toEqual([2, 4, 3, 1]); // -500, -50, 0, 100

      const desc = sortTableRows(inrRows, "inrVal", "desc", cols);
      expect(desc.map((r) => r.id)).toEqual([1, 3, 4, 2]); // 100, 0, -50, -500
    });

    it("sorts decimal numbers as numeric values rather than false dates when format is unspecified", () => {
      const decimalRows = [
        { id: 1, val: "1.15" },
        { id: 2, val: "2.5" },
        { id: 3, val: "1.5" },
        { id: 4, val: "1.35" },
      ];
      const cols: ColumnConfig[] = [{ key: "val", label: "Value" }];

      const asc = sortTableRows(decimalRows, "val", "asc", cols);
      expect(asc.map((r) => r.id)).toEqual([1, 4, 3, 2]); // 1.15, 1.35, 1.5, 2.5

      const desc = sortTableRows(decimalRows, "val", "desc", cols);
      expect(desc.map((r) => r.id)).toEqual([2, 3, 4, 1]); // 2.5, 1.5, 1.35, 1.15
    });

    it("sorts numeric strings numerically rather than lexicographically", () => {
      const numStringRows = [
        { code: "100" },
        { code: "20" },
        { code: "5" },
        { code: "15" },
      ];
      const cols: ColumnConfig[] = [{ key: "code", label: "Code" }];

      const asc = sortTableRows(numStringRows, "code", "asc", cols);
      expect(asc.map((r) => r.code)).toEqual(["5", "15", "20", "100"]);

      const desc = sortTableRows(numStringRows, "code", "desc", cols);
      expect(desc.map((r) => r.code)).toEqual(["100", "20", "15", "5"]);
    });
  });

  describe("Date Sorting", () => {
    const dateRows = [
      { id: 1, nav_date: "2024-03-15" },
      { id: 2, nav_date: "2023-01-10" },
      { id: 3, nav_date: "2024-12-31" },
      { id: 4, nav_date: "2023-08-20" },
    ];

    it("sorts date strings chronologically ascending and descending", () => {
      const asc = sortTableRows(dateRows, "nav_date", "asc", sampleColumns);
      expect(asc.map((r) => r.id)).toEqual([2, 4, 1, 3]);

      const desc = sortTableRows(dateRows, "nav_date", "desc", sampleColumns);
      expect(desc.map((r) => r.id)).toEqual([3, 1, 4, 2]);
    });

    it("sorts native Date objects chronologically", () => {
      const nativeDateRows = [
        { id: 1, d: new Date("2024-05-01") },
        { id: 2, d: new Date("2022-01-01") },
        { id: 3, d: new Date("2023-06-15") },
      ];
      const cols: ColumnConfig[] = [{ key: "d", label: "Date" }];

      const asc = sortTableRows(nativeDateRows, "d", "asc", cols);
      expect(asc.map((r) => r.id)).toEqual([2, 3, 1]);
    });

    it("handles mixed Date objects and ISO strings in the same column", () => {
      const mixedDateRows = [
        { id: 1, d: new Date("2024-03-01") },
        { id: 2, d: "2022-05-10" },
        { id: 3, d: "2025-01-01" },
        { id: 4, d: new Date("2023-11-20") },
      ];
      const cols: ColumnConfig[] = [{ key: "d", label: "Date" }];

      const asc = sortTableRows(mixedDateRows, "d", "asc", cols);
      expect(asc.map((r) => r.id)).toEqual([2, 4, 1, 3]);

      const desc = sortTableRows(mixedDateRows, "d", "desc", cols);
      expect(desc.map((r) => r.id)).toEqual([3, 1, 4, 2]);
    });
  });

  describe("String Sorting (Case-Insensitive & Natural Collation)", () => {
    const textRows = [
      { name: "SBI Bluechip" },
      { name: "axis Growth" },
      { name: "HDFC Top 100" },
      { name: "Axis Small Cap" },
      { name: "kotak Flexicap" },
    ];
    const textCols: ColumnConfig[] = [{ key: "name", label: "Scheme Name" }];

    it("sorts case-insensitively in alphabetical order", () => {
      const asc = sortTableRows(textRows, "name", "asc", textCols);
      const namesAsc = asc.map((r) => r.name);
      expect(namesAsc).toEqual([
        "axis Growth",
        "Axis Small Cap",
        "HDFC Top 100",
        "kotak Flexicap",
        "SBI Bluechip",
      ]);

      const desc = sortTableRows(textRows, "name", "desc", textCols);
      const namesDesc = desc.map((r) => r.name);
      expect(namesDesc).toEqual([
        "SBI Bluechip",
        "kotak Flexicap",
        "HDFC Top 100",
        "Axis Small Cap",
        "axis Growth",
      ]);
    });

    it("applies natural numeric collation to alphanumeric strings", () => {
      const funds = [
        { name: "Fund 10" },
        { name: "Fund 2" },
        { name: "Fund 1" },
        { name: "Fund 20" },
      ];
      const cols: ColumnConfig[] = [{ key: "name", label: "Fund" }];

      const asc = sortTableRows(funds, "name", "asc", cols);
      expect(asc.map((r) => r.name)).toEqual(["Fund 1", "Fund 2", "Fund 10", "Fund 20"]);
    });
  });

  describe("Robust Null, Undefined, and Missing/Invalid Value Handling", () => {
    const mixedRows = [
      { id: 1, val: 50 },
      { id: 2, val: null },
      { id: 3, val: -10 },
      { id: 4, val: undefined },
      { id: 5, val: 20 },
      { id: 6, val: "" },
      { id: 7, val: "N/A" },
      { id: 8, val: "Corrupt" },
    ];
    const mixedCols: ColumnConfig[] = [{ key: "val", label: "Value", format: "number" }];

    it("places null, undefined, and unparseable values consistently at the end in ascending sort", () => {
      const asc = sortTableRows(mixedRows, "val", "asc", mixedCols);
      const validIds = asc.slice(0, 3).map((r) => r.id);
      const missingIds = asc.slice(3).map((r) => r.id);

      expect(validIds).toEqual([3, 5, 1]); // -10, 20, 50
      expect(missingIds).toEqual([2, 4, 6, 7, 8]); // null, undefined, "", "N/A", "Corrupt" at end
    });

    it("places null, undefined, and unparseable values consistently at the end in descending sort", () => {
      const desc = sortTableRows(mixedRows, "val", "desc", mixedCols);
      const validIds = desc.slice(0, 3).map((r) => r.id);
      const missingIds = desc.slice(3).map((r) => r.id);

      expect(validIds).toEqual([1, 5, 3]); // 50, 20, -10
      expect(missingIds).toEqual([2, 4, 6, 7, 8]); // missing and invalid remain stably at the end!
    });

    it("handles date column with invalid Date objects and unparseable date strings consistently at the end", () => {
      const dateNullRows = [
        { id: 1, date: "2024-01-01" },
        { id: 2, date: null },
        { id: 3, date: new Date("invalid") },
        { id: 4, date: "2023-01-01" },
        { id: 5, date: "not-a-date" },
      ];
      const cols: ColumnConfig[] = [{ key: "date", label: "Date", format: "date" }];

      const asc = sortTableRows(dateNullRows, "date", "asc", cols);
      expect(asc.slice(0, 2).map((r) => r.id)).toEqual([4, 1]); // 2023-01-01, 2024-01-01
      expect(asc.slice(2).map((r) => r.id)).toEqual([2, 3, 5]); // invalid/null at end

      const desc = sortTableRows(dateNullRows, "date", "desc", cols);
      expect(desc.slice(0, 2).map((r) => r.id)).toEqual([1, 4]); // 2024-01-01, 2023-01-01
      expect(desc.slice(2).map((r) => r.id)).toEqual([2, 3, 5]); // invalid/null still at end!
    });

    it("handles column where all values are missing without errors", () => {
      const allNullRows = [
        { id: 1, val: null },
        { id: 2, val: undefined },
        { id: 3, val: "N/A" },
      ];
      const cols: ColumnConfig[] = [{ key: "val", label: "Value" }];

      const asc = sortTableRows(allNullRows, "val", "asc", cols);
      expect(asc.map((r) => r.id)).toEqual([1, 2, 3]);

      const desc = sortTableRows(allNullRows, "val", "desc", cols);
      expect(desc.map((r) => r.id)).toEqual([1, 2, 3]);
    });

    it("keeps corrupt, em-dash, and invalid values at the end in descending sort even when format is unspecified", () => {
      // Screener table columns like expense_ratio omit format because render handles display
      const unformattedNumericRows = [
        { id: 1, val: 50 },
        { id: 2, val: "Corrupt" },
        { id: 3, val: 100 },
        { id: 4, val: "—" }, // em-dash placeholder
        { id: 5, val: 20 },
        { id: 6, val: null },
      ];
      const unformattedCols: ColumnConfig[] = [{ key: "val", label: "Value" }];

      const asc = sortTableRows(unformattedNumericRows, "val", "asc", unformattedCols);
      expect(asc.slice(0, 3).map((r) => r.id)).toEqual([5, 1, 3]); // 20, 50, 100
      expect(asc.slice(3).map((r) => r.id)).toEqual([2, 4, 6]); // Corrupt, —, null at end

      const desc = sortTableRows(unformattedNumericRows, "val", "desc", unformattedCols);
      expect(desc.slice(0, 3).map((r) => r.id)).toEqual([3, 1, 5]); // 100, 50, 20
      expect(desc.slice(3).map((r) => r.id)).toEqual([2, 4, 6]); // Corrupt, —, null STILL stably at end!
    });

    it("keeps invalid date strings at the end in descending sort even when format is unspecified", () => {
      const unformattedDateRows = [
        { id: 1, d: "2024-03-15" },
        { id: 2, d: "invalid-date" },
        { id: 3, d: "2024-01-10" },
      ];
      const unformattedCols: ColumnConfig[] = [{ key: "d", label: "Date" }];

      const desc = sortTableRows(unformattedDateRows, "d", "desc", unformattedCols);
      expect(desc.slice(0, 2).map((r) => r.id)).toEqual([1, 3]); // 2024-03-15, 2024-01-10
      expect(desc[2].id).toBe(2); // invalid-date stays at the end!
    });

    it("sorts Indian DD-MM-YYYY and DD/MM/YYYY dates chronologically", () => {
      const indianDateRows = [
        { id: 1, d: "15/03/2024" },
        { id: 2, d: "10/01/2023" },
        { id: 3, d: "31/12/2024" },
        { id: 4, d: "20/08/2023" },
      ];
      const cols: ColumnConfig[] = [{ key: "d", label: "Date", format: "date" }];

      const asc = sortTableRows(indianDateRows, "d", "asc", cols);
      expect(asc.map((r) => r.id)).toEqual([2, 4, 1, 3]);

      const desc = sortTableRows(indianDateRows, "d", "desc", cols);
      expect(desc.map((r) => r.id)).toEqual([3, 1, 4, 2]);
    });

    it("strictly respects format: 'text' by comparing alphabetically without numeric coercion", () => {
      const textRows = [
        { id: 1, code: "123" },
        { id: 2, code: "0123" },
      ];
      const cols: ColumnConfig[] = [{ key: "code", label: "Code", format: "text" }];

      // In format: 'text', '0123' and '123' are not considered equal
      const asc = sortTableRows(textRows, "code", "asc", cols);
      expect(asc.map((r) => r.id)).toEqual([2, 1]); // '0123' before '123'

      expect(compareTableValues("0123", "123", "text")).not.toBe(0);
    });

    it("sorts score and ratio strings (e.g. '1-2', '5-10') as text rather than false dates", () => {
      const ratioRows = [
        { id: 1, v: "10-20" },
        { id: 2, v: "1-2" },
        { id: 3, v: "5-10" },
      ];
      const cols: ColumnConfig[] = [{ key: "v", label: "Ratio" }];

      const asc = sortTableRows(ratioRows, "v", "asc", cols);
      expect(asc.map((r) => r.v)).toEqual(["1-2", "5-10", "10-20"]);

      const desc = sortTableRows(ratioRows, "v", "desc", cols);
      expect(desc.map((r) => r.v)).toEqual(["10-20", "5-10", "1-2"]);
    });

    it("places non-finite and arithmetic overflow values (Infinity, -Infinity, 'inf') consistently at the end", () => {
      const infRows = [
        { id: 1, val: 50 },
        { id: 2, val: Infinity },
        { id: 3, val: -10 },
        { id: 4, val: -Infinity },
        { id: 5, val: "Infinity" },
        { id: 6, val: "inf" },
        { id: 7, val: 25 },
      ];
      const cols: ColumnConfig[] = [{ key: "val", label: "Val", format: "number" }];

      const asc = sortTableRows(infRows, "val", "asc", cols);
      expect(asc.slice(0, 3).map((r) => r.id)).toEqual([3, 7, 1]); // -10, 25, 50
      expect(asc.slice(3).map((r) => r.id)).toEqual([2, 4, 5, 6]); // non-finite at end!

      const desc = sortTableRows(infRows, "val", "desc", cols);
      expect(desc.slice(0, 3).map((r) => r.id)).toEqual([1, 7, 3]); // 50, 25, -10
      expect(desc.slice(3).map((r) => r.id)).toEqual([2, 4, 5, 6]); // non-finite STILL at end!
    });

    it("places calendar rollover dates (e.g. 99/99/2024, 31/02/2024) consistently at the end instead of sorting to top", () => {
      const invalidDateRows = [
        { id: 1, d: "15/03/2024" },
        { id: 2, d: "99/99/2024" },
        { id: 3, d: "10/01/2023" },
        { id: 4, d: "31/02/2024" },
      ];
      const cols: ColumnConfig[] = [{ key: "d", label: "Date", format: "date" }];

      const asc = sortTableRows(invalidDateRows, "d", "asc", cols);
      expect(asc.slice(0, 2).map((r) => r.id)).toEqual([3, 1]); // 2023-01-10, 2024-03-15
      expect(asc.slice(2).map((r) => r.id)).toEqual([2, 4]); // invalid dates at end

      const desc = sortTableRows(invalidDateRows, "d", "desc", cols);
      expect(desc.slice(0, 2).map((r) => r.id)).toEqual([1, 3]); // 2024-03-15, 2023-01-10
      expect(desc.slice(2).map((r) => r.id)).toEqual([2, 4]); // invalid dates still at end (not year 2032 at top!)
    });

    it("sorts 50/50 number/text mixed columns as text without discarding text rows as missing", () => {
      const halfHalfRows = [
        { id: 1, val: 100 },
        { id: 2, val: "Beta" },
      ];
      const cols: ColumnConfig[] = [{ key: "val", label: "Val" }];

      const desc = sortTableRows(halfHalfRows, "val", "desc", cols);
      expect(desc.map((r) => r.id)).toEqual([2, 1]); // 'Beta' before '100' in descending text sort
    });
  });

  describe("Stability and Exact Restoration", () => {
    it("preserves exact original row sequence when sort is reset (null / null)", () => {
      const originalRows = [
        { id: "A", score: 80 },
        { id: "B", score: 20 },
        { id: "C", score: 95 },
        { id: "D", score: 50 },
      ];
      const cols: ColumnConfig[] = [{ key: "score", label: "Score", format: "number" }];

      const sorted = sortTableRows(originalRows, "score", "asc", cols);
      expect(sorted.map((r) => r.id)).toEqual(["B", "D", "A", "C"]);

      const reset = sortTableRows(originalRows, null, null, cols);
      expect(reset).toBe(originalRows);
      expect(reset.map((r) => r.id)).toEqual(["A", "B", "C", "D"]);
    });

    it("maintains stable ordering for equal values", () => {
      const tiedRows = [
        { id: 1, group: "Alpha" },
        { id: 2, group: "Beta" },
        { id: 3, group: "Alpha" },
        { id: 4, group: "Alpha" },
      ];
      const cols: ColumnConfig[] = [{ key: "group", label: "Group" }];

      const asc = sortTableRows(tiedRows, "group", "asc", cols);
      const alphaIds = asc.filter((r) => r.group === "Alpha").map((r) => r.id);
      expect(alphaIds).toEqual([1, 3, 4]);
    });

    it("respects sortValue function if provided on column config", () => {
      const rowsWithNested = [
        { id: 1, metrics: { ratio: 0.85 } },
        { id: 2, metrics: { ratio: 0.12 } },
        { id: 3, metrics: { ratio: 0.45 } },
      ];
      const cols: ColumnConfig[] = [
        {
          key: "ratio",
          label: "Ratio",
          format: "number",
          sortValue: (row) => (row.metrics as { ratio: number }).ratio,
        },
      ];

      const asc = sortTableRows(rowsWithNested, "ratio", "asc", cols);
      expect(asc.map((r) => r.id)).toEqual([2, 3, 1]);

      const desc = sortTableRows(rowsWithNested, "ratio", "desc", cols);
      expect(desc.map((r) => r.id)).toEqual([1, 3, 2]);
    });

    it("gracefully handles sortValue throwing an exception without crashing", () => {
      const brokenRows = [
        { id: 1, val: 50 },
        { id: 2, val: null },
      ];
      const cols: ColumnConfig[] = [
        {
          key: "val",
          label: "Val",
          sortValue: (row) => {
            if (row.val === null) throw new Error("Unexpected null");
            return row.val;
          },
        },
      ];

      expect(() => sortTableRows(brokenRows, "val", "asc", cols)).not.toThrow();
      const asc = sortTableRows(brokenRows, "val", "asc", cols);
      expect(asc.map((r) => r.id)).toEqual([1, 2]);
    });
  });
});

describe("DataTable - Interactive Component & DOM Tests", () => {
  let container: HTMLDivElement | null = null;
  let root: Root | null = null;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    if (root && container) {
      root.unmount();
      await new Promise((r) => setTimeout(r, 10));
      container.remove();
      container = null;
      root = null;
    }
  });

  async function renderTable(ui: React.ReactElement) {
    root!.render(ui);
    await new Promise((r) => setTimeout(r, 10));
  }

  async function clickHeader(headerEl: Element) {
    headerEl.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    await new Promise((r) => setTimeout(r, 10));
  }

  async function keyPress(el: Element, key: string) {
    el.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
    await new Promise((r) => setTimeout(r, 10));
  }

  const columns: ColumnConfig[] = [
    { key: "code", label: "AMFI Code", format: "number", decimals: 0 },
    { key: "name", label: "Scheme Name" },
    { key: "ter", label: "TER %", format: "signed_pct" },
    { key: "action", label: "Action", sortable: false },
  ];

  const testRows = [
    { code: 300, name: "SBI Mutual Fund", ter: 1.5, action: "View" },
    { code: 100, name: "Axis Bluechip", ter: 0.8, action: "View" },
    { code: 200, name: "HDFC Top 100", ter: 1.2, action: "View" },
  ];

  function getHeaderCells(): HTMLElement[] {
    return Array.from(container!.querySelectorAll("thead th"));
  }

  function getRenderedColumnValues(colIndex: number): string[] {
    const trs = Array.from(container!.querySelectorAll("tbody tr"));
    return trs.map((tr) => {
      const tds = tr.querySelectorAll("td");
      return tds[colIndex]?.textContent?.trim() ?? "";
    });
  }

  it("renders with initial unsorted state, clear hover affordance, and neutral indicators", async () => {
    await renderTable(<DataTable columns={columns} rows={testRows} keyField="code" />);

    const ths = getHeaderCells();
    expect(ths.length).toBe(4);

    // First 3 columns are sortable
    for (let i = 0; i < 3; i++) {
      expect(ths[i].getAttribute("aria-sort")).toBe("none");
      expect(ths[i].getAttribute("data-sort-direction")).toBe("none");
      expect(ths[i].classList.contains("cursor-pointer")).toBe(true);

      const indicator = ths[i].querySelector("[data-testid^='sort-indicator-']");
      expect(indicator).not.toBeNull();
      expect(indicator?.textContent).toContain("↕");
    }

    // 4th column has sortable: false
    expect(ths[3].getAttribute("aria-sort")).toBeNull();
    expect(ths[3].classList.contains("cursor-pointer")).toBe(false);
    expect(ths[3].querySelector("[data-testid^='sort-indicator-']")).toBeNull();

    // Initial rows match original order
    expect(getRenderedColumnValues(0)).toEqual(["300", "100", "200"]);
  });

  it("cycles through all 3 states: unsorted -> ascending -> descending -> reset to original order", async () => {
    const onSortChange = vi.fn();
    await renderTable(
      <DataTable
        columns={columns}
        rows={testRows}
        keyField="code"
        onSortChange={onSortChange}
      />
    );

    const ths = getHeaderCells();
    const codeHeader = ths[0];
    const codeIndicator = () => codeHeader.querySelector("[data-testid='sort-indicator-code']");

    // 1. FIRST CLICK -> Ascending
    await clickHeader(codeHeader);
    expect(codeHeader.getAttribute("aria-sort")).toBe("ascending");
    expect(codeHeader.getAttribute("data-sort-direction")).toBe("asc");
    expect(codeIndicator()?.textContent).toContain("▲");
    expect(getRenderedColumnValues(0)).toEqual(["100", "200", "300"]);
    expect(onSortChange).toHaveBeenLastCalledWith("code", "asc");

    // 2. SECOND CLICK -> Descending
    await clickHeader(codeHeader);
    expect(codeHeader.getAttribute("aria-sort")).toBe("descending");
    expect(codeHeader.getAttribute("data-sort-direction")).toBe("desc");
    expect(codeIndicator()?.textContent).toContain("▼");
    expect(getRenderedColumnValues(0)).toEqual(["300", "200", "100"]);
    expect(onSortChange).toHaveBeenLastCalledWith("code", "desc");

    // 3. THIRD CLICK -> Reset to original order
    await clickHeader(codeHeader);
    expect(codeHeader.getAttribute("aria-sort")).toBe("none");
    expect(codeHeader.getAttribute("data-sort-direction")).toBe("none");
    expect(codeIndicator()?.textContent).toContain("↕");
    expect(getRenderedColumnValues(0)).toEqual(["300", "100", "200"]); // Exact initial sequence!
    expect(onSortChange).toHaveBeenLastCalledWith(null, null);

    // 4. FOURTH CLICK -> Cycles back to Ascending
    await clickHeader(codeHeader);
    expect(codeHeader.getAttribute("aria-sort")).toBe("ascending");
    expect(codeIndicator()?.textContent).toContain("▲");
    expect(getRenderedColumnValues(0)).toEqual(["100", "200", "300"]);
  });

  it("resets previous column and sorts new column ascending when switching columns", async () => {
    const onSortChange = vi.fn();
    await renderTable(
      <DataTable
        columns={columns}
        rows={testRows}
        keyField="code"
        onSortChange={onSortChange}
      />
    );

    const ths = getHeaderCells();
    const codeHeader = ths[0];
    const nameHeader = ths[1];

    // Click code -> code is ascending
    await clickHeader(codeHeader);
    expect(codeHeader.getAttribute("aria-sort")).toBe("ascending");
    expect(codeHeader.querySelector("[data-testid='sort-indicator-code']")?.textContent).toContain("▲");
    expect(getRenderedColumnValues(0)).toEqual(["100", "200", "300"]);

    // Click name -> code resets to none, name becomes ascending
    await clickHeader(nameHeader);
    expect(codeHeader.getAttribute("aria-sort")).toBe("none");
    expect(codeHeader.querySelector("[data-testid='sort-indicator-code']")?.textContent).toContain("↕");

    expect(nameHeader.getAttribute("aria-sort")).toBe("ascending");
    expect(nameHeader.querySelector("[data-testid='sort-indicator-name']")?.textContent).toContain("▲");
    expect(getRenderedColumnValues(1)).toEqual([
      "Axis Bluechip",
      "HDFC Top 100",
      "SBI Mutual Fund",
    ]);
    expect(onSortChange).toHaveBeenLastCalledWith("name", "asc");

    // Second click on name -> descending
    await clickHeader(nameHeader);
    expect(nameHeader.getAttribute("aria-sort")).toBe("descending");
    expect(nameHeader.querySelector("[data-testid='sort-indicator-name']")?.textContent).toContain("▼");
    expect(getRenderedColumnValues(1)).toEqual([
      "SBI Mutual Fund",
      "HDFC Top 100",
      "Axis Bluechip",
    ]);
  });

  it("handles keyboard navigation (Enter and Space keys) on column headers", async () => {
    await renderTable(<DataTable columns={columns} rows={testRows} keyField="code" />);

    const codeHeader = getHeaderCells()[0];

    // Enter key triggers asc
    await keyPress(codeHeader, "Enter");
    expect(codeHeader.getAttribute("aria-sort")).toBe("ascending");

    // Space key triggers desc
    await keyPress(codeHeader, " ");
    expect(codeHeader.getAttribute("aria-sort")).toBe("descending");

    // Enter key resets
    await keyPress(codeHeader, "Enter");
    expect(codeHeader.getAttribute("aria-sort")).toBe("none");
  });

  it("does not sort non-sortable columns on click or keypress", async () => {
    await renderTable(<DataTable columns={columns} rows={testRows} keyField="code" />);

    const actionHeader = getHeaderCells()[3];
    await clickHeader(actionHeader);
    expect(getRenderedColumnValues(0)).toEqual(["300", "100", "200"]);

    await keyPress(actionHeader, "Enter");
    expect(getRenderedColumnValues(0)).toEqual(["300", "100", "200"]);
  });

  it("gracefully displays 'No rows to display.' on empty rows without throwing on sort click", async () => {
    await renderTable(<DataTable columns={columns} rows={[]} keyField="code" />);

    expect(container!.textContent).toContain("No rows to display.");

    const codeHeader = getHeaderCells()[0];
    await expect(clickHeader(codeHeader)).resolves.not.toThrow();
    expect(codeHeader.getAttribute("aria-sort")).toBe("ascending");
  });

  it("supports custom renderers while properly ordering the underlying data rows", async () => {
    const customColumns: ColumnConfig[] = [
      {
        key: "score",
        label: "Score",
        format: "number",
        decimals: 0,
        render: (row) => <span data-testid="custom-cell">{`Score is ${row.score}`}</span>,
      },
    ];
    const customRows = [{ score: 50 }, { score: 10 }, { score: 90 }];

    await renderTable(<DataTable columns={customColumns} rows={customRows} keyField="score" />);

    const header = getHeaderCells()[0];
    await clickHeader(header); // asc: 10, 50, 90

    const cells = Array.from(container!.querySelectorAll("[data-testid='custom-cell']")).map(
      (el) => el.textContent
    );
    expect(cells).toEqual(["Score is 10", "Score is 50", "Score is 90"]);
  });

  it("supports controlled sorting props (sortKey and sortDirection)", async () => {
    const onSortChange = vi.fn();
    await renderTable(
      <DataTable
        columns={columns}
        rows={testRows}
        keyField="code"
        sortKey="name"
        sortDirection="desc"
        onSortChange={onSortChange}
      />
    );

    const nameHeader = getHeaderCells()[1];
    expect(nameHeader.getAttribute("aria-sort")).toBe("descending");
    expect(getRenderedColumnValues(1)).toEqual([
      "SBI Mutual Fund",
      "HDFC Top 100",
      "Axis Bluechip",
    ]);

    // Clicking when controlled invokes callback with next state
    await clickHeader(nameHeader);
    expect(onSortChange).toHaveBeenCalledWith(null, null);
  });

  it("retains sort state and properly updates rows when dynamic query filters update the rows array", async () => {
    await renderTable(<DataTable columns={columns} rows={testRows} keyField="code" />);

    const codeHeader = getHeaderCells()[0];
    await clickHeader(codeHeader); // asc: 100, 200, 300
    expect(getRenderedColumnValues(0)).toEqual(["100", "200", "300"]);

    // Dynamic query filters produce an updated rows array
    const updatedRows = [
      { code: 500, name: "Nippon India", ter: 0.5, action: "View" },
      { code: 50, name: "Quant Small", ter: 0.7, action: "View" },
      { code: 250, name: "Mirae Asset", ter: 1.0, action: "View" },
    ];
    await renderTable(<DataTable columns={columns} rows={updatedRows} keyField="code" />);

    // Table should automatically sort new rows by active sort (code asc: 50, 250, 500)
    expect(getRenderedColumnValues(0)).toEqual(["50", "250", "500"]);
  });

  it("handles rapid consecutive clicks across columns without desynchronizing state", async () => {
    await renderTable(<DataTable columns={columns} rows={testRows} keyField="code" />);

    const codeHeader = getHeaderCells()[0];
    const nameHeader = getHeaderCells()[1];

    // Rapid 6-click cycle on code: asc -> desc -> reset -> asc -> desc -> reset
    for (let i = 0; i < 6; i++) {
      await clickHeader(codeHeader);
    }
    expect(codeHeader.getAttribute("aria-sort")).toBe("none");
    expect(getRenderedColumnValues(0)).toEqual(["300", "100", "200"]);

    // Switch to name
    await clickHeader(nameHeader);
    expect(nameHeader.getAttribute("aria-sort")).toBe("ascending");
    expect(getRenderedColumnValues(1)).toEqual([
      "Axis Bluechip",
      "HDFC Top 100",
      "SBI Mutual Fund",
    ]);
  });

  it("prevents default on mousedown with detail > 1 to avoid double-click text selection artifacting", async () => {
    await renderTable(<DataTable columns={columns} rows={testRows} keyField="code" />);
    const codeHeader = getHeaderCells()[0];

    const dblClickMousedown = new MouseEvent("mousedown", {
      bubbles: true,
      cancelable: true,
      detail: 2,
    });
    codeHeader.dispatchEvent(dblClickMousedown);
    expect(dblClickMousedown.defaultPrevented).toBe(true);

    const singleClickMousedown = new MouseEvent("mousedown", {
      bubbles: true,
      cancelable: true,
      detail: 1,
    });
    codeHeader.dispatchEvent(singleClickMousedown);
    expect(singleClickMousedown.defaultPrevented).toBe(false);
  });

  it("formats empty strings, whitespace, and non-finite numbers as '-' rather than '0.00' or '₹0.00'", async () => {
    const emptyRows = [
      { code: "", name: "Test Fund", ter: "   ", inrVal: "", infVal: Infinity },
    ];
    const formatCols: ColumnConfig[] = [
      { key: "code", label: "Code", format: "number", decimals: 2 },
      { key: "ter", label: "TER", format: "signed_pct", decimals: 2 },
      { key: "inrVal", label: "INR", format: "inr" },
      { key: "infVal", label: "Inf", format: "number", decimals: 2 },
    ];
    await renderTable(<DataTable columns={formatCols} rows={emptyRows} keyField="name" />);
    expect(getRenderedColumnValues(0)[0]).toBe("-");
    expect(getRenderedColumnValues(1)[0]).toBe("-");
    expect(getRenderedColumnValues(2)[0]).toBe("-");
    expect(getRenderedColumnValues(3)[0]).toBe("-");
  });

  it("gracefully renders and handles sparse arrays containing null row items without throwing", async () => {
    const sparseRows = [
      { code: 100, name: "Fund A", ter: 1.0, action: "View" },
      null as unknown as Record<string, unknown>,
      { code: 200, name: "Fund B", ter: 2.0, action: "View" },
    ];
    await expect(
      renderTable(<DataTable columns={columns} rows={sparseRows} keyField="code" />)
    ).resolves.not.toThrow();

    const codeHeader = getHeaderCells()[0];
    await expect(clickHeader(codeHeader)).resolves.not.toThrow();
  });
});
