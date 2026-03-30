"""Add syllabus_topic and readability_level to questions

Revision ID: b2f9c1a1
Revises: 9a16bcc274bb
Create Date: 2026-01-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2f9c1a1'
down_revision: Union[str, Sequence[str], None] = '9a16bcc274bb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('questions', sa.Column('syllabus_topic', sa.String(length=128), nullable=True))
    op.create_index(op.f('ix_questions_syllabus_topic'), 'questions', ['syllabus_topic'], unique=False)
    op.add_column('questions', sa.Column('readability_level', sa.String(length=32), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('questions', 'readability_level')
    op.drop_index(op.f('ix_questions_syllabus_topic'), table_name='questions')
    op.drop_column('questions', 'syllabus_topic'
)
