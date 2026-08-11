"""Owner-bound action inbox and exact confirmation API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from api.identity import Principal, get_current_principal
from capabilities.broker import ActionBroker
from capabilities.actions import ProposedAction
from capabilities.store import CapabilityStore
from core.state import get_capability_store
from config.settings import settings

router = APIRouter(prefix="/api/actions", tags=["capability-actions"])


class ApprovalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_digest: str = Field(pattern="^sha256:[0-9a-f]{64}$")


def get_action_broker(
    store: CapabilityStore = Depends(get_capability_store),
) -> ActionBroker:
    return ActionBroker(store)


@router.post("/propose", status_code=201)
async def propose_action(
    body: ProposedAction,
    principal: Principal = Depends(get_current_principal),
    broker: ActionBroker = Depends(get_action_broker),
) -> dict:
    if not settings.capability_actions_enabled:
        raise HTTPException(503, "capability actions are disabled by the release kill switch")
    try:
        return _response(await broker.propose(principal.subject, body))
    except PermissionError as exc:
        raise HTTPException(404, "capability, scope, or connection not found") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/pending")
async def list_pending_actions(
    principal: Principal = Depends(get_current_principal),
    broker: ActionBroker = Depends(get_action_broker),
) -> dict:
    return {"items": [_response(item) for item in await broker.pending(principal.subject)]}


@router.get("/{action_id}")
async def get_action(
    action_id: str,
    principal: Principal = Depends(get_current_principal),
    broker: ActionBroker = Depends(get_action_broker),
) -> dict:
    try:
        return _response(await broker.get(principal.subject, action_id))
    except PermissionError as exc:
        raise HTTPException(404, "action not found") from exc


@router.post("/{action_id}/approve")
async def approve_action(
    action_id: str,
    body: ApprovalBody,
    principal: Principal = Depends(get_current_principal),
    broker: ActionBroker = Depends(get_action_broker),
) -> dict:
    if not settings.capability_actions_enabled:
        raise HTTPException(503, "capability actions are disabled by the release kill switch")
    try:
        return _response(
            await broker.approve(principal.subject, action_id, body.request_digest)
        )
    except PermissionError as exc:
        raise HTTPException(404, "action not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/{action_id}/deny")
async def deny_action(
    action_id: str,
    principal: Principal = Depends(get_current_principal),
    broker: ActionBroker = Depends(get_action_broker),
) -> dict:
    try:
        return _response(await broker.deny(principal.subject, action_id))
    except PermissionError as exc:
        raise HTTPException(404, "action not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


def _response(action) -> dict:
    return {
        "id": action.id,
        "status": action.status.value,
        "operation": action.operation,
        "arguments": action.arguments,
        "shared_data": action.shared_data,
        "mutable_resources": action.mutable_resources,
        "request_digest": action.request_digest,
        "expires_at": action.expires_at.isoformat(),
        "result": action.result,
        "error": action.error,
    }
