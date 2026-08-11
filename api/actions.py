"""Owner-bound action inbox API; execution is introduced by U5."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.identity import Principal, get_current_principal

router = APIRouter(prefix="/api/actions", tags=["capability-actions"])


@router.get("/pending")
async def list_pending_actions(
    principal: Principal = Depends(get_current_principal),
) -> dict:
    return {"owner": principal.subject, "items": []}
