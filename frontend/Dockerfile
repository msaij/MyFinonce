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

EXPOSE 3000

# Dev server, not a production build -- matches this project's existing
# philosophy (the Streamlit container is also just "run the app directly").
# docker-compose.yml bind-mounts ./frontend:/app over this for live reload
# during development, with an anonymous volume over /app/node_modules so the
# host's (nonexistent) node_modules doesn't shadow what npm install baked in.
CMD ["npm", "run", "dev"]
