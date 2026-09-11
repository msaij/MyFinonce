"""FastAPI backend entrypoint.

Phase 0 (scaffolding) only: a health check, nothing else. No DB access, no
routers, no startup event yet -- those land in Phase 1 per the migration
plan (../../.claude/plans/floofy-petting-mountain.md), once the read-only
vs. read-write question against the shared DuckDB file has been verified
empirically rather than assumed.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings

app = FastAPI(title="Indian Mutual Funds API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    # Next.js dev server + same-origin container-to-container calls during migration.
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "enable_sync_daemon": settings.enable_sync_daemon,
    }
