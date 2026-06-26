"""add_channels_table

Revision ID: a1b2c3d4e5f6
Revises: f3a7b2c1d4e5
Create Date: 2026-06-26 17:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "f3a7b2c1d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            chat_id BIGINT NOT NULL,
            title VARCHAR DEFAULT '',
            added_by BIGINT,
            created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
            is_active BOOLEAN DEFAULT TRUE,
            PRIMARY KEY (chat_id)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS channels")
