"""nvr max live streams

Revision ID: 9c1f4a7b2d10
Revises: 2413b3084362
Create Date: 2026-10-22 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9c1f4a7b2d10"
down_revision: str | Sequence[str] | None = "2413b3084362"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("nvrs", sa.Column("max_live_streams", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("nvrs", "max_live_streams")
