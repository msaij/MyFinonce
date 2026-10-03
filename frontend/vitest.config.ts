import path from "path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "."),
    },
  },
  test: {
    environment: "jsdom",
    // The frontend image sets NODE_ENV=production for `next start`, and tests run in that
    // image inherited it: React then loaded its production build, where act() does not
    // exist, and every component test that renders through act() failed.
    env: { NODE_ENV: "test" },
  },
});
