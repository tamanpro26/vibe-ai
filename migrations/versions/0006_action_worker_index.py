"""Index stale action leases for bounded worker recovery."""

from alembic import op

revision = "0006_action_worker_index"
down_revision = "0005_action_broker"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_capability_action_stale_lease",
        "capability_action_requests",
        ["status", "lease_expires_at"],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_capability_action_stale_lease",
        table_name="capability_action_requests",
        if_exists=True,
    )
