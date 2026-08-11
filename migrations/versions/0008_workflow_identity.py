"""Guarantee one durable checkpoint per owner workflow."""

from alembic import op
from sqlalchemy import inspect

revision = "0008_workflow_identity"
down_revision = "0007_capability_version_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    names = {
        item.get("name")
        for item in inspect(op.get_bind()).get_unique_constraints(
            "capability_workflow_checkpoints"
        )
    }
    if "uq_capability_workflow_owner_job" in names:
        return
    with op.batch_alter_table("capability_workflow_checkpoints") as batch:
        batch.create_unique_constraint(
            "uq_capability_workflow_owner_job", ["owner_id", "job_id"]
        )


def downgrade() -> None:
    names = {
        item.get("name")
        for item in inspect(op.get_bind()).get_unique_constraints(
            "capability_workflow_checkpoints"
        )
    }
    if "uq_capability_workflow_owner_job" not in names:
        return
    with op.batch_alter_table("capability_workflow_checkpoints") as batch:
        batch.drop_constraint("uq_capability_workflow_owner_job", type_="unique")
