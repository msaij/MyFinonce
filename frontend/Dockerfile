# syntax=docker/dockerfile:1
FROM node:20-alpine

WORKDIR /app
# .npmrc MUST be copied before package.json/npm install -- it carries the
# fetch-timeout/fetch-retries tuning this environment needs (see below). It
# was previously only copied via `COPY . .` AFTER npm install, silently
# making every timeout/retry setting in it a no-op for the actual install.
COPY .npmrc package.json ./
# Cache mount persists npm's download cache across build attempts (keyed by
# BuildKit, independent of image layers) -- added after this environment hit
# repeated transient registry-connection stalls during Phase 2's dependency
# install: some connections to the registry's CDN establish successfully but
# then go silently dead (0 bytes, no RST/FIN) instead of erroring, so a short
# fetch-timeout that gives up and retries fast matters more here than a long
# one. With the cache mount, a retry only re-fetches what didn't complete.
RUN --mount=type=cache,target=/root/.npm npm install
COPY . .

# Production build, not the dev server (was `CMD ["npm", "run", "dev"]` for the
# whole active-migration-development period -- fine while pages were being
# built and iterated on many times an hour, but next dev's per-request
# on-demand compilation/Fast-Refresh instrumentation adds massive latency to
# every request once the app is actually being used: measured ~1.1s for an API
# call proxied through `next dev`'s rewrite handling that takes ~25-50ms
# against the backend directly, or through a production `next start`).
# docker-compose.yml no longer bind-mounts source over this image -- a
# production container runs what was baked in at build time; rebuild the
# image after a code change instead of relying on live reload.
ENV NODE_ENV=production
# BACKEND_URL must be a build-time ENV, not just a docker-compose.yml runtime
# environment var: `next build` calls next.config.js's rewrites() once to
# generate .next/routes-manifest.json, and `next start` replays that
# manifest rather than re-invoking rewrites() -- so a value only set at
# container-start time (docker-compose.yml's `environment:`) is invisible to
# the build and silently falls back to next.config.js's localhost default,
# which inside THIS container resolves to itself, not the backend service.
# (Confirmed the hard way: every /api/* call 500'd after switching off next
# dev, which evaluates rewrites() live and so never exposed this.) The
# Compose network's service name is stable for this project's one deployment
# shape, so it's hardcoded here rather than threaded through as a build ARG.
ENV BACKEND_URL=http://backend:8000
# Separate from BACKEND_URL above, and for a different reason: this one is read
# IN THE BROWSER (any NEXT_PUBLIC_* var gets inlined into the client JS bundle
# at this build step), which cannot resolve the Docker-internal hostname
# `backend` at all -- it needs the same host-mapped port the browser's own
# machine reaches this stack on (docker-compose.yml maps backend's 8000
# straight through). Used only for the admin Activity Log's SSE connection,
# to bypass `next start`'s own gzip compression of the proxied /api/* route --
# gzip fundamentally breaks Server-Sent Events (each small event needs to
# flush immediately; a compressor buffers until it has enough data, which for
# a slow-trickle log stream may never come, so the browser's EventSource
# receives nothing despite the backend sending real bytes -- confirmed
# directly: curl against either origin gets every event immediately, but a
# real browser EventSource against the proxied :3000 route got zero messages
# with content-encoding: gzip on the response). The backend's CORSMiddleware
# already allows http://localhost:3000 explicitly, so a direct cross-origin
# connection from the browser works with no backend change needed.
ENV NEXT_PUBLIC_BACKEND_ORIGIN=http://localhost:8000
RUN npm run build

EXPOSE 3000
CMD ["npm", "run", "start"]
