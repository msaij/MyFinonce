import type { Metadata } from "next";

import { AppProviders } from "@/lib/providers";
import "./globals.css";

export const metadata: Metadata = {
  title: "Indian Mutual Funds",
  description: "AMFI-sourced mutual fund analytics dashboard.",
};

// AppShell (Sidebar + DateRangePicker) is applied per-page, not here, so each
// page can pass its own pageContext (see fetcher/date_picker.py's
// page_context param) -- see app/page.tsx for the pattern.
export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <AppProviders>{children}</AppProviders>
      </body>
    </html>
  );
}
