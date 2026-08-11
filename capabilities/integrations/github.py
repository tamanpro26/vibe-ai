"""Narrow GitHub adapter: append a comment to one immutable issue target."""

from __future__ import annotations

import httpx

from capabilities.credentials import CredentialBinding

GITHUB_API = "https://api.github.com"
GITHUB_API_VERSION = "2026-03-10"


class GitHubIssueAdapter:
    def __init__(self, store, vault, client: httpx.AsyncClient) -> None:
        self.store = store
        self.vault = vault
        self.client = client

    async def _headers(self, owner_id: str, connection) -> dict[str, str]:
        envelope = await self.store.get_credential(owner_id, "github", connection.id)
        token = self.vault.decrypt(
            envelope, CredentialBinding(owner_id, "github", connection.id)
        ).decode("utf-8")
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        }

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
