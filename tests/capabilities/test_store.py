from __future__ import annotations

import pytest

from capabilities.models import ActivationMode, ScopeKind, ScopeState
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
