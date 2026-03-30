"""Create syllabus_topics table

Revision ID: c3d5e6a
Revises: b2f9c1a1
Create Date: 2026-01-10 00:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d5e6a'
down_revision: Union[str, Sequence[str], None] = 'b2f9c1a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'syllabus_topics',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('subject', sa.String(length=64), nullable=False),
        sa.Column('grade', sa.String(length=32), nullable=True),
        sa.Column('slug', sa.String(length=128), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('slug')
    )
    op.create_index(op.f('ix_syllabus_topics_subject'), 'syllabus_topics', ['subject'], unique=False)
    op.create_index(op.f('ix_syllabus_topics_grade'), 'syllabus_topics', ['grade'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_syllabus_topics_grade'), table_name='syllabus_topics')
    op.drop_index(op.f('ix_syllabus_topics_subject'), table_name='syllabus_topics')
    op.drop_table('syllabus_topics')
