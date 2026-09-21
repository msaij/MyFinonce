# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
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

EXPOSE 8000

# --workers 1 is deliberate: in-process caches, WRITE_LOCK, BACKFILL_STATE, and
# the AMFI schedule thread are process-local. A future app.worker extraction
# would be required before raising --workers. Do not change in this program.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
