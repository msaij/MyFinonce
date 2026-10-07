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
  // Proxies /api/* to the FastAPI backend so the browser only ever talks to
  // one origin in dev; the backend's own routes already live under /api/.
  async rewrites() {
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
