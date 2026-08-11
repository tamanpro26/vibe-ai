from __future__ import annotations

import pytest

from capabilities.registry import CapabilityRegistry
from capabilities.store import CapabilityStore
from tests.capabilities.test_manifests import native_manifest


@pytest.mark.asyncio
async def test_editing_published_skill_creates_a_new_version(tmp_path):
    store = CapabilityStore(f"sqlite+aiosqlite:///{tmp_path / 'capabilities.db'}")
    await store.init()
    registry = CapabilityRegistry(store)

    draft = await registry.create_draft("user-1", native_manifest())
    first = await registry.publish("user-1", draft.id)
    edited = await registry.edit_as_new_draft(
        "user-1", first.id, {"version": "1.1.0", "description": "Updated safely."}
    )
    second = await registry.publish("user-1", edited.id)

    assert first.id != second.id
    assert first.content_digest != second.content_digest
    assert (await store.get_version(first.id)).manifest["description"] != second.manifest["description"]


@pytest.mark.asyncio
async def test_builtin_registry_seed_is_idempotent(tmp_path):
    store = CapabilityStore(f"sqlite+aiosqlite:///{tmp_path / 'capabilities.db'}")
    await store.init()
    registry = CapabilityRegistry(store)

    first = await registry.seed_builtins()
    second = await registry.seed_builtins()

    assert [item.content_digest for item in first] == [item.content_digest for item in second]
    assert first[0].owner_id == "vibeai"


@pytest.mark.asyncio
async def test_user_draft_cannot_self_grant_trust_permissions_or_actions(tmp_path):
    store = CapabilityStore(f"sqlite+aiosqlite:///{tmp_path / 'authority.db'}")
    await store.init()
    registry = CapabilityRegistry(store)

    with pytest.raises(ValueError, match="User Imported"):
        await registry.create_draft("user-1", native_manifest(trust="vibeai_reviewed"))
    with pytest.raises(ValueError, match="instruction skills"):
        await registry.create_draft(
            "user-1",
            native_manifest(
                kind="approved_action",
                instructions=None,
                permissions=[{"name": "github.write", "purpose": "Write issues"}],
            ),
        )
