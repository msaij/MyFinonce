import type { Config } from "tailwindcss";

// Design tokens -- see globals.css for the actual :root variable definitions
// these reference. Always light: dark mode was removed (2026-09-12, user
// request) by deleting globals.css's `@media (prefers-color-scheme: dark)`
// override block entirely, so `darkMode` here has no block left to activate
// -- set to "media" it would just never match anything; "class" is the
// honest way to say "no automatic dark variant" (nothing anywhere applies a
// `dark` class either, so this is inert either way, but this reads correctly
// rather than implying an OS-driven dark mode that no longer exists).
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        success: "var(--mf-success)",
        "success-bg": "var(--mf-success-bg)",
        danger: "var(--mf-danger)",
        "danger-bg": "var(--mf-danger-bg)",
        warning: "var(--mf-warning)",
        "warning-bg": "var(--mf-warning-bg)",
        accent: "var(--mf-accent)",
        "accent-bg": "var(--mf-accent-bg)",
        muted: "var(--mf-muted)",
        border: "var(--mf-border)",
        card: "var(--mf-card-bg)",
      },
    },
  },
  plugins: [],
};

export default config;
