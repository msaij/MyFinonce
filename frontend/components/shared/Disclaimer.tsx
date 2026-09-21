"use client";

import { Banner } from "./Banner";

export function NonAdviceDisclaimer({ compact = false }: { compact?: boolean }) {
  return (
    <Banner level="warning">
      <b>This is not personalized investment advice.</b> MyFinonce is not a SEBI-registered
      Investment Adviser or Research Analyst. Figures are sourced from official AMFI NAVs and
      Regulation 66 TER disclosures. Screener sorts and labels such as &quot;Institutional Alpha
      Star&quot; / &quot;Value Trap / Laggard&quot; are descriptive versus a category median, not a
      recommendation. Consult a SEBI-registered adviser before investing.
      {compact ? null : (
        <span className="mt-1 block text-xs">
          Headline TWR is NAV-only (no exit load, STT, or tax overlay).
        </span>
      )}
    </Banner>
  );
}
