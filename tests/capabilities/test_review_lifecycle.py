from __future__ import annotations

import base64
import os

import pytest

from capabilities.credentials import CredentialVault
from capabilities.models import ReviewState
from capabilities.review import EncryptedQuarantine, PackageImporter, ReviewService
from capabilities.store import CapabilityStore
from tests.capabilities.test_import_security import archive
from tests.capabilities.test_provider_adapters import skill


def vault() -> CredentialVault:
    key = base64.urlsafe_b64encode(os.urandom(32)).decode()
    return CredentialVault({"v1": key}, "v1", "test")


@pytest.mark.asyncio
async def test_review_binds_exact_digest_and_requires_server_role(tmp_path):
    store = CapabilityStore(f"sqlite+aiosqlite:///{tmp_path / 'review.db'}")
    await store.init()
    await store.bootstrap_roles_once(["admin-1"], ["reviewer-1"])
    quarantine = EncryptedQuarantine(tmp_path / "quarantine", vault())
    service = ReviewService(store, PackageImporter(), quarantine)
    candidate = await service.submit_github(
        "user-1",
        archive({"SKILL.md": skill()}),
        provider="claude",
        repository="owner/repo",
        commit_sha="a" * 40,
    )

    with pytest.raises(PermissionError):
        await service.approve("user-1", candidate.id, candidate.source_digest, candidate.evidence_digest)
    with pytest.raises(ValueError):
        await service.approve("reviewer-1", candidate.id, "sha256:changed", candidate.evidence_digest)

    version = await service.approve(
        "reviewer-1", candidate.id, candidate.source_digest, candidate.evidence_digest
    )
    assert version.review_state is ReviewState.REVIEWED
    assert not quarantine.exists(candidate.id)


@pytest.mark.asyncio
async def test_changed_digest_does_not_inherit_trust_and_revocation_removes_eligibility(tmp_path):
    store = CapabilityStore(f"sqlite+aiosqlite:///{tmp_path / 'lifecycle.db'}")
    await store.init()
    await store.bootstrap_roles_once([], ["reviewer-1"])
    service = ReviewService(
        store, PackageImporter(), EncryptedQuarantine(tmp_path / "quarantine", vault())
    )
    first = await service.submit_github(
        "user-1", archive({"SKILL.md": skill()}), "claude", "owner/repo", "a" * 40
    )
    version = await service.approve(
        "reviewer-1", first.id, first.source_digest, first.evidence_digest
    )
    await store.install("user-1", version.id)

    second = await service.submit_github(
        "user-1",
        archive({"SKILL.md": skill(description="Changed content")}),
        "claude",
        "owner/repo",
        "b" * 40,
    )
    assert second.source_digest != first.source_digest
    assert second.state is ReviewState.AWAITING_REVIEW

    await service.revoke("reviewer-1", version.id, "security finding")
    assert await store.list_resolvable_versions("user-1") == []


@pytest.mark.asyncio
async def test_reviewer_authority_is_rechecked_at_decision_time(tmp_path):
    store = CapabilityStore(f"sqlite+aiosqlite:///{tmp_path / 'authority.db'}")
    await store.init()
    await store.bootstrap_roles_once(["admin-1"], ["reviewer-1"])
    service = ReviewService(
        store, PackageImporter(), EncryptedQuarantine(tmp_path / "quarantine", vault())
    )
    candidate = await service.submit_github(
        "user-1", archive({"SKILL.md": skill()}), "claude", "owner/repo", "a" * 40
    )
    await store.revoke_role("admin-1", "reviewer-1", "reviewer")

    with pytest.raises(PermissionError):
        await service.approve(
            "reviewer-1", candidate.id, candidate.source_digest, candidate.evidence_digest
        )
