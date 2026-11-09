"""Add password-based authentication (User credentials + refresh tokens).

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0001 builds its schema from the live `Base.metadata`, so on a database that has
    # never been migrated before, `alembic upgrade head` replays 0001 with the models
    # as they look *today* - which already includes the columns/table this revision
    # adds. Guard every change on the inspected schema so this revision is a correct
    # no-op in that case, and a real ALTER on a database that only has the 0001 shape.
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    user_columns = {column["name"] for column in inspector.get_columns("users")}
    if "password_hash" not in user_columns:
        with op.batch_alter_table("users") as batch_op:
            batch_op.add_column(sa.Column("password_hash", sa.String(255), nullable=True))
            batch_op.add_column(
                sa.Column("failed_login_count", sa.Integer(), nullable=False, server_default="0")
            )
            batch_op.add_column(
                sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True)
            )

    email_already_unique = any(
        set(constraint["column_names"]) == {"email"}
        for constraint in inspector.get_unique_constraints("users")
    ) or any(
        index["unique"] and set(index["column_names"]) == {"email"}
        for index in inspector.get_indexes("users")
    )
    if not email_already_unique:
        with op.batch_alter_table("users") as batch_op:
            batch_op.create_unique_constraint("uq_users_email", ["email"])

    if "refresh_tokens" not in inspector.get_table_names():
        op.create_table(
            "refresh_tokens",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "user_id",
                sa.String(64),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
        op.create_index("ix_refresh_tokens_token_hash", "refresh_tokens", ["token_hash"])


def downgrade() -> None:
    op.drop_index("ix_refresh_tokens_token_hash", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_user_id", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("uq_users_email", type_="unique")
        batch_op.drop_column("locked_until")
        batch_op.drop_column("failed_login_count")
        batch_op.drop_column("password_hash")
