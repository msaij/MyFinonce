# syntax=docker/dockerfile:1
FROM python:3.11-slim
# Lets scripts/dev.ps1 prune this project's superseded images without touching any others.
LABEL org.myfinonce.app=backend

# Compiled bytecode goes to /tmp/pycache inside the container, not into __pycache__ folders
# in the bind-mounted source on the Windows host. It survives `docker restart`, so a restart
# no longer recompiles every module from source read through the bind mount (the old
# PYTHONDONTWRITEBYTECODE=1 kept the repo clean by caching nothing at all).
ENV PYTHONPYCACHEPREFIX=/tmp/pycache \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Kolkata

RUN apt-get update && apt-get install -y --no-install-recommends tzdata curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
# Cache mount + a short --timeout/--retries: this environment's registry connections
# sometimes establish then go silently dead instead of erroring (same issue hit and
# fixed for npm in frontend/Dockerfile) -- fail each stalled request fast and retry
# rather than hang on pip's much longer default timeout, and keep the cache mount so
# a retry doesn't re-download packages it already got.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --timeout 20 --retries 10 -r requirements.txt
COPY . .

# The image only supplies Python and the dependencies: docker-compose.yml bind-mounts
# ./backend over /app, so a code change needs `scripts\dev.ps1 backend` (a restart), not a
# rebuild. Rebuild (`scripts\dev.ps1 backend-image`) only when requirements.txt changes.
EXPOSE 8000

# --workers 1 is deliberate: in-process caches, WRITE_LOCK, BACKFILL_STATE, and
# the AMFI schedule thread are process-local. A future app.worker extraction
# would be required before raising --workers. Do not change in this program.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
