"""Owner-bound GitHub App connection lifecycle."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from api.capabilities import get_capability_store
from api.identity import Principal, get_current_principal
from capabilities.credentials import CredentialBinding, CredentialVault
from capabilities.store import CapabilityStore
from config.settings import settings

router = APIRouter(prefix="/api/integrations", tags=["capability-integrations"])
_GITHUB_API = "https://api.github.com"


class GitHubCallbackBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: str = Field(min_length=20, max_length=4096)
    installation_id: int = Field(gt=0)


class GitHubAppClient:
    def __init__(self, app_id: str, private_key: str, client: httpx.AsyncClient | None = None) -> None:
        self.app_id = app_id
        self.private_key = private_key.replace("\\n", "\n")
        self.client = client

    async def verify_installation(self, installation_id: int) -> dict[str, Any]:
        if not self.app_id or not self.private_key:
            raise RuntimeError("GitHub App credentials are not configured")
        owns_client = self.client is None
        client = self.client or httpx.AsyncClient(timeout=10, follow_redirects=False)
        app_headers = {
            "Authorization": f"Bearer {self._app_token()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            installation = await client.get(
                f"{_GITHUB_API}/app/installations/{installation_id}", headers=app_headers
            )
            installation.raise_for_status()
            installation_data = installation.json()
            token_response = await client.post(
                f"{_GITHUB_API}/app/installations/{installation_id}/access_tokens",
                headers=app_headers,
                json={"permissions": {"issues": "write", "metadata": "read"}},
            )
            token_response.raise_for_status()
            token_data = token_response.json()
            installation_headers = {
                "Authorization": f"Bearer {token_data['token']}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            repositories = await client.get(
                f"{_GITHUB_API}/installation/repositories?per_page=100",
                headers=installation_headers,
            )
            repositories.raise_for_status()
            repository_ids = [
                str(item["id"])
                for item in repositories.json().get("repositories", [])
                if isinstance(item.get("id"), int)
            ]
            account = installation_data.get("account") or {}
            if not repository_ids or not isinstance(account.get("id"), int):
                raise RuntimeError("GitHub installation has no verified repositories")
            return {
                "external_account_id": f"{account['id']}:{installation_id}",
                "access_token": token_data["token"],
                "expires_at": token_data.get("expires_at"),
                "repository_ids": repository_ids,
            }
        finally:
            if owns_client:
                await client.aclose()

    def _app_token(self) -> str:
        now = int(time.time())
        return jwt.encode(
            {"iat": now - 30, "exp": now + 8 * 60, "iss": self.app_id},
            self.private_key,
            algorithm="RS256",
        )


@router.get("")
async def list_integrations(
    principal: Principal = Depends(get_current_principal),
    store: CapabilityStore = Depends(get_capability_store),
) -> dict:
    values = await store.list_service_connections(principal.subject)
    return {
        "items": [
            {
                "id": item.id,
                "provider": item.provider,
                "external_account_id": item.external_account_id,
                "allowed_operations": item.allowed_operations,
                "immutable_targets": item.immutable_targets,
                "expires_at": item.expires_at.isoformat() if item.expires_at else None,
                "revoked": item.revoked_at is not None,
            }
            for item in values
        ]
    }


@router.post("/github/connect")
async def start_github_connection(
    principal: Principal = Depends(get_current_principal),
    store: CapabilityStore = Depends(get_capability_store),
) -> dict[str, str]:
    if not settings.github_app_slug or not settings.github_app_state_secret:
        raise HTTPException(503, "GitHub App connection is not configured")
    state, nonce_digest, expires_at = _sign_state(
        principal.subject, settings.github_app_state_secret
    )
    await store.issue_oauth_state(
        principal.subject, "github", nonce_digest, expires_at
    )
    return {
        "url": f"https://github.com/apps/{settings.github_app_slug}/installations/new?state={state}",
        "state": state,
    }


@router.post("/github/callback", status_code=201)
async def finish_github_connection(
    body: GitHubCallbackBody,
    principal: Principal = Depends(get_current_principal),
    store: CapabilityStore = Depends(get_capability_store),
) -> dict[str, Any]:
    try:
        state = _verify_state(body.state, settings.github_app_state_secret)
        if state["sub"] != principal.subject:
            raise ValueError("OAuth state owner mismatch")
        await store.consume_oauth_state(
            principal.subject, "github", _nonce_digest(state["nonce"])
        )
        verified = await GitHubAppClient(
            settings.github_app_id, settings.github_app_private_key
        ).verify_installation(body.installation_id)
        vault = _credential_vault()
        connection_id = str(uuid.uuid4())
        connection = await store.create_service_connection(
            owner_id=principal.subject,
            provider="github",
            external_account_id=verified["external_account_id"],
            credential_reference=f"credential:{connection_id}",
            allowed_operations=["issues:read", "issues:comment"],
            immutable_targets=[f"repository:{item}" for item in verified["repository_ids"]],
            connection_id=connection_id,
        )
        binding = CredentialBinding(principal.subject, "github", connection.id)
        envelope = vault.encrypt(verified["access_token"].encode(), binding)
        await store.put_credential(principal.subject, "github", connection.id, envelope)
        await store.append_audit(
            principal.subject,
            "integration.connected",
            connection.id,
            {
                "provider": "github",
                "external_account_id": verified["external_account_id"],
                "repository_count": len(verified["repository_ids"]),
            },
        )
        return {"id": connection.id, "provider": "github", "status": "connected"}
    except (ValueError, PermissionError) as exc:
        raise HTTPException(400, "Invalid, expired, or reused GitHub connection state") from exc
    except (httpx.HTTPError, RuntimeError, KeyError) as exc:
        raise HTTPException(502, "GitHub installation could not be verified") from exc


@router.post("/{connection_id}/revoke")
async def revoke_integration(
    connection_id: str,
    principal: Principal = Depends(get_current_principal),
    store: CapabilityStore = Depends(get_capability_store),
) -> dict[str, bool]:
    try:
        await store.revoke_service_connection(principal.subject, connection_id)
        try:
            await store.revoke_credential(principal.subject, connection_id)
        except PermissionError:
            pass
        await store.append_audit(
            principal.subject,
            "integration.revoked",
            connection_id,
            {"connection_id": connection_id},
        )
        return {"revoked": True}
    except PermissionError as exc:
        raise HTTPException(404, "service connection not found") from exc


def _sign_state(owner_id: str, secret: str) -> tuple[str, str, datetime]:
    if not secret:
        raise ValueError("OAuth state signing is not configured")
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    payload = {"sub": owner_id, "nonce": secrets.token_urlsafe(18), "exp": int(expires_at.timestamp())}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(secret.encode(), raw, hashlib.sha256).digest()
    state = f"{_b64(raw)}.{_b64(signature)}"
    return state, _nonce_digest(payload["nonce"]), expires_at


def _verify_state(state: str, secret: str) -> dict[str, Any]:
    if not secret:
        raise ValueError("OAuth state signing is not configured")
    try:
        raw_part, signature_part = state.split(".", 1)
        raw = _unb64(raw_part)
        signature = _unb64(signature_part)
        expected = hmac.new(secret.encode(), raw, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("invalid OAuth state signature")
        payload = json.loads(raw)
        if int(payload["exp"]) < int(time.time()) or not payload.get("sub") or not payload.get("nonce"):
            raise ValueError("expired OAuth state")
        return payload
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid OAuth state") from exc


def _credential_vault() -> CredentialVault:
    try:
        keys = json.loads(settings.capability_credential_keys)
        return CredentialVault(
            keys, settings.capability_credential_active_key, settings.deployment_environment
        )
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise RuntimeError("credential encryption is not configured") from exc


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _nonce_digest(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode()).hexdigest()}"
