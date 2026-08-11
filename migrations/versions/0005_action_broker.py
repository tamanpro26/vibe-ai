"""Add exact action requests, outbox, and execution receipts."""

from alembic import op

from capabilities.models import (
    CapabilityActionExecution,
    CapabilityActionOutbox,
    CapabilityActionRequest,
)

revision = "0005_action_broker"
down_revision = "0004_resolution_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    CapabilityActionRequest.__table__.create(bind=op.get_bind(), checkfirst=True)
    CapabilityActionOutbox.__table__.create(bind=op.get_bind(), checkfirst=True)
    CapabilityActionExecution.__table__.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    CapabilityActionExecution.__table__.drop(bind=op.get_bind(), checkfirst=True)
    CapabilityActionOutbox.__table__.drop(bind=op.get_bind(), checkfirst=True)
    CapabilityActionRequest.__table__.drop(bind=op.get_bind(), checkfirst=True)
