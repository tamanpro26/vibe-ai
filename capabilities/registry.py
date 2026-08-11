"""Canonical discovery and immutable lifecycle service."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from capabilities.manifests import CapabilityManifest, validate_package
from capabilities.models import CapabilityAuthorDraft, CapabilityVersionRecord
from capabilities.store import CapabilityStore


class CapabilityRegistry:
    def __init__(self, store: CapabilityStore) -> None:
        self.store = store

    async def create_draft(
        self, owner_id: str, manifest: dict[str, Any]
    ) -> CapabilityAuthorDraft:
        validate_package(manifest)
        return await self.store.create_draft(owner_id, manifest)

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

    async def seed_builtins(self, builtins_root: Path | None = None) -> list[CapabilityVersionRecord]:
        root = builtins_root or Path(__file__).with_name("builtins")
        versions: list[CapabilityVersionRecord] = []
        for manifest_path in sorted(root.glob("*/manifest.json")):
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            draft = await self.create_draft("vibeai", manifest)
            versions.append(await self.publish("vibeai", draft.id))
        return versions
