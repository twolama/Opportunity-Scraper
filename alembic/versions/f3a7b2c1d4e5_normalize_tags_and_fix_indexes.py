"""normalize_tags_and_fix_indexes

Revision ID: f3a7b2c1d4e5
Revises: e8d62ed31231
Create Date: 2026-06-26 04:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f3a7b2c1d4e5"
down_revision: Union[str, Sequence[str], None] = "e8d62ed31231"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Create tags table (if not already exists)
    op.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            id SERIAL NOT NULL,
            name VARCHAR NOT NULL,
            PRIMARY KEY (id),
            UNIQUE (name)
        )
    """)

    # Create opportunity_tags join table (if not already exists)
    op.execute("""
        CREATE TABLE IF NOT EXISTS opportunity_tags (
            opportunity_id INTEGER NOT NULL,
            tag_id INTEGER NOT NULL,
            PRIMARY KEY (opportunity_id, tag_id),
            FOREIGN KEY (opportunity_id) REFERENCES opportunities(id) ON DELETE CASCADE,
            FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
        )
    """)

    # Migrate existing comma-separated tags into normalized form
    rows = conn.execute(
        sa.text("SELECT id, tags FROM opportunities WHERE tags IS NOT NULL AND tags != ''")
    ).fetchall()
    for opp_id, tags_str in rows:
        for tag_name in tags_str.split(", "):
            tag_name = tag_name.strip()
            if not tag_name:
                continue
            existing = conn.execute(
                sa.text("SELECT id FROM tags WHERE name = :name"), {"name": tag_name}
            ).fetchone()
            if existing:
                tag_id = existing[0]
            else:
                result = conn.execute(
                    sa.text("INSERT INTO tags (name) VALUES (:name) RETURNING id"),
                    {"name": tag_name},
                )
                tag_id = result.scalar()
            conn.execute(
                sa.text("INSERT INTO opportunity_tags (opportunity_id, tag_id) VALUES (:oid, :tid) ON CONFLICT DO NOTHING"),
                {"oid": opp_id, "tid": tag_id},
            )

    # Drop old separate indexes (if they exist)
    op.execute("DROP INDEX IF EXISTS idx_created_at")
    op.execute("DROP INDEX IF EXISTS idx_posted_to_telegram")

    # Create new composite index + tags index (if they don't already exist)
    op.execute("CREATE INDEX IF NOT EXISTS idx_posted_created ON opportunities (posted_to_telegram, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_tags ON opportunities (tags)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_tags")
    op.execute("DROP INDEX IF EXISTS idx_posted_created")
    op.execute("CREATE INDEX IF NOT EXISTS idx_created_at ON opportunities (created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_posted_to_telegram ON opportunities (posted_to_telegram)")
    op.execute("DROP TABLE IF EXISTS opportunity_tags")
    op.execute("DROP TABLE IF EXISTS tags")
