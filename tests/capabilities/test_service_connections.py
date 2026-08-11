from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from api.integrations import GitHubAppClient, _sign_state, _verify_state
from capabilities.store import CapabilityStore


@pytest.mark.asyncio
async def test_service_connections_are_owner_bound_and_revocable(tmp_path):
    store = CapabilityStore(f"sqlite+aiosqlite:///{tmp_path / 'connections.db'}")
    await store.init()
    connection = await store.create_service_connection(
        owner_id="user-1",
        provider="github",
        external_account_id="installation:123",
        credential_reference="credential:opaque",
        allowed_operations=["issues:read", "issues:comment"],
        immutable_targets=["repo:456"],
    )

    assert (await store.get_service_connection("user-1", connection.id)).id == connection.id
    with pytest.raises(PermissionError):
        await store.get_service_connection("user-2", connection.id)

    await store.revoke_service_connection("user-1", connection.id)
    with pytest.raises(PermissionError):
        await store.get_service_connection("user-1", connection.id)


@pytest.mark.asyncio
async def test_oauth_state_is_owner_bound_and_single_use(tmp_path):
    store = CapabilityStore(f"sqlite+aiosqlite:///{tmp_path / 'oauth.db'}")
    await store.init()
    state, digest, expires_at = _sign_state("user-1", "state-secret")
    assert _verify_state(state, "state-secret")["sub"] == "user-1"
    with pytest.raises(ValueError):
        _verify_state(state + "tampered", "state-secret")

    await store.issue_oauth_state("user-1", "github", digest, expires_at)
    with pytest.raises(PermissionError):
        await store.consume_oauth_state("user-2", "github", digest)
    await store.consume_oauth_state("user-1", "github", digest)
    with pytest.raises(PermissionError):
        await store.consume_oauth_state("user-1", "github", digest)


@pytest.mark.asyncio
async def test_github_installation_is_verified_server_to_server():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/app/installations/123":
            return httpx.Response(200, json={"account": {"id": 77, "login": "owner"}})
        if request.url.path == "/app/installations/123/access_tokens":
            return httpx.Response(201, json={"token": "short-lived", "expires_at": "2030-01-01T00:00:00Z"})
        if request.url.path == "/installation/repositories":
            return httpx.Response(200, json={"repositories": [{"id": 456}, {"id": 789}]})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        verified = await GitHubAppClient("42", pem, client).verify_installation(123)

    assert verified["external_account_id"] == "77:123"
    assert verified["repository_ids"] == ["456", "789"]


@pytest.mark.asyncio
async def test_github_installation_requires_the_users_admin_identity():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "user-token"})
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 99, "login": "attacker"})
        if request.url.path == "/app/installations/123":
            return httpx.Response(
                200,
                json={"account": {"id": 77, "login": "victim", "type": "User"}},
            )
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        github = GitHubAppClient("42", pem, client)
        with pytest.raises(PermissionError, match="does not own"):
            await github.verify_user_admin(
                "oauth-code",
                123,
                client_id="client-id",
                client_secret="client-secret",
                redirect_uri="https://vibe.test/callback",
            )
