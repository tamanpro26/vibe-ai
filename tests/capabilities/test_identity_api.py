from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.capabilities import get_capability_store, router
from api.identity import IdentityVerifier, Principal, get_current_principal
from capabilities.store import CapabilityStore
from tests.capabilities.test_manifests import native_manifest


ISSUER = "https://clerk.example.test"
AUDIENCE = "vibeai-web"
AUTHORIZED_PARTY = "https://vibeai.example.test"


@pytest.fixture(scope="module")
def signing_keys():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


def token(private_key, **changes) -> str:
    now = datetime.now(timezone.utc)
    claims = {
        "sub": "user-1",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "azp": AUTHORIZED_PARTY,
        "iat": now,
        "nbf": now - timedelta(seconds=1),
        "exp": now + timedelta(minutes=5),
        "sid": "session-1",
    }
    claims.update(changes)
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test-key"})


def test_valid_identity_is_verified(signing_keys):
    private, public = signing_keys
    principal = IdentityVerifier(
        issuer=ISSUER,
        audience=AUDIENCE,
        authorized_parties={AUTHORIZED_PARTY},
        static_key=public,
    ).verify(token(private))

    assert principal.subject == "user-1"
    assert principal.session_id == "session-1"


@pytest.mark.parametrize(
    "changes",
    [
        {"iss": "https://attacker.invalid"},
        {"aud": "another-app"},
        {"azp": "https://attacker.invalid"},
        {"exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
        {"nbf": datetime.now(timezone.utc) + timedelta(minutes=5)},
    ],
)
def test_invalid_identity_claims_fail_closed(signing_keys, changes):
    private, public = signing_keys
    verifier = IdentityVerifier(
        issuer=ISSUER,
        audience=AUDIENCE,
        authorized_parties={AUTHORIZED_PARTY},
        static_key=public,
    )
    with pytest.raises(ValueError):
        verifier.verify(token(private, **changes))


@pytest.mark.asyncio
async def test_capability_api_is_owner_scoped(tmp_path):
    store = CapabilityStore(f"sqlite+aiosqlite:///{tmp_path / 'api.db'}")
    await store.init()
    app = FastAPI()
    app.include_router(router)
    owner = {"value": "user-1"}
    app.dependency_overrides[get_capability_store] = lambda: store
    app.dependency_overrides[get_current_principal] = lambda: Principal(
        subject=owner["value"], session_id="session", claims={}
    )
    client = TestClient(app)

    draft_response = client.post("/api/capabilities/drafts", json={"manifest": native_manifest()})
    assert draft_response.status_code == 201
    draft_id = draft_response.json()["id"]
    published = client.post(f"/api/capabilities/drafts/{draft_id}/publish")
    assert published.status_code == 201
    version_id = published.json()["id"]

    owner["value"] = "user-2"
    assert client.get(f"/api/capabilities/versions/{version_id}").status_code == 404
    assert client.post(f"/api/capabilities/versions/{version_id}/archive").status_code == 404
    assert client.post(f"/api/capabilities/versions/{version_id}/install").status_code == 404


@pytest.mark.asyncio
async def test_project_and_chat_scope_must_be_registered_by_owner(tmp_path):
    store = CapabilityStore(f"sqlite+aiosqlite:///{tmp_path / 'scope.db'}")
    await store.init()
    app = FastAPI()
    app.include_router(router)
    owner = {"value": "user-1"}
    app.dependency_overrides[get_capability_store] = lambda: store
    app.dependency_overrides[get_current_principal] = lambda: Principal(
        subject=owner["value"], session_id="session", claims={}
    )
    client = TestClient(app)

    project = client.post("/api/capabilities/scopes/project/project-1")
    chat = client.post(
        "/api/capabilities/scopes/chat/chat-1", json={"parent_scope_id": "project-1"}
    )
    assert project.status_code == 201
    assert chat.status_code == 201

    owner["value"] = "user-2"
    copied = client.post(
        "/api/capabilities/scopes/chat/chat-2", json={"parent_scope_id": "project-1"}
    )
    assert copied.status_code == 404


@pytest.mark.asyncio
async def test_reviewer_bootstrap_is_one_time_and_server_owned(tmp_path):
    store = CapabilityStore(f"sqlite+aiosqlite:///{tmp_path / 'roles.db'}")
    await store.init()

    assert await store.bootstrap_roles_once(["admin-1"], ["reviewer-1"]) is True
    assert await store.has_role("admin-1", "admin")
    assert await store.has_role("admin-1", "reviewer")
    assert await store.has_role("reviewer-1", "reviewer")
    assert await store.bootstrap_roles_once(["attacker"], ["attacker"]) is False
    assert not await store.has_role("attacker", "admin")


def test_capability_api_rejects_missing_end_user_token(tmp_path):
    store = CapabilityStore(f"sqlite+aiosqlite:///{tmp_path / 'missing-user.db'}")
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_capability_store] = lambda: store

    response = TestClient(app).get("/api/capabilities")
    assert response.status_code == 401
