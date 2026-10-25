/** @type {import('next').NextConfig} */

// The Docker image type-checks and lints in its own `check` stage, in parallel with the
// build, and cannot be produced unless that stage passes (see Dockerfile) -- so the build
// stage sets NEXT_SKIP_CHECKS=1 rather than doing the same 20 seconds of work twice. A
// plain `npm run build` anywhere else still checks.
const skipChecks = process.env.NEXT_SKIP_CHECKS === "1";

const nextConfig = {
  reactStrictMode: true,
  // Build traces list each server file's node_modules dependencies for serverless hosts
  // and `output: "standalone"`. This app runs `next start` on a full production
  // node_modules (see Dockerfile), so tracing was pure cost: 8s of every build, and 20s
  // when standalone was tried instead. (Next 15 removes this option; drop it there.)
  outputFileTracing: false,
  typescript: { ignoreBuildErrors: skipChecks },
  eslint: { ignoreDuringBuilds: skipChecks },
  // No page of this app may be framed by another site. The API refuses cross-site
  // requests, but a page framed on someone else's site makes same-origin calls, so a
  // disguised click on "Delete goal" or "Generate SIPs" would otherwise go through.
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy", value: "frame-ancestors 'none'" },
          { key: "X-Frame-Options", value: "DENY" },
        ],
      },
    ];
  },
  // Proxies /api/* to the FastAPI backend so the browser only ever talks to
  // one origin in dev; the backend's own routes already live under /api/.
  async rewrites() {
    // Only `next build` and `next dev` call this: `next start` replays the
    // routes-manifest the build wrote, and `next lint` never asks. So a production
    // build without BACKEND_URL fails here, instead of baking in localhost:8000 --
    // which inside the container is the container itself, so every /api/* call 500s.
    if (!process.env.BACKEND_URL && process.env.NODE_ENV === "production") {
      throw new Error("BACKEND_URL is not set. A production build needs it, e.g. http://backend:8000 (frontend/Dockerfile sets it).");
    }
    const backendUrl = process.env.BACKEND_URL || "http://localhost:8000";
    return [
      {
        source: "/api/:path*",
        destination: `${backendUrl}/api/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
