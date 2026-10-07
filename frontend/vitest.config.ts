import path from "path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "."),
    },
  },
  test: {
    // A browser DOM only where something renders or persists to localStorage: booting
    // jsdom for every file was most of the suite's time (51s of environment setup across
    // 20 files) and the pure-logic tests in lib/ never touch it.
    environment: "node",
    environmentMatchGlobs: [
      ["components/**", "jsdom"],
      ["lib/stores/**", "jsdom"],
    ],
    // The frontend image sets NODE_ENV=production for `next start`, and tests run in that
    // image inherited it: React then loaded its production build, where act() does not
    // exist, and every component test that renders through act() failed.
    env: { NODE_ENV: "test" },
  },
});
