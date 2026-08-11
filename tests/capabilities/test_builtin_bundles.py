from __future__ import annotations

from capabilities.models import ActivationMode
from capabilities.registry import CapabilityRegistry
from capabilities.resolver import ResolutionRequest, resolve_from_store
from capabilities.store import CapabilityStore


async def test_builtin_families_seed_install_and_compose(tmp_path):
    store = CapabilityStore(f"sqlite+aiosqlite:///{tmp_path / 'builtins.db'}")
    await store.init()
    registry = CapabilityRegistry(store)
    seeded = await registry.seed_builtins()
    identities = {item.capability_id for item in seeded}
    assert {
        "research-analyst", "software-engineering", "content-studio",
        "quality-verifier", "research-to-creation", "github-issue-comment",
    }.issubset(identities)

    bundle = next(item for item in seeded if item.capability_id == "research-to-creation")
    await registry.install("user-1", bundle.id)
    installed = await store.list_active_installations("user-1")
    installed_ids = {item.capability_version_id for item in installed}
    assert bundle.id in installed_ids
    assert len(installed_ids) == 4

    snapshot = await resolve_from_store(
        store,
        ResolutionRequest(
            owner_id="user-1",
            prompt="Research the evidence and write a creative report",
            activation_mode=ActivationMode.AUTOMATIC,
        ),
    )
    selected = {item.capability_id for item in snapshot.selected}
    assert {"research-to-creation", "research-analyst", "content-studio"}.issubset(selected)
    assert snapshot.dependency_graph["research-to-creation"] == (
        "research-analyst", "content-studio", "quality-verifier"
    )


async def test_bundle_exposes_skipped_optional_member(tmp_path):
    store = CapabilityStore(f"sqlite+aiosqlite:///{tmp_path / 'optional.db'}")
    await store.init()
    registry = CapabilityRegistry(store)
    seeded = await registry.seed_builtins()
    bundle = next(item for item in seeded if item.capability_id == "research-to-creation")
    await registry.install("user-1", bundle.id)
    verifier = next(item for item in seeded if item.capability_id == "quality-verifier")
    await store.archive_version("vibeai", verifier.id)

    snapshot = await resolve_from_store(
        store,
        ResolutionRequest(
            owner_id="user-1",
            prompt="Research and write a report",
            activation_mode=ActivationMode.AUTOMATIC,
        ),
    )
    assert snapshot.skipped_optional == {"research-to-creation": ("quality-verifier",)}
