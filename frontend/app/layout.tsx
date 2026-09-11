import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Indian Mutual Funds",
  description: "AMFI-sourced mutual fund analytics dashboard.",
};

// Phase 0 scaffold only: no AppShell/Sidebar yet. Those land in Phase 2 of
// the migration plan (../../.claude/plans/floofy-petting-mountain.md),
// alongside the shared component library and the global date-range store.
export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
