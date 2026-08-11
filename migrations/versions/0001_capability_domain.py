"""Create the canonical Capability Hub domain."""

from alembic import op

from capabilities.models import (
    CapabilityActivationPreference,
    CapabilityAuditEvent,
    CapabilityAuthorDraft,
    CapabilityCredentialRecord,
    CapabilityExecutionLease,
    CapabilityInstallation,
    CapabilityReviewEvidence,
    CapabilityScopeOverride,
    CapabilityServiceConnection,
    CapabilityVersionRecord,
    CapabilityWorkflowCheckpoint,
)

revision = "0001_capability_domain"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for table in (
        CapabilityAuthorDraft.__table__,
        CapabilityVersionRecord.__table__,
        CapabilityInstallation.__table__,
        CapabilityScopeOverride.__table__,
        CapabilityActivationPreference.__table__,
        CapabilityReviewEvidence.__table__,
        CapabilityServiceConnection.__table__,
        CapabilityCredentialRecord.__table__,
        CapabilityWorkflowCheckpoint.__table__,
        CapabilityExecutionLease.__table__,
        CapabilityAuditEvent.__table__,
    ):
        table.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed((
        CapabilityAuthorDraft.__table__,
        CapabilityVersionRecord.__table__,
        CapabilityInstallation.__table__,
        CapabilityScopeOverride.__table__,
        CapabilityActivationPreference.__table__,
        CapabilityReviewEvidence.__table__,
        CapabilityServiceConnection.__table__,
        CapabilityCredentialRecord.__table__,
        CapabilityWorkflowCheckpoint.__table__,
        CapabilityExecutionLease.__table__,
        CapabilityAuditEvent.__table__,
    )):
        table.drop(bind=bind, checkfirst=True)
