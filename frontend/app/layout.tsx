import type { Metadata } from "next";

import { AppProviders } from "@/lib/providers";
import "./globals.css";

export const metadata: Metadata = {
  title: "myFinonce",
  description: "Indian mutual fund analytics on official AMFI data, NSE IPOs and financial calculators.",
};

// AppShell (Sidebar + controls bar) is applied per-page, not here, so a page outside the
// mutual-fund analytics can drop the fund controls (hideDateRange) -- see app/page.tsx.
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
