import type { Config } from "tailwindcss";

// Design tokens mirror fetcher/theme.py's CSS custom properties 1:1 so the
// two UIs stay visually consistent during the migration -- see globals.css
// for the actual :root/dark variable definitions these reference.
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  darkMode: "media",
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
