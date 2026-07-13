"""job_jd update team_signal type

Revision ID: c037798e8b1d
Revises: 96d009568658
Create Date: 2026-07-14 00:40:40.478338

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c037798e8b1d"
down_revision: Union[str, None] = "96d009568658"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "job_jds",
        "team_signals",
        existing_type=postgresql.JSON(astext_type=sa.Text()),
        type_=sa.ARRAY(sa.String()),
        existing_nullable=True,
        postgresql_using="ARRAY[]::varchar[]",
    )


def downgrade() -> None:
    op.alter_column(
        "job_jds",
        "team_signals",
        existing_type=sa.ARRAY(sa.String()),
        type_=postgresql.JSON(astext_type=sa.Text()),
        existing_nullable=True,
        postgresql_using="NULL::json",
    )
