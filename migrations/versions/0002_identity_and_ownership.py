"""Add server-authoritative scope ownership records."""

from alembic import op

from capabilities.models import (
    CapabilityOAuthState,
    CapabilityOwnedScope,
    CapabilityRoleAssignment,
    CapabilitySystemState,
)

revision = "0002_identity_ownership"
down_revision = "0001_capability_domain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    CapabilityOwnedScope.__table__.create(bind=op.get_bind(), checkfirst=True)
    CapabilityOAuthState.__table__.create(bind=op.get_bind(), checkfirst=True)
    CapabilityRoleAssignment.__table__.create(bind=op.get_bind(), checkfirst=True)
    CapabilitySystemState.__table__.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    CapabilitySystemState.__table__.drop(bind=op.get_bind(), checkfirst=True)
    CapabilityRoleAssignment.__table__.drop(bind=op.get_bind(), checkfirst=True)
    CapabilityOAuthState.__table__.drop(bind=op.get_bind(), checkfirst=True)
    CapabilityOwnedScope.__table__.drop(bind=op.get_bind(), checkfirst=True)
