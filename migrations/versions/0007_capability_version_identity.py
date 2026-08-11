"""Make semantic capability versions unambiguous per owner."""

from alembic import op
from sqlalchemy import inspect

revision = "0007_capability_version_identity"
down_revision = "0006_action_worker_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    names = {
        item.get("name")
        for item in inspect(op.get_bind()).get_unique_constraints("capability_versions")
    }
    if "uq_capability_version_identity" in names:
        return
    with op.batch_alter_table("capability_versions") as batch:
        if "uq_capability_version_digest" in names:
            batch.drop_constraint("uq_capability_version_digest", type_="unique")
        batch.create_unique_constraint(
            "uq_capability_version_identity", ["owner_id", "capability_id", "version"]
        )


def downgrade() -> None:
    names = {
        item.get("name")
        for item in inspect(op.get_bind()).get_unique_constraints("capability_versions")
    }
    if "uq_capability_version_digest" in names:
        return
    with op.batch_alter_table("capability_versions") as batch:
        if "uq_capability_version_identity" in names:
            batch.drop_constraint("uq_capability_version_identity", type_="unique")
        batch.create_unique_constraint(
            "uq_capability_version_digest",
            ["owner_id", "capability_id", "version", "content_digest", "source_digest"],
        )
