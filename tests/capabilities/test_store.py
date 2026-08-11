from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from capabilities.models import (
    ActionStatus,
    ActivationMode,
    CapabilityActionRequest,
    ScopeKind,
    ScopeState,
)
from capabilities.store import CapabilityStore
from tests.capabilities.test_manifests import native_manifest


@pytest.mark.asyncio
async def test_draft_publish_install_scope_and_archive_lifecycle(tmp_path):
    store = CapabilityStore(f"sqlite+aiosqlite:///{tmp_path / 'capabilities.db'}")
    await store.init()

    draft = await store.create_draft("user-1", native_manifest())
    version = await store.publish_draft("user-1", draft.id)
    installed = await store.install("user-1", version.id)
    override = await store.set_scope_override(
        "user-1",
        installed.id,
        ScopeKind.CHAT,
        "chat-1",
        ScopeState.DISABLED,
        {"tone": "concise"},
    )

    assert version.owner_id == "user-1"
    assert override.state is ScopeState.DISABLED
    assert override.configuration_patch == {"tone": "concise"}
    assert await store.get_activation_mode("user-1") is ActivationMode.MANUAL_ONLY

    await store.archive_version("user-1", version.id)
    archived = await store.get_version(version.id)
    assert archived.archived_at is not None
    assert (await store.list_resolvable_versions("user-1")) == []

    await store.close()


@pytest.mark.asyncio
async def test_versions_are_immutable_and_owner_scoped(tmp_path):
    store = CapabilityStore(f"sqlite+aiosqlite:///{tmp_path / 'capabilities.db'}")
    await store.init()
    draft = await store.create_draft("user-1", native_manifest())
    version = await store.publish_draft("user-1", draft.id)

    with pytest.raises(PermissionError):
        await store.archive_version("user-2", version.id)
    with pytest.raises(ValueError, match="immutable"):
        await store.update_version_manifest(version.id, native_manifest(name="Changed"))


@pytest.mark.asyncio
async def test_audit_payloads_are_redacted_and_append_only(tmp_path):
    store = CapabilityStore(f"sqlite+aiosqlite:///{tmp_path / 'capabilities.db'}")
    await store.init()
    event = await store.append_audit(
        owner_id="user-1",
        event_type="credential.connected",
        subject_id="connection-1",
        payload={"token": "secret", "permission": "issues:write"},
    )

    assert "secret" not in str(event.payload)
    assert event.payload["token"] == "[REDACTED]"
    with pytest.raises(ValueError, match="append-only"):
        await store.update_audit(event.id, {})


@pytest.mark.asyncio
async def test_schema_initialization_is_idempotent_and_production_sqlite_is_rejected(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'capabilities.db'}"
    store = CapabilityStore(url)
    await store.init()
    await store.init()
    await store.close()

    with pytest.raises(ValueError, match="PostgreSQL"):
        CapabilityStore(url, environment="production")


@pytest.mark.asyncio
async def test_standalone_chat_scope_can_be_registered_without_a_project(tmp_path):
    store = CapabilityStore(f"sqlite+aiosqlite:///{tmp_path / 'capabilities.db'}")
    await store.init()

    chat = await store.register_owned_scope("user-1", ScopeKind.CHAT, "chat-1")

    assert chat.parent_scope_id is None
    assert await store.owns_scope("user-1", ScopeKind.CHAT, "chat-1")


@pytest.mark.asyncio
async def test_expired_approval_is_persisted_instead_of_rolled_back(tmp_path):
    from tests.capabilities.test_action_broker import setup_broker

    store, broker, proposal = await setup_broker(tmp_path)
    action = await broker.propose("user-1", proposal)
    async with store._sessions.begin() as session:
        await session.execute(
            update(CapabilityActionRequest)
            .where(CapabilityActionRequest.id == action.id)
            .values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        )

    with pytest.raises(ValueError, match="expired"):
        await broker.approve("user-1", action.id, action.request_digest)

    assert (await broker.get("user-1", action.id)).status is ActionStatus.EXPIRED
