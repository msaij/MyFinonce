"""Admin write authentication. Fail closed unless ADMIN_TOKEN_OPTIONAL."""

from __future__ import annotations

import logging

from fastapi import HTTPException, Request

from app.core.config import settings

logger = logging.getLogger("admin_auth")


def require_admin_token(request: Request) -> None:
    if settings.admin_token_optional:
        return
    if not settings.admin_token:
        logger.warning("Admin write rejected: ADMIN_TOKEN not configured")
        raise HTTPException(status_code=401, detail="ADMIN_TOKEN not configured")
    provided = request.headers.get("X-Admin-Token") or ""
    if provided != settings.admin_token:
        logger.warning("Admin write rejected: invalid or missing X-Admin-Token")
        raise HTTPException(status_code=401, detail="Unauthorized")
