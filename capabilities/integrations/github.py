"""Narrow GitHub adapter: append a comment to one immutable issue target."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import httpx
import jwt

from capabilities.credentials import CredentialBinding
from config.settings import settings

GITHUB_API = "https://api.github.com"
GITHUB_API_VERSION = "2026-03-10"


class GitHubIssueAdapter:
    def __init__(self, store, vault, client: httpx.AsyncClient) -> None:
        self.store = store
        self.vault = vault
        self.client = client

    async def _headers(self, owner_id: str, connection) -> dict[str, str]:
        expires_at = connection.expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at is not None and expires_at <= datetime.now(timezone.utc) + timedelta(minutes=1):
            await self._refresh(owner_id, connection)
        envelope = await self.store.get_credential(owner_id, "github", connection.id)
        token = self.vault.decrypt(
            envelope, CredentialBinding(owner_id, "github", connection.id)
        ).decode("utf-8")
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        }

    async def _refresh(self, owner_id: str, connection) -> None:
        if not settings.github_app_id or not settings.github_app_private_key:
            raise RuntimeError("GitHub App token refresh is not configured")
        try:
            installation_id = int(connection.external_account_id.rsplit(":", 1)[1])
        except (IndexError, ValueError) as exc:
            raise ValueError("invalid GitHub installation identity") from exc
        now = int(time.time())
        app_token = jwt.encode(
            {"iat": now - 30, "exp": now + 8 * 60, "iss": settings.github_app_id},
            settings.github_app_private_key.replace("\\n", "\n"),
            algorithm="RS256",
        )
        response = await self.client.post(
            f"{GITHUB_API}/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {app_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={"permissions": {"issues": "write", "metadata": "read"}},
        )
        response.raise_for_status()
        data = response.json()
        token = data.get("token")
        raw_expiry = data.get("expires_at")
        if not token or not raw_expiry:
            raise RuntimeError("GitHub App refresh returned an incomplete credential")
        expiry = datetime.fromisoformat(raw_expiry.replace("Z", "+00:00"))
        envelope = self.vault.encrypt(
            token.encode(), CredentialBinding(owner_id, "github", connection.id)
        )
        await self.store.put_credential(owner_id, "github", connection.id, envelope)
        await self.store.update_service_connection_expiry(owner_id, connection.id, expiry)
        connection.expires_at = expiry

    async def preflight(self, action, connection) -> dict:
        headers = await self._headers(connection.owner_id, connection)
        args = action.arguments.model_dump() if hasattr(action.arguments, "model_dump") else action.arguments
        response = await self.client.get(
            f"{GITHUB_API}/repositories/{args['repository_id']}/issues/{args['issue_number']}",
            headers=headers,
        )
        response.raise_for_status()
        value = response.json()
        if value.get("state") != "open":
            raise ValueError("target issue is not open")
        return {"issue_id": str(value.get("id")), "state": "open"}

    async def execute(self, action, connection) -> dict:
        headers = await self._headers(connection.owner_id, connection)
        args = action.arguments.model_dump() if hasattr(action.arguments, "model_dump") else action.arguments
        response = await self.client.post(
            f"{GITHUB_API}/repositories/{args['repository_id']}/issues/{args['issue_number']}/comments",
            headers=headers,
            json={"body": args["body"]},
        )
        response.raise_for_status()
        value = response.json()
        return {"comment_id": str(value["id"]), "url": value.get("html_url", "")}
