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
    CapabilityScopeOverride,
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
