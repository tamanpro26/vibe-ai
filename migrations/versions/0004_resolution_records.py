"""Add deterministic resolution snapshots and chat suggestion decisions."""

from alembic import op

from capabilities.models import CapabilityResolutionRecord, CapabilitySuggestionDecision

revision = "0004_resolution_records"
down_revision = "0003_import_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    CapabilitySuggestionDecision.__table__.create(bind=op.get_bind(), checkfirst=True)
    CapabilityResolutionRecord.__table__.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    CapabilityResolutionRecord.__table__.drop(bind=op.get_bind(), checkfirst=True)
    CapabilitySuggestionDecision.__table__.drop(bind=op.get_bind(), checkfirst=True)
