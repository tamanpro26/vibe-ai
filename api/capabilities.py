"""Authenticated Capability Hub registry and scope APIs."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from api.identity import Principal, get_current_principal
from capabilities.manifests import ActivationMode, CapabilityManifest, validate_package
from capabilities.models import ScopeKind, ScopeState
from capabilities.registry import CapabilityRegistry
from capabilities.store import CapabilityStore
from core.state import get_capability_store as _get_capability_store

router = APIRouter(prefix="/api/capabilities", tags=["capabilities"])
_SCOPE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def get_capability_store() -> CapabilityStore:
    return _get_capability_store()


class DraftBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manifest: dict[str, Any]


class EditBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patch: dict[str, Any]


class ScopeRegistrationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parent_scope_id: str | None = Field(default=None, max_length=128)


class ScopeOverrideBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: ScopeState
    configuration_patch: dict[str, Any] = Field(default_factory=dict)


class ActivationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: ActivationMode
    onboarding_accepted: bool = False


@router.post("/validate")
async def validate_manifest(
    body: DraftBody,
    principal: Principal = Depends(get_current_principal),
) -> dict[str, Any]:
    del principal
    manifest = validate_package(body.manifest).manifest
    return _manifest_response(manifest)


@router.get("")
async def list_capabilities(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10_000),
    principal: Principal = Depends(get_current_principal),
    store: CapabilityStore = Depends(get_capability_store),
) -> dict[str, Any]:
    versions = await store.list_catalog(principal.subject, limit=limit, offset=offset)
    return {"items": [_version_response(item) for item in versions], "limit": limit, "offset": offset}


@router.post("/drafts", status_code=status.HTTP_201_CREATED)
async def create_draft(
    body: DraftBody,
    principal: Principal = Depends(get_current_principal),
    store: CapabilityStore = Depends(get_capability_store),
) -> dict[str, Any]:
    try:
        draft = await CapabilityRegistry(store).create_draft(principal.subject, body.manifest)
        return {"id": draft.id, "state": draft.state.value, "manifest": draft.manifest}
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc


@router.post("/drafts/{draft_id}/publish", status_code=status.HTTP_201_CREATED)
async def publish_draft(
    draft_id: str,
    principal: Principal = Depends(get_current_principal),
    store: CapabilityStore = Depends(get_capability_store),
) -> dict[str, Any]:
    try:
        version = await CapabilityRegistry(store).publish(principal.subject, draft_id)
        return _version_response(version)
    except PermissionError as exc:
        raise HTTPException(404, detail="draft not found") from exc


@router.delete("/drafts/{draft_id}")
async def delete_draft(
    draft_id: str,
    principal: Principal = Depends(get_current_principal),
    store: CapabilityStore = Depends(get_capability_store),
) -> dict[str, bool]:
    try:
        await store.delete_draft(principal.subject, draft_id)
        return {"deleted": True}
    except PermissionError as exc:
        raise HTTPException(404, detail="draft not found") from exc
    except ValueError as exc:
        raise HTTPException(409, detail=str(exc)) from exc


@router.post("/versions/{version_id}/edit", status_code=status.HTTP_201_CREATED)
async def edit_version(
    version_id: str,
    body: EditBody,
    principal: Principal = Depends(get_current_principal),
    store: CapabilityStore = Depends(get_capability_store),
) -> dict[str, Any]:
    try:
        draft = await CapabilityRegistry(store).edit_as_new_draft(
            principal.subject, version_id, body.patch
        )
        return {"id": draft.id, "state": draft.state.value, "manifest": draft.manifest}
    except PermissionError as exc:
        raise HTTPException(404, detail="capability version not found") from exc


@router.get("/versions/{version_id}")
async def get_version(
    version_id: str,
    principal: Principal = Depends(get_current_principal),
    store: CapabilityStore = Depends(get_capability_store),
) -> dict[str, Any]:
    version = await store.get_version(version_id)
    if version is None or version.owner_id not in {principal.subject, "vibeai"}:
        raise HTTPException(404, detail="capability version not found")
    return _version_response(version)


@router.post("/versions/{version_id}/archive")
async def archive_version(
    version_id: str,
    principal: Principal = Depends(get_current_principal),
    store: CapabilityStore = Depends(get_capability_store),
) -> dict[str, bool]:
    try:
        await CapabilityRegistry(store).archive(principal.subject, version_id)
        return {"archived": True}
    except PermissionError as exc:
        raise HTTPException(404, detail="capability version not found") from exc


@router.post("/versions/{version_id}/install", status_code=status.HTTP_201_CREATED)
async def install_version(
    version_id: str,
    principal: Principal = Depends(get_current_principal),
    store: CapabilityStore = Depends(get_capability_store),
) -> dict[str, Any]:
    try:
        installation = await store.install(principal.subject, version_id)
        return {
            "id": installation.id,
            "capability_version_id": installation.capability_version_id,
            "installed_at": installation.installed_at.isoformat(),
        }
    except (LookupError, PermissionError, ValueError) as exc:
        raise HTTPException(404, detail="installable capability version not found") from exc


@router.post("/scopes/{scope_kind}/{scope_id}", status_code=status.HTTP_201_CREATED)
async def register_scope(
    scope_kind: ScopeKind,
    scope_id: str,
    body: ScopeRegistrationBody | None = None,
    principal: Principal = Depends(get_current_principal),
    store: CapabilityStore = Depends(get_capability_store),
) -> dict[str, Any]:
    if scope_kind is ScopeKind.ACCOUNT or not _SCOPE_ID.fullmatch(scope_id):
        raise HTTPException(422, detail="invalid project or chat scope")
    parent = body.parent_scope_id if body else None
    if parent is not None and not _SCOPE_ID.fullmatch(parent):
        raise HTTPException(422, detail="invalid parent scope")
    try:
        record = await store.register_owned_scope(principal.subject, scope_kind, scope_id, parent)
        return {"scope_kind": record.scope_kind.value, "scope_id": record.scope_id}
    except PermissionError as exc:
        raise HTTPException(404, detail="parent scope not found") from exc


@router.delete("/scopes/{scope_kind}/{scope_id}")
async def delete_scope(
    scope_kind: ScopeKind,
    scope_id: str,
    principal: Principal = Depends(get_current_principal),
    store: CapabilityStore = Depends(get_capability_store),
) -> dict[str, bool]:
    if scope_kind is ScopeKind.ACCOUNT:
        raise HTTPException(422, detail="account scope cannot be deleted")
    try:
        await store.delete_owned_scope(principal.subject, scope_kind, scope_id)
        await store.append_audit(
            principal.subject,
            "scope.deleted",
            scope_id,
            {"scope_kind": scope_kind.value, "scope_id": scope_id},
        )
        return {"deleted": True}
    except PermissionError as exc:
        raise HTTPException(404, detail="scope not found") from exc


@router.patch("/installations/{installation_id}/scopes/{scope_kind}/{scope_id}")
async def set_scope_override(
    installation_id: str,
    scope_kind: ScopeKind,
    scope_id: str,
    body: ScopeOverrideBody,
    principal: Principal = Depends(get_current_principal),
    store: CapabilityStore = Depends(get_capability_store),
) -> dict[str, Any]:
    if not await store.owns_scope(principal.subject, scope_kind, scope_id):
        raise HTTPException(404, detail="scope not found for owner")
    try:
        record = await store.set_scope_override(
            principal.subject,
            installation_id,
            scope_kind,
            scope_id,
            body.state,
            body.configuration_patch,
        )
        return {
            "id": record.id,
            "state": record.state.value,
            "configuration_patch": record.configuration_patch,
        }
    except PermissionError as exc:
        raise HTTPException(404, detail="installation not found") from exc


@router.patch("/activation")
async def set_activation(
    body: ActivationBody,
    principal: Principal = Depends(get_current_principal),
    store: CapabilityStore = Depends(get_capability_store),
) -> dict[str, str]:
    preference = await store.set_activation_mode(
        principal.subject, body.mode, onboarding_accepted=body.onboarding_accepted
    )
    return {"mode": preference.mode.value}


def _manifest_response(manifest: CapabilityManifest) -> dict[str, Any]:
    return {**manifest.model_dump(mode="json"), "content_digest": manifest.content_digest}


def _version_response(version) -> dict[str, Any]:
    return {
        "id": version.id,
        "owner": "vibeai" if version.owner_id == "vibeai" else "current_user",
        "content_digest": version.content_digest,
        "review_state": version.review_state.value,
        "archived": version.archived_at is not None,
        "manifest": version.manifest,
    }
