"""Add immutable import candidate and review evidence lifecycle."""

from alembic import op

from capabilities.models import CapabilityImportCandidate

revision = "0003_import_review"
down_revision = "0002_identity_ownership"
branch_labels = None
depends_on = None


def upgrade() -> None:
    CapabilityImportCandidate.__table__.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    CapabilityImportCandidate.__table__.drop(bind=op.get_bind(), checkfirst=True)
