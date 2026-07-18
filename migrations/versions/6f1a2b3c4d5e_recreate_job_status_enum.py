"""recreate job_status_enum with simplified states

Revision ID: 6f1a2b3c4d5e
Revises: 5442c62a0005
Create Date: 2026-07-19 01:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "6f1a2b3c4d5e"
down_revision: Union[str, None] = "5442c62a0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Nearest valid successor for each removed value.
_REMAP = {
    "REFERRAL_IN_PROGRESS": "JD_PARSED",
    "WAITING_FOR_REFERRAL": "JD_PARSED",
    "RESUME_GENERATED": "APPLIED",
    "READY_TO_APPLY": "APPLIED",
    "WORKDAY_RUNNING": "APPLIED",
}

_NEW_VALUES = (
    "NEW",
    "JD_PARSED",
    "REFERRAL_RECEIVED",
    "REFERRAL_NOT_RECEIVED",
    "APPLIED",
    "OA",
    "INTERVIEW",
    "OFFER",
    "REJECTED",
    "WITHDRAWN",
)

_OLD_VALUES = (
    "NEW",
    "JD_PARSED",
    "REFERRAL_IN_PROGRESS",
    "WAITING_FOR_REFERRAL",
    "REFERRAL_RECEIVED",
    "RESUME_GENERATED",
    "READY_TO_APPLY",
    "WORKDAY_RUNNING",
    "APPLIED",
    "OA",
    "INTERVIEW",
    "OFFER",
    "REJECTED",
    "WITHDRAWN",
)


def upgrade() -> None:
    # 1. Remap any rows still using removed enum values to their
    #    nearest valid successor so the USING cast below cannot fail.
    for old, new in _REMAP.items():
        op.execute(
            sa.text(
                "UPDATE jobs SET status = :new WHERE status = :old"
            ).bindparams(new=new, old=old)
        )

    # 2. Recreate the enum: Postgres cannot drop a single value, so we
    #    rename the old type, create the new one, convert the column,
    #    then drop the old type.
    op.execute(
        sa.text(
            "ALTER TYPE job_status_enum RENAME TO job_status_enum_old"
        )
    )
    op.execute(
        sa.text(
            "CREATE TYPE job_status_enum AS ENUM ("
            + ", ".join(f"'{v}'" for v in _NEW_VALUES)
            + ")"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE jobs ALTER COLUMN status "
            "TYPE job_status_enum USING status::text::job_status_enum"
        )
    )
    op.execute(sa.text("DROP TYPE job_status_enum_old"))


def downgrade() -> None:
    # 1. Map the new-only value back to a valid old value.
    op.execute(
        sa.text(
            "UPDATE jobs SET status = 'JD_PARSED' WHERE status = 'REFERRAL_NOT_RECEIVED'"
        )
    )

    # 2. Recreate the original enum with all 14 historical values.
    op.execute(
        sa.text(
            "ALTER TYPE job_status_enum RENAME TO job_status_enum_old"
        )
    )
    op.execute(
        sa.text(
            "CREATE TYPE job_status_enum AS ENUM ("
            + ", ".join(f"'{v}'" for v in _OLD_VALUES)
            + ")"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE jobs ALTER COLUMN status "
            "TYPE job_status_enum USING status::text::job_status_enum"
        )
    )
    op.execute(sa.text("DROP TYPE job_status_enum_old"))
