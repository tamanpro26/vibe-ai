"""Canonical discovery and immutable lifecycle service."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from capabilities.manifests import CapabilityKind, CapabilityManifest, TrustState, validate_package
from capabilities.models import CapabilityAuthorDraft, CapabilityVersionRecord
from capabilities.store import CapabilityStore


class CapabilityRegistry:
    def __init__(self, store: CapabilityStore) -> None:
        self.store = store

    async def create_draft(
        self, owner_id: str, manifest: dict[str, Any]
    ) -> CapabilityAuthorDraft:
        parsed = validate_package(manifest).manifest
        if owner_id != "vibeai":
            if parsed.kind is not CapabilityKind.INSTRUCTION_SKILL:
                raise ValueError("user-authored capabilities must be instruction skills")
            if parsed.trust is not TrustState.USER_IMPORTED:
                raise ValueError("user-authored capabilities use User Imported trust")
            if parsed.permissions or parsed.services or parsed.dependencies:
                raise ValueError("user-authored instruction skills cannot grant permissions or services")
        return await self.store.create_draft(owner_id, parsed.model_dump(mode="json"))

    def preview(self, manifest: dict[str, Any]) -> CapabilityManifest:
        return validate_package(manifest).manifest

    async def publish(self, owner_id: str, draft_id: str) -> CapabilityVersionRecord:
        return await self.store.publish_draft(owner_id, draft_id)

    async def edit_as_new_draft(
        self, owner_id: str, version_id: str, patch: dict[str, Any]
    ) -> CapabilityAuthorDraft:
        return await self.store.create_draft_from_version(owner_id, version_id, patch)

    async def archive(self, owner_id: str, version_id: str) -> None:
        await self.store.archive_version(owner_id, version_id)

    async def install(self, owner_id: str, version_id: str):
        """Install a bundle and its version-pinned members as one logical operation."""
        root = await self.store.get_version(version_id)
        if root is None or root.owner_id not in {owner_id, "vibeai"}:
            raise PermissionError("capability version not available to owner")
        visited: set[str] = set()

        async def install_version(record):
            manifest = CapabilityManifest.model_validate(record.manifest)
            if manifest.capability_id in visited:
                return await self.store.get_active_installation(owner_id, record.id)
            visited.add(manifest.capability_id)
            for dependency in manifest.dependencies:
                member = await self.store.find_version(
                    owner_id, dependency.capability_id, dependency.version
                )
                if member is None:
                    if dependency.required:
                        raise ValueError(f"required member unavailable: {dependency.capability_id}")
                    continue
                await install_version(member)
            return await self.store.install(owner_id, record.id)

        return await install_version(root)

    async def seed_builtins(self, builtins_root: Path | None = None) -> list[CapabilityVersionRecord]:
        root = builtins_root or Path(__file__).with_name("builtins")
        versions: list[CapabilityVersionRecord] = []
        for manifest_path in sorted(root.glob("*/manifest.json")):
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            draft = await self.create_draft("vibeai", manifest)
            versions.append(await self.publish("vibeai", draft.id))
        return versions
