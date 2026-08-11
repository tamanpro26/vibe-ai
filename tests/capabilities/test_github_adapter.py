from __future__ import annotations

import base64
import os

import httpx
import pytest

from capabilities.actions import GitHubIssueComment, ProposedAction
from capabilities.credentials import CredentialBinding, CredentialVault
from capabilities.integrations.github import GitHubIssueAdapter
from capabilities.store import CapabilityStore
from tests.capabilities.test_action_policy import proposal


@pytest.mark.asyncio
async def test_github_adapter_uses_pinned_api_and_exact_numeric_target(tmp_path):
    store = CapabilityStore(f"sqlite+aiosqlite:///{tmp_path / 'github.db'}")
    await store.init()
    key = base64.urlsafe_b64encode(os.urandom(32)).decode()
    vault = CredentialVault({"v1": key}, "v1", "test")
    connection = await store.create_service_connection(
        "user-1", "github", "installation:1", "credential:1",
        ["issues:read", "issues:comment"], ["repository:456"], connection_id="connection-1",
    )
    envelope = vault.encrypt(b"token", CredentialBinding("user-1", "github", connection.id))
    await store.put_credential("user-1", "github", connection.id, envelope)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.headers["X-GitHub-Api-Version"] == "2026-03-10"
        assert request.url.host == "api.github.com"
        if request.method == "GET":
            return httpx.Response(200, json={"id": 12, "state": "open"})
        return httpx.Response(201, json={"id": 99, "html_url": "https://github.com/comment/99"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        adapter = GitHubIssueAdapter(store, vault, client)
        action = proposal()
        await adapter.preflight(action, connection)
        result = await adapter.execute(action, connection)

    assert [call.url.path for call in calls] == [
        "/repositories/456/issues/12", "/repositories/456/issues/12/comments"
    ]
    assert result["comment_id"] == "99"
