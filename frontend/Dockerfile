# syntax=docker/dockerfile:1
#
# Five stages. `check` and `build` both start from `deps` and BuildKit runs them in
# parallel (with `prod-deps` alongside); the final `runner` copies a marker out of `check`,
# so an image can only be produced when the type-check, lint and unit tests pass --
# `docker compose up -d --build frontend` is the whole release step, and nothing
# unchecked can be shipped by forgetting to run the tests first.
#
# What each stage keeps between builds (BuildKit cache mounts, not image layers):
#   deps, prod-deps  npm's download cache          -> a lockfile change re-fetches only what changed
#   check            tsc's .tsbuildinfo + ESLint's  -> only changed files are re-checked
#   build            .next/cache (webpack/SWC)     -> only changed modules are recompiled
# and the runner's node_modules layer is reused untouched until the lockfile changes, so a
# code change exports only the new .next. Measured before this layout: 73s for any
# one-line change -- 28s compiling from cold, 21s re-running the type-check and lint the
# test step had just run, 8s of build tracing, 6.5s exporting a 1.14GB image.

FROM node:20-alpine AS deps
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
# .npmrc MUST be copied before the install -- it carries the fetch-timeout/fetch-retries
# tuning this environment needs: some connections to the registry's CDN establish and then
# go silently dead (0 bytes, no RST/FIN), so a short timeout that gives up and retries fast
# matters more than a long one. `npm ci` installs exactly what package-lock.json pins (and
# fails if it disagrees with package.json), so two builds of the same commit get the same
# dependencies; `npm install` could quietly resolve newer versions within the ^ ranges.
COPY .npmrc package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci

# What the server needs at runtime: no compiler, linter or test runner.
FROM node:20-alpine AS prod-deps
WORKDIR /app
COPY .npmrc package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --omit=dev

FROM deps AS check
COPY . .
# NODE_ENV stays unset here: React's production build has no act(), which every component
# test renders through (vitest.config.ts pins NODE_ENV=test as well).
RUN --mount=type=cache,target=/app/.next/cache,id=mf-frontend-check \
    npx tsc --noEmit --tsBuildInfoFile .next/cache/tsconfig.tsbuildinfo \
    && npx next lint \
    && npx vitest run \
    && touch /tmp/checks-passed

FROM deps AS build
COPY . .
ENV NODE_ENV=production
# BACKEND_URL must be a build-time ENV, not a docker-compose.yml runtime var: `next build`
# calls next.config.js's rewrites() once to write .next/routes-manifest.json, and the
# server replays that manifest -- a value set only at container start is invisible to the
# build and falls back to next.config.js's localhost default, which inside this container
# is the container itself. (Every /api/* call 500'd the one time this was runtime-only.)
ENV BACKEND_URL=http://backend:8000
# Read IN THE BROWSER (NEXT_PUBLIC_* is inlined into the client bundle here), so it is the
# host-mapped port, not the Docker-internal name. Used only by the admin Activity Log's SSE
# stream, to bypass the Next server's gzip of proxied /api/* responses -- gzip buffers
# small events until it has enough data, so EventSource through :3000 received nothing.
# The backend's CORS already allows http://localhost:3000.
ENV NEXT_PUBLIC_BACKEND_ORIGIN=http://localhost:8000
# The `check` stage has already type-checked and linted this exact source.
ENV NEXT_SKIP_CHECKS=1
RUN --mount=type=cache,target=/app/.next/cache,id=mf-frontend-build npm run build

# A production server (`next start`), not `next dev`: dev's per-request compilation
# measured ~1.1s for an API call that takes 25-50ms through a production server.
# node_modules comes first so its layer is reused as-is until the lockfile changes.
FROM node:20-alpine AS runner
# Lets scripts/dev.ps1 prune this project's superseded images without touching any others.
LABEL org.myfinonce.app=frontend
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=prod-deps /app/node_modules ./node_modules
COPY package.json next.config.js ./
COPY --from=check /tmp/checks-passed /tmp/checks-passed
COPY --from=build --chown=node:node /app/.next ./.next
USER node
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --start-interval=1s --retries=3 \
    CMD wget -qO /dev/null http://127.0.0.1:3000/ || exit 1
CMD ["node", "node_modules/next/dist/bin/next", "start", "-p", "3000"]
