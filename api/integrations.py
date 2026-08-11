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
from urllib.parse import urlencode

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
    installation_id: int | None = Field(default=None, gt=0)
    code: str | None = Field(default=None, min_length=8, max_length=512)


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

    async def verify_user_admin(
        self,
        code: str,
        installation_id: int,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
    ) -> None:
        """Prove the signed-in GitHub user owns/administers the installation."""
        if not client_id or not client_secret or not redirect_uri:
            raise RuntimeError("GitHub OAuth verification is not configured")
        owns_client = self.client is None
        client = self.client or httpx.AsyncClient(timeout=10, follow_redirects=False)
        try:
            token_response = await client.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
            )
            token_response.raise_for_status()
            user_token = token_response.json().get("access_token")
            if not user_token:
                raise RuntimeError("GitHub OAuth did not return a user token")
            user_headers = {
                "Authorization": f"Bearer {user_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            user_response = await client.get(f"{_GITHUB_API}/user", headers=user_headers)
            user_response.raise_for_status()
            user = user_response.json()
            installation_response = await client.get(
                f"{_GITHUB_API}/app/installations/{installation_id}",
                headers={
                    "Authorization": f"Bearer {self._app_token()}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            installation_response.raise_for_status()
            account = installation_response.json().get("account") or {}
            if account.get("type") == "User":
                if account.get("id") != user.get("id"):
                    raise PermissionError("GitHub user does not own this installation")
                return
            if account.get("type") == "Organization" and account.get("login"):
                membership = await client.get(
                    f"{_GITHUB_API}/user/memberships/orgs/{account['login']}",
                    headers=user_headers,
                )
                membership.raise_for_status()
                data = membership.json()
                if data.get("state") == "active" and data.get("role") == "admin":
                    return
            raise PermissionError("GitHub user does not administer this installation")
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
    if (
        not settings.github_app_slug
        or not settings.github_app_state_secret
        or not settings.github_app_client_id
        or not settings.github_app_client_secret
        or not settings.github_app_callback_url
    ):
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
        if not body.code:
            if not body.installation_id or state.get("phase", "install") != "install":
                raise ValueError("GitHub installation callback is incomplete")
            await store.consume_oauth_state(
                principal.subject, "github", _nonce_digest(state["nonce"])
            )
            oauth_state, nonce_digest, expires_at = _sign_state(
                principal.subject,
                settings.github_app_state_secret,
                phase="verify",
                installation_id=body.installation_id,
            )
            await store.issue_oauth_state(
                principal.subject, "github", nonce_digest, expires_at
            )
            query = urlencode({
                "client_id": settings.github_app_client_id,
                "redirect_uri": settings.github_app_callback_url,
                "scope": "read:user read:org",
                "state": oauth_state,
            })
            return {"status": "oauth_required", "url": f"https://github.com/login/oauth/authorize?{query}"}
        if state.get("phase") != "verify" or not isinstance(state.get("installation_id"), int):
            raise ValueError("GitHub OAuth callback is not bound to an installation")
        await store.consume_oauth_state(
            principal.subject, "github", _nonce_digest(state["nonce"])
        )
        client = GitHubAppClient(
            settings.github_app_id, settings.github_app_private_key
        )
        await client.verify_user_admin(
            body.code,
            state["installation_id"],
            client_id=settings.github_app_client_id,
            client_secret=settings.github_app_client_secret,
            redirect_uri=settings.github_app_callback_url,
        )
        verified = await client.verify_installation(state["installation_id"])
        vault = _credential_vault()
        expires_at = (
            datetime.fromisoformat(verified["expires_at"].replace("Z", "+00:00"))
            if verified.get("expires_at") else None
        )
        binding_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{principal.subject}:github:{verified['external_account_id']}"))
        binding = CredentialBinding(principal.subject, "github", binding_id)
        envelope = vault.encrypt(verified["access_token"].encode(), binding)
        connection = await store.upsert_service_connection_with_credential(
            owner_id=principal.subject,
            provider="github",
            external_account_id=verified["external_account_id"],
            allowed_operations=["issues:read", "issues:comment"],
            immutable_targets=[f"repository:{item}" for item in verified["repository_ids"]],
            expires_at=expires_at,
            envelope=envelope,
            connection_id=binding_id,
        )
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


def _sign_state(
    owner_id: str,
    secret: str,
    *,
    phase: str = "install",
    installation_id: int | None = None,
) -> tuple[str, str, datetime]:
    if not secret:
        raise ValueError("OAuth state signing is not configured")
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    payload = {
        "sub": owner_id,
        "nonce": secrets.token_urlsafe(18),
        "exp": int(expires_at.timestamp()),
        "phase": phase,
    }
    if installation_id is not None:
        payload["installation_id"] = installation_id
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
