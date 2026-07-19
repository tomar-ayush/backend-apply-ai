"""split llm_api_key into per-provider encrypted columns

Revision ID: 7a2c1d4e5f60
Revises: 6f1a2b3c4d5e
Create Date: 2026-07-19 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "7a2c1d4e5f60"
down_revision: Union[str, None] = "6f1a2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("openrouter_llm_api_key", sa.Text(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("openai_llm_api_key", sa.Text(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("gemini_llm_api_key", sa.Text(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("claude_llm_api_key", sa.Text(), nullable=True),
    )
    # The single shared key is replaced by the four provider-specific columns.
    op.drop_column("users", "encrypted_llm_api_key")


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column("encrypted_llm_api_key", sa.Text(), nullable=True),
    )
    op.drop_column("users", "claude_llm_api_key")
    op.drop_column("users", "gemini_llm_api_key")
    op.drop_column("users", "openai_llm_api_key")
    op.drop_column("users", "openrouter_llm_api_key")
