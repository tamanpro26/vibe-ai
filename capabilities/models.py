"""SQLAlchemy persistence model for the canonical capability domain."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from capabilities.manifests import ActivationMode


def _id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class DraftState(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class ReviewState(StrEnum):
    DISCOVERED = "discovered"
    QUARANTINED = "quarantined"
    SCAN_FAILED = "scan_failed"
    AWAITING_REVIEW = "awaiting_review"
    REVIEWED = "reviewed"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    REVOKED = "revoked"


class ScopeKind(StrEnum):
    ACCOUNT = "account"
    PROJECT = "project"
    CHAT = "chat"


class ScopeState(StrEnum):
    INHERIT = "inherit"
    ENABLED = "enabled"
    DISABLED = "disabled"


class CapabilityAuthorDraft(Base):
    __tablename__ = "capability_author_drafts"
    __table_args__ = (Index("ix_capability_drafts_owner_updated", "owner_id", "updated_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    state: Mapped[DraftState] = mapped_column(
        SAEnum(DraftState, native_enum=False, values_callable=lambda cls: [item.value for item in cls]),
        default=DraftState.DRAFT,
        nullable=False,
    )
    source_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )


class CapabilityVersionRecord(Base):
    __tablename__ = "capability_versions"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "capability_id", "version", "content_digest", "source_digest",
            name="uq_capability_version_digest",
        ),
        Index("ix_capability_versions_resolvable", "owner_id", "archived_at", "revoked_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    capability_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    trust: Mapped[str] = mapped_column(String(40), nullable=False)
    review_state: Mapped[ReviewState] = mapped_column(
        SAEnum(ReviewState, native_enum=False, values_callable=lambda cls: [item.value for item in cls]),
        default=ReviewState.REVIEWED,
        nullable=False,
    )
    content_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source_manifest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    source_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    source_provider: Mapped[str] = mapped_column(String(80), default="vibeai", nullable=False)
    source_revision: Mapped[str | None] = mapped_column(String(255), nullable=True)
    adapter_version: Mapped[str] = mapped_column(String(40), default="native-v1", nullable=False)
    compatibility_report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CapabilityInstallation(Base):
    __tablename__ = "capability_installations"
    __table_args__ = (
        UniqueConstraint("owner_id", "capability_version_id", name="uq_capability_installation_owner_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    capability_version_id: Mapped[str] = mapped_column(
        ForeignKey("capability_versions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    uninstalled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CapabilityScopeOverride(Base):
    __tablename__ = "capability_scope_overrides"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "installation_id", "scope_kind", "scope_id", name="uq_capability_scope_target"
        ),
        Index("ix_capability_scope_lookup", "owner_id", "scope_kind", "scope_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    installation_id: Mapped[str] = mapped_column(
        ForeignKey("capability_installations.id", ondelete="CASCADE"), nullable=False
    )
    scope_kind: Mapped[ScopeKind] = mapped_column(
        SAEnum(ScopeKind, native_enum=False, values_callable=lambda cls: [item.value for item in cls]),
        nullable=False,
    )
    scope_id: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[ScopeState] = mapped_column(
        SAEnum(ScopeState, native_enum=False, values_callable=lambda cls: [item.value for item in cls]),
        default=ScopeState.INHERIT,
        nullable=False,
    )
    configuration_patch: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )


class CapabilitySuggestionDecision(Base):
    __tablename__ = "capability_suggestion_decisions"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "chat_id", "request_id", "capability_id",
            name="uq_capability_suggestion_decision",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    chat_id: Mapped[str] = mapped_column(String(255), nullable=False)
    request_id: Mapped[str] = mapped_column(String(255), nullable=False)
    capability_id: Mapped[str] = mapped_column(String(160), nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class CapabilityResolutionRecord(Base):
    __tablename__ = "capability_resolution_records"
    __table_args__ = (
        UniqueConstraint("owner_id", "snapshot_id", name="uq_capability_resolution_snapshot"),
        Index("ix_capability_resolution_scope", "owner_id", "project_id", "chat_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    project_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    chat_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    snapshot_id: Mapped[str] = mapped_column(String(80), nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class CapabilityActivationPreference(Base):
    __tablename__ = "capability_activation_preferences"

    owner_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    mode: Mapped[ActivationMode] = mapped_column(
        SAEnum(ActivationMode, native_enum=False, values_callable=lambda cls: [item.value for item in cls]),
        default=ActivationMode.MANUAL_ONLY,
        nullable=False,
    )
    onboarding_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )


class CapabilityOwnedScope(Base):
    __tablename__ = "capability_owned_scopes"
    __table_args__ = (
        UniqueConstraint("owner_id", "scope_kind", "scope_id", name="uq_capability_owned_scope"),
        Index("ix_capability_owned_scope_parent", "owner_id", "parent_scope_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    scope_kind: Mapped[ScopeKind] = mapped_column(
        SAEnum(ScopeKind, native_enum=False, values_callable=lambda cls: [item.value for item in cls]),
        nullable=False,
    )
    scope_id: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_scope_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CapabilityOAuthState(Base):
    __tablename__ = "capability_oauth_states"

    nonce_digest: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class CapabilityRoleAssignment(Base):
    __tablename__ = "capability_role_assignments"
    __table_args__ = (
        UniqueConstraint("user_id", "role", name="uq_capability_role_assignment"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    granted_by: Mapped[str] = mapped_column(String(255), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CapabilitySystemState(Base):
    __tablename__ = "capability_system_state"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )


class CapabilityReviewEvidence(Base):
    __tablename__ = "capability_review_evidence"
    __table_args__ = (
        UniqueConstraint("capability_version_id", "evidence_digest", name="uq_capability_review_evidence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    capability_version_id: Mapped[str] = mapped_column(
        ForeignKey("capability_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    evidence_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    reviewer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decision: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class CapabilityImportCandidate(Base):
    __tablename__ = "capability_import_candidates"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "repository", "commit_sha", "source_digest",
            name="uq_capability_import_candidate",
        ),
        Index("ix_capability_import_state", "state", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    repository: Mapped[str] = mapped_column(String(255), nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    source_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(80), nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    compatibility_report: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    state: Mapped[ReviewState] = mapped_column(
        SAEnum(ReviewState, native_enum=False, values_callable=lambda cls: [item.value for item in cls]),
        default=ReviewState.AWAITING_REVIEW,
        nullable=False,
    )
    version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reviewer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CapabilityServiceConnection(Base):
    __tablename__ = "capability_service_connections"
    __table_args__ = (
        UniqueConstraint("owner_id", "provider", "external_account_id", name="uq_service_connection_owner"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    external_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    credential_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    allowed_operations: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    immutable_targets: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class CapabilityCredentialRecord(Base):
    __tablename__ = "capability_credentials"
    __table_args__ = (
        UniqueConstraint("owner_id", "provider", "connection_id", name="uq_capability_credential_binding"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    connection_id: Mapped[str] = mapped_column(String(36), nullable=False)
    envelope: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    key_id: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CapabilityWorkflowCheckpoint(Base):
    __tablename__ = "capability_workflow_checkpoints"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    job_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    checkpoint_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )


class CapabilityExecutionLease(Base):
    __tablename__ = "capability_execution_leases"

    resource_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    worker_id: Mapped[str] = mapped_column(String(255), nullable=False)
    lease_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    send_attempted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class CapabilityAuditEvent(Base):
    __tablename__ = "capability_audit_events"
    __table_args__ = (Index("ix_capability_audit_owner_time", "owner_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


__all__ = [
    "ActivationMode",
    "Base",
    "CapabilityActivationPreference",
    "CapabilityAuditEvent",
    "CapabilityAuthorDraft",
    "CapabilityCredentialRecord",
    "CapabilityExecutionLease",
    "CapabilityInstallation",
    "CapabilityImportCandidate",
    "CapabilityOwnedScope",
    "CapabilityOAuthState",
    "CapabilityRoleAssignment",
    "CapabilityReviewEvidence",
    "CapabilityResolutionRecord",
    "CapabilityScopeOverride",
    "CapabilitySuggestionDecision",
    "CapabilityServiceConnection",
    "CapabilitySystemState",
    "CapabilityVersionRecord",
    "CapabilityWorkflowCheckpoint",
    "DraftState",
    "ReviewState",
    "ScopeKind",
    "ScopeState",
]
