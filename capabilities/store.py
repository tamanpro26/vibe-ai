"""Transactional async persistence for Capability Hub state."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from capabilities.credentials import CredentialEnvelope
from capabilities.manifests import ActivationMode, CapabilityManifest, TrustState, validate_package
from capabilities.models import (
    Base,
    CapabilityActivationPreference,
    CapabilityAuditEvent,
    CapabilityAuthorDraft,
    CapabilityCredentialRecord,
    CapabilityInstallation,
    CapabilityImportCandidate,
    CapabilityOwnedScope,
    CapabilityOAuthState,
    CapabilityReviewEvidence,
    CapabilityRoleAssignment,
    CapabilityScopeOverride,
    CapabilitySystemState,
    CapabilityVersionRecord,
    DraftState,
    ReviewState,
    ScopeKind,
    ScopeState,
)

_SENSITIVE_KEYS = {
    "access_token", "authorization", "client_secret", "credential", "password",
    "private_key", "refresh_token", "secret", "token",
}


class CapabilityStore:
    """Owns capability transactions; callers never receive a raw DB session."""

    def __init__(self, database_url: str, *, environment: str = "local") -> None:
        if environment == "production" and database_url.startswith("sqlite"):
            raise ValueError("production capability storage must use PostgreSQL")
        self.database_url = database_url
        self.engine: AsyncEngine = create_async_engine(database_url, pool_pre_ping=True)
        if database_url.startswith("sqlite"):
            event.listen(self.engine.sync_engine, "connect", _enable_sqlite_foreign_keys)
        self._sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def init(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        await self.engine.dispose()

    async def create_draft(self, owner_id: str, manifest: dict[str, Any]) -> CapabilityAuthorDraft:
        parsed = CapabilityManifest.model_validate(manifest)
        async with self._sessions.begin() as session:
            draft = CapabilityAuthorDraft(owner_id=owner_id, manifest=parsed.model_dump(mode="json"))
            session.add(draft)
        return draft

    async def publish_draft(self, owner_id: str, draft_id: str) -> CapabilityVersionRecord:
        async with self._sessions.begin() as session:
            draft = await session.get(CapabilityAuthorDraft, draft_id)
            if draft is None or draft.owner_id != owner_id:
                raise PermissionError("draft not found for owner")
            if draft.state is not DraftState.DRAFT:
                raise ValueError("only an editable draft may be published")
            package = validate_package(draft.manifest)
            manifest = package.manifest
            existing = await session.scalar(
                select(CapabilityVersionRecord).where(
                    CapabilityVersionRecord.capability_id == manifest.capability_id,
                    CapabilityVersionRecord.version == manifest.version,
                    CapabilityVersionRecord.content_digest == manifest.content_digest,
                )
            )
            if existing is not None:
                if existing.owner_id != owner_id:
                    raise ValueError("version identity already belongs to another owner")
                draft.state = DraftState.PUBLISHED
                return existing
            review_state = (
                ReviewState.REVIEWED
                if manifest.trust in {TrustState.VIBEAI_BUILTIN, TrustState.USER_IMPORTED}
                else ReviewState.AWAITING_REVIEW
            )
            version = CapabilityVersionRecord(
                owner_id=owner_id,
                capability_id=manifest.capability_id,
                version=manifest.version,
                kind=manifest.kind.value,
                trust=manifest.trust.value,
                review_state=review_state,
                content_digest=manifest.content_digest,
                manifest=manifest.model_dump(mode="json"),
                source_manifest=package.source_manifest,
                source_digest=package.source_digest,
            )
            session.add(version)
            draft.state = DraftState.PUBLISHED
        return version

    async def create_draft_from_version(
        self, owner_id: str, version_id: str, patch: dict[str, Any]
    ) -> CapabilityAuthorDraft:
        async with self._sessions.begin() as session:
            version = await session.get(CapabilityVersionRecord, version_id)
            if version is None or version.owner_id != owner_id:
                raise PermissionError("capability version not found for owner")
            updated = {**version.manifest, **patch}
            parsed = CapabilityManifest.model_validate(updated)
            draft = CapabilityAuthorDraft(
                owner_id=owner_id,
                manifest=parsed.model_dump(mode="json"),
                source_version_id=version_id,
            )
            session.add(draft)
        return draft

    async def delete_draft(self, owner_id: str, draft_id: str) -> None:
        async with self._sessions.begin() as session:
            draft = await session.get(CapabilityAuthorDraft, draft_id)
            if draft is None or draft.owner_id != owner_id:
                raise PermissionError("draft not found for owner")
            if draft.state is not DraftState.DRAFT:
                raise ValueError("only an unpublished draft can be deleted")
            await session.delete(draft)

    async def get_version(self, version_id: str) -> CapabilityVersionRecord | None:
        async with self._sessions() as session:
            return await session.get(CapabilityVersionRecord, version_id)

    async def update_version_manifest(self, version_id: str, manifest: dict[str, Any]) -> None:
        del version_id, manifest
        raise ValueError("capability versions are immutable; create a new draft")

    async def archive_version(self, owner_id: str, version_id: str) -> None:
        async with self._sessions.begin() as session:
            version = await session.get(CapabilityVersionRecord, version_id)
            if version is None or version.owner_id != owner_id:
                raise PermissionError("capability version not found for owner")
            if version.archived_at is None:
                version.archived_at = datetime.now(timezone.utc)

    async def install(self, owner_id: str, version_id: str) -> CapabilityInstallation:
        async with self._sessions.begin() as session:
            version = await session.get(CapabilityVersionRecord, version_id)
            if version is None:
                raise LookupError("capability version not found")
            if version.owner_id not in {owner_id, "vibeai"}:
                raise PermissionError("capability version not available to owner")
            if version.archived_at or version.revoked_at:
                raise ValueError("archived or revoked capability cannot be installed")
            existing = await session.scalar(
                select(CapabilityInstallation).where(
                    CapabilityInstallation.owner_id == owner_id,
                    CapabilityInstallation.capability_version_id == version_id,
                    CapabilityInstallation.uninstalled_at.is_(None),
                )
            )
            if existing:
                return existing
            installation = CapabilityInstallation(owner_id=owner_id, capability_version_id=version_id)
            session.add(installation)
        return installation

    async def set_scope_override(
        self,
        owner_id: str,
        installation_id: str,
        scope_kind: ScopeKind,
        scope_id: str,
        state: ScopeState,
        configuration_patch: dict[str, Any] | None = None,
    ) -> CapabilityScopeOverride:
        async with self._sessions.begin() as session:
            installation = await session.get(CapabilityInstallation, installation_id)
            if installation is None or installation.owner_id != owner_id:
                raise PermissionError("installation not found for owner")
            existing = await session.scalar(
                select(CapabilityScopeOverride).where(
                    CapabilityScopeOverride.owner_id == owner_id,
                    CapabilityScopeOverride.installation_id == installation_id,
                    CapabilityScopeOverride.scope_kind == scope_kind,
                    CapabilityScopeOverride.scope_id == scope_id,
                )
            )
            if existing:
                existing.state = state
                existing.configuration_patch = configuration_patch or {}
                return existing
            override = CapabilityScopeOverride(
                owner_id=owner_id,
                installation_id=installation_id,
                scope_kind=scope_kind,
                scope_id=scope_id,
                state=state,
                configuration_patch=configuration_patch or {},
            )
            session.add(override)
        return override

    async def get_activation_mode(self, owner_id: str) -> ActivationMode:
        async with self._sessions() as session:
            preference = await session.get(CapabilityActivationPreference, owner_id)
            return preference.mode if preference else ActivationMode.MANUAL_ONLY

    async def set_activation_mode(
        self, owner_id: str, mode: ActivationMode, *, onboarding_accepted: bool = False
    ) -> CapabilityActivationPreference:
        async with self._sessions.begin() as session:
            preference = await session.get(CapabilityActivationPreference, owner_id)
            if preference is None:
                preference = CapabilityActivationPreference(owner_id=owner_id)
                session.add(preference)
            preference.mode = mode
            if onboarding_accepted and preference.onboarding_accepted_at is None:
                preference.onboarding_accepted_at = datetime.now(timezone.utc)
        return preference

    async def register_owned_scope(
        self,
        owner_id: str,
        scope_kind: ScopeKind,
        scope_id: str,
        parent_scope_id: str | None = None,
    ) -> CapabilityOwnedScope:
        if scope_kind is ScopeKind.ACCOUNT:
            raise ValueError("account ownership comes from the verified principal")
        if scope_kind is ScopeKind.CHAT:
            if not parent_scope_id or not await self.owns_scope(
                owner_id, ScopeKind.PROJECT, parent_scope_id
            ):
                raise PermissionError("parent project scope not found for owner")
        async with self._sessions.begin() as session:
            record = await session.scalar(
                select(CapabilityOwnedScope).where(
                    CapabilityOwnedScope.owner_id == owner_id,
                    CapabilityOwnedScope.scope_kind == scope_kind,
                    CapabilityOwnedScope.scope_id == scope_id,
                )
            )
            if record:
                record.active = True
                record.deleted_at = None
                if parent_scope_id:
                    record.parent_scope_id = parent_scope_id
                return record
            record = CapabilityOwnedScope(
                owner_id=owner_id,
                scope_kind=scope_kind,
                scope_id=scope_id,
                parent_scope_id=parent_scope_id,
            )
            session.add(record)
        return record

    async def owns_scope(self, owner_id: str, scope_kind: ScopeKind, scope_id: str) -> bool:
        if scope_kind is ScopeKind.ACCOUNT:
            return scope_id == owner_id
        async with self._sessions() as session:
            record = await session.scalar(
                select(CapabilityOwnedScope.id).where(
                    CapabilityOwnedScope.owner_id == owner_id,
                    CapabilityOwnedScope.scope_kind == scope_kind,
                    CapabilityOwnedScope.scope_id == scope_id,
                    CapabilityOwnedScope.active.is_(True),
                )
            )
            return record is not None

    async def delete_owned_scope(self, owner_id: str, scope_kind: ScopeKind, scope_id: str) -> None:
        async with self._sessions.begin() as session:
            record = await session.scalar(
                select(CapabilityOwnedScope).where(
                    CapabilityOwnedScope.owner_id == owner_id,
                    CapabilityOwnedScope.scope_kind == scope_kind,
                    CapabilityOwnedScope.scope_id == scope_id,
                    CapabilityOwnedScope.active.is_(True),
                )
            )
            if record is None:
                raise PermissionError("scope not found for owner")
            record.active = False
            record.deleted_at = datetime.now(timezone.utc)

    async def bootstrap_roles_once(
        self, admin_ids: list[str], reviewer_ids: list[str]
    ) -> bool:
        """Apply deployment-secret bootstrap exactly once, then seal it."""
        async with self._sessions.begin() as session:
            marker = await session.get(CapabilitySystemState, "role_bootstrap_complete")
            if marker is not None:
                return False
            assignments = {
                **{user_id: {"admin", "reviewer"} for user_id in admin_ids if user_id},
            }
            for user_id in reviewer_ids:
                if user_id:
                    assignments.setdefault(user_id, set()).add("reviewer")
            for user_id, roles in assignments.items():
                for role in roles:
                    session.add(
                        CapabilityRoleAssignment(
                            user_id=user_id,
                            role=role,
                            granted_by="deployment-bootstrap",
                        )
                    )
            session.add(
                CapabilitySystemState(
                    key="role_bootstrap_complete",
                    value={"admin_count": len(admin_ids), "reviewer_count": len(reviewer_ids)},
                )
            )
        return True

    async def has_role(self, user_id: str, role: str) -> bool:
        async with self._sessions() as session:
            assignment = await session.scalar(
                select(CapabilityRoleAssignment.id).where(
                    CapabilityRoleAssignment.user_id == user_id,
                    CapabilityRoleAssignment.role == role,
                    CapabilityRoleAssignment.revoked_at.is_(None),
                )
            )
            return assignment is not None

    async def grant_role(self, admin_id: str, user_id: str, role: str) -> None:
        if role not in {"admin", "reviewer"}:
            raise ValueError("unsupported capability role")
        async with self._sessions.begin() as session:
            await _require_active_role(session, admin_id, "admin")
            assignment = await session.scalar(
                select(CapabilityRoleAssignment).where(
                    CapabilityRoleAssignment.user_id == user_id,
                    CapabilityRoleAssignment.role == role,
                )
            )
            if assignment:
                assignment.revoked_at = None
                assignment.granted_by = admin_id
                assignment.granted_at = datetime.now(timezone.utc)
            else:
                session.add(
                    CapabilityRoleAssignment(
                        user_id=user_id, role=role, granted_by=admin_id
                    )
                )

    async def revoke_role(self, admin_id: str, user_id: str, role: str) -> None:
        async with self._sessions.begin() as session:
            await _require_active_role(session, admin_id, "admin")
            assignment = await session.scalar(
                select(CapabilityRoleAssignment).where(
                    CapabilityRoleAssignment.user_id == user_id,
                    CapabilityRoleAssignment.role == role,
                    CapabilityRoleAssignment.revoked_at.is_(None),
                )
            )
            if assignment is None:
                raise LookupError("active role assignment not found")
            assignment.revoked_at = datetime.now(timezone.utc)

    async def list_resolvable_versions(self, owner_id: str) -> list[CapabilityVersionRecord]:
        async with self._sessions() as session:
            values = await session.scalars(
                select(CapabilityVersionRecord)
                .join(
                    CapabilityInstallation,
                    CapabilityInstallation.capability_version_id == CapabilityVersionRecord.id,
                )
                .where(
                    CapabilityInstallation.owner_id == owner_id,
                    CapabilityInstallation.uninstalled_at.is_(None),
                    CapabilityVersionRecord.archived_at.is_(None),
                    CapabilityVersionRecord.revoked_at.is_(None),
                    CapabilityVersionRecord.review_state == ReviewState.REVIEWED,
                )
                .order_by(CapabilityVersionRecord.capability_id, CapabilityVersionRecord.version)
            )
            return list(values)

    async def list_catalog(
        self, owner_id: str, *, limit: int = 50, offset: int = 0
    ) -> list[CapabilityVersionRecord]:
        async with self._sessions() as session:
            values = await session.scalars(
                select(CapabilityVersionRecord)
                .where(
                    CapabilityVersionRecord.owner_id.in_([owner_id, "vibeai"]),
                    CapabilityVersionRecord.archived_at.is_(None),
                    CapabilityVersionRecord.revoked_at.is_(None),
                )
                .order_by(CapabilityVersionRecord.capability_id, CapabilityVersionRecord.version)
                .offset(offset)
                .limit(limit)
            )
            return list(values)

    async def create_import_candidate(
        self,
        *,
        candidate_id: str,
        owner_id: str,
        provider: str,
        repository: str,
        commit_sha: str,
        source_digest: str,
        evidence_digest: str,
        adapter_version: str,
        manifest: dict[str, Any],
        compatibility_report: dict[str, Any],
        evidence: dict[str, Any],
    ) -> CapabilityImportCandidate:
        async with self._sessions.begin() as session:
            existing = await session.scalar(
                select(CapabilityImportCandidate).where(
                    CapabilityImportCandidate.owner_id == owner_id,
                    CapabilityImportCandidate.repository == repository,
                    CapabilityImportCandidate.commit_sha == commit_sha,
                    CapabilityImportCandidate.source_digest == source_digest,
                )
            )
            if existing:
                return existing
            candidate = CapabilityImportCandidate(
                id=candidate_id,
                owner_id=owner_id,
                provider=provider,
                repository=repository,
                commit_sha=commit_sha,
                source_digest=source_digest,
                evidence_digest=evidence_digest,
                adapter_version=adapter_version,
                manifest=manifest,
                compatibility_report=compatibility_report,
                evidence=evidence,
                state=ReviewState.AWAITING_REVIEW,
            )
            session.add(candidate)
        return candidate

    async def get_import_candidate(self, candidate_id: str) -> CapabilityImportCandidate | None:
        async with self._sessions() as session:
            return await session.get(CapabilityImportCandidate, candidate_id)

    async def approve_import_candidate(
        self,
        reviewer_id: str,
        candidate_id: str,
        expected_source_digest: str,
        expected_evidence_digest: str,
    ) -> CapabilityVersionRecord:
        async with self._sessions.begin() as session:
            await _require_active_role(session, reviewer_id, "reviewer")
            candidate = await session.get(CapabilityImportCandidate, candidate_id)
            if candidate is None:
                raise LookupError("import candidate not found")
            if candidate.state is not ReviewState.AWAITING_REVIEW:
                if candidate.state is ReviewState.REVIEWED and candidate.version_id:
                    version = await session.get(CapabilityVersionRecord, candidate.version_id)
                    if version:
                        return version
                raise ValueError("candidate is not awaiting review")
            if (
                candidate.source_digest != expected_source_digest
                or candidate.evidence_digest != expected_evidence_digest
            ):
                raise ValueError("review digest does not match immutable evidence")
            manifest_data = {**candidate.manifest, "trust": "vibeai_reviewed"}
            manifest = CapabilityManifest.model_validate(manifest_data)
            source_manifest = json.dumps(
                candidate.manifest, sort_keys=True, separators=(",", ":")
            ).encode()
            version = await session.scalar(
                select(CapabilityVersionRecord).where(
                    CapabilityVersionRecord.owner_id == "vibeai",
                    CapabilityVersionRecord.capability_id == manifest.capability_id,
                    CapabilityVersionRecord.version == manifest.version,
                    CapabilityVersionRecord.content_digest == manifest.content_digest,
                    CapabilityVersionRecord.source_digest == candidate.source_digest,
                )
            )
            if version is None:
                version = CapabilityVersionRecord(
                    owner_id="vibeai",
                    capability_id=manifest.capability_id,
                    version=manifest.version,
                    kind=manifest.kind.value,
                    trust=manifest.trust.value,
                    review_state=ReviewState.REVIEWED,
                    content_digest=manifest.content_digest,
                    manifest=manifest.model_dump(mode="json"),
                    source_manifest=source_manifest,
                    source_digest=candidate.source_digest,
                    source_provider=candidate.provider,
                    source_revision=candidate.commit_sha,
                    adapter_version=candidate.adapter_version,
                    compatibility_report=candidate.compatibility_report,
                )
                session.add(version)
                await session.flush()
                session.add(
                    CapabilityReviewEvidence(
                        capability_version_id=version.id,
                        evidence_digest=candidate.evidence_digest,
                        evidence=candidate.evidence,
                        reviewer_id=reviewer_id,
                        decision="approved",
                    )
                )
            candidate.state = ReviewState.REVIEWED
            candidate.version_id = version.id
            candidate.reviewer_id = reviewer_id
            candidate.decided_at = datetime.now(timezone.utc)
        return version

    async def revoke_version(self, version_id: str, reviewer_id: str, reason: str) -> None:
        async with self._sessions.begin() as session:
            await _require_active_role(session, reviewer_id, "reviewer")
            version = await session.get(CapabilityVersionRecord, version_id)
            if version is None:
                raise LookupError("capability version not found")
            version.review_state = ReviewState.REVOKED
            version.revoked_at = datetime.now(timezone.utc)
            candidate = await session.scalar(
                select(CapabilityImportCandidate).where(
                    CapabilityImportCandidate.version_id == version_id
                )
            )
            if candidate:
                candidate.state = ReviewState.REVOKED
                candidate.reviewer_id = reviewer_id
                candidate.decision_reason = reason[:1000]
                candidate.decided_at = datetime.now(timezone.utc)

    async def reject_import_candidate(
        self, candidate_id: str, reviewer_id: str, reason: str
    ) -> None:
        async with self._sessions.begin() as session:
            await _require_active_role(session, reviewer_id, "reviewer")
            candidate = await session.get(CapabilityImportCandidate, candidate_id)
            if candidate is None:
                raise LookupError("import candidate not found")
            if candidate.state is not ReviewState.AWAITING_REVIEW:
                raise ValueError("candidate is not awaiting review")
            candidate.state = ReviewState.REJECTED
            candidate.reviewer_id = reviewer_id
            candidate.decision_reason = reason[:1000]
            candidate.decided_at = datetime.now(timezone.utc)

    async def supersede_version(
        self, previous_version_id: str, replacement_version_id: str, reviewer_id: str
    ) -> None:
        async with self._sessions.begin() as session:
            await _require_active_role(session, reviewer_id, "reviewer")
            previous = await session.get(CapabilityVersionRecord, previous_version_id)
            replacement = await session.get(CapabilityVersionRecord, replacement_version_id)
            if (
                previous is None
                or replacement is None
                or replacement.review_state is not ReviewState.REVIEWED
                or previous.capability_id != replacement.capability_id
            ):
                raise ValueError("supersession requires reviewed versions of the same capability")
            previous.review_state = ReviewState.SUPERSEDED
            previous.archived_at = datetime.now(timezone.utc)
            candidate = await session.scalar(
                select(CapabilityImportCandidate).where(
                    CapabilityImportCandidate.version_id == previous_version_id
                )
            )
            if candidate:
                candidate.state = ReviewState.SUPERSEDED
                candidate.reviewer_id = reviewer_id

    async def append_audit(
        self, owner_id: str, event_type: str, subject_id: str, payload: dict[str, Any]
    ) -> CapabilityAuditEvent:
        redacted = _redact(payload)
        canonical = json.dumps(redacted, sort_keys=True, separators=(",", ":")).encode()
        async with self._sessions.begin() as session:
            event_record = CapabilityAuditEvent(
                owner_id=owner_id,
                event_type=event_type,
                subject_id=subject_id,
                payload=redacted,
                payload_digest=f"sha256:{hashlib.sha256(canonical).hexdigest()}",
            )
            session.add(event_record)
        return event_record

    async def create_service_connection(
        self,
        owner_id: str,
        provider: str,
        external_account_id: str,
        credential_reference: str,
        allowed_operations: list[str],
        immutable_targets: list[str],
        connection_id: str | None = None,
    ):
        from capabilities.models import CapabilityServiceConnection

        async with self._sessions.begin() as session:
            record = CapabilityServiceConnection(
                owner_id=owner_id,
                provider=provider,
                external_account_id=external_account_id,
                credential_reference=credential_reference,
                allowed_operations=sorted(set(allowed_operations)),
                immutable_targets=sorted(set(immutable_targets)),
            )
            if connection_id is not None:
                record.id = connection_id
            session.add(record)
        return record

    async def issue_oauth_state(
        self,
        owner_id: str,
        provider: str,
        nonce_digest: str,
        expires_at: datetime,
    ) -> None:
        async with self._sessions.begin() as session:
            session.add(
                CapabilityOAuthState(
                    nonce_digest=nonce_digest,
                    owner_id=owner_id,
                    provider=provider,
                    expires_at=expires_at,
                )
            )

    async def consume_oauth_state(
        self, owner_id: str, provider: str, nonce_digest: str
    ) -> None:
        now = datetime.now(timezone.utc)
        async with self._sessions.begin() as session:
            record = await session.get(CapabilityOAuthState, nonce_digest)
            expires_at = record.expires_at if record else None
            if expires_at is not None and expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if (
                record is None
                or record.owner_id != owner_id
                or record.provider != provider
                or record.consumed_at is not None
                or expires_at <= now
            ):
                raise PermissionError("OAuth state is invalid, expired, or already used")
            record.consumed_at = now

    async def get_service_connection(self, owner_id: str, connection_id: str):
        from capabilities.models import CapabilityServiceConnection

        async with self._sessions() as session:
            record = await session.get(CapabilityServiceConnection, connection_id)
            if record is None or record.owner_id != owner_id or record.revoked_at is not None:
                raise PermissionError("service connection not found for owner")
            return record

    async def list_service_connections(self, owner_id: str):
        from capabilities.models import CapabilityServiceConnection

        async with self._sessions() as session:
            values = await session.scalars(
                select(CapabilityServiceConnection)
                .where(CapabilityServiceConnection.owner_id == owner_id)
                .order_by(CapabilityServiceConnection.created_at.desc())
            )
            return list(values)

    async def revoke_service_connection(self, owner_id: str, connection_id: str) -> None:
        from capabilities.models import CapabilityServiceConnection

        async with self._sessions.begin() as session:
            record = await session.get(CapabilityServiceConnection, connection_id)
            if record is None or record.owner_id != owner_id:
                raise PermissionError("service connection not found for owner")
            record.revoked_at = datetime.now(timezone.utc)

    async def put_credential(
        self,
        owner_id: str,
        provider: str,
        connection_id: str,
        envelope: CredentialEnvelope,
    ) -> CapabilityCredentialRecord:
        """Persist only an authenticated envelope, never plaintext."""
        async with self._sessions.begin() as session:
            record = await session.scalar(
                select(CapabilityCredentialRecord).where(
                    CapabilityCredentialRecord.owner_id == owner_id,
                    CapabilityCredentialRecord.provider == provider,
                    CapabilityCredentialRecord.connection_id == connection_id,
                )
            )
            if record is None:
                record = CapabilityCredentialRecord(
                    owner_id=owner_id,
                    provider=provider,
                    connection_id=connection_id,
                    envelope=envelope.model_dump(mode="json"),
                    key_id=envelope.key_id,
                )
                session.add(record)
            else:
                record.envelope = envelope.model_dump(mode="json")
                record.key_id = envelope.key_id
                record.rotated_at = datetime.now(timezone.utc)
                record.revoked_at = None
        return record

    async def get_credential(
        self, owner_id: str, provider: str, connection_id: str
    ) -> CredentialEnvelope:
        async with self._sessions() as session:
            record = await session.scalar(
                select(CapabilityCredentialRecord).where(
                    CapabilityCredentialRecord.owner_id == owner_id,
                    CapabilityCredentialRecord.provider == provider,
                    CapabilityCredentialRecord.connection_id == connection_id,
                    CapabilityCredentialRecord.revoked_at.is_(None),
                )
            )
            if record is None:
                raise PermissionError("credential not found for owner or has been revoked")
            return CredentialEnvelope.model_validate(record.envelope)

    async def revoke_credential(self, owner_id: str, connection_id: str) -> None:
        async with self._sessions.begin() as session:
            record = await session.scalar(
                select(CapabilityCredentialRecord).where(
                    CapabilityCredentialRecord.owner_id == owner_id,
                    CapabilityCredentialRecord.connection_id == connection_id,
                )
            )
            if record is None:
                raise PermissionError("credential not found for owner")
            record.revoked_at = datetime.now(timezone.utc)

    async def update_audit(self, event_id: str, payload: dict[str, Any]) -> None:
        del event_id, payload
        raise ValueError("audit events are append-only")


def _redact(value: Any, key: str | None = None) -> Any:
    if key and key.casefold() in _SENSITIVE_KEYS:
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): _redact(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, bytes):
        return f"sha256:{hashlib.sha256(value).hexdigest()}"
    if isinstance(value, str) and len(value) > 1000:
        return f"sha256:{hashlib.sha256(value.encode()).hexdigest()}"
    return value


def _enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:
    del connection_record
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


async def _require_active_role(session, user_id: str, role: str) -> None:
    assignment = await session.scalar(
        select(CapabilityRoleAssignment.id).where(
            CapabilityRoleAssignment.user_id == user_id,
            CapabilityRoleAssignment.role == role,
            CapabilityRoleAssignment.revoked_at.is_(None),
        )
    )
    if assignment is None:
        raise PermissionError(f"active {role} role required")
