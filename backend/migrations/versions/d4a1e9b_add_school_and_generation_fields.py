"""add school table and question generation fields

Revision ID: d4a1e9b_add_school_and_generation_fields
Revises: c3d5e6a_add_syllabus_topic_table
Create Date: 2026-01-11 18:30:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'd4a1e9b'
down_revision = 'c3d5e6a'
branch_labels = None
depends_on = None


def upgrade():
    # Create schools table
    op.create_table(
        'schools',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('slug', sa.String(length=128), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('name')
    )
    op.create_index(op.f('ix_schools_name'), 'schools', ['name'], unique=False)
    op.create_index(op.f('ix_schools_slug'), 'schools', ['slug'], unique=False)

    # Add role and school_id to users
    op.add_column('users', sa.Column('role', sa.String(length=32), nullable=False, server_default='student'))
    op.add_column('users', sa.Column('school_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_users_role'), 'users', ['role'], unique=False)
    op.create_index(op.f('ix_users_school_id'), 'users', ['school_id'], unique=False)
    op.create_foreign_key('fk_users_school_id_schools', 'users', 'schools', ['school_id'], ['id'], ondelete='SET NULL')

    # Add generation fields to questions
    op.add_column('questions', sa.Column('is_generated', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.add_column('questions', sa.Column('generated_by', sa.Integer(), nullable=True))
    op.add_column('questions', sa.Column('generation_meta', sa.JSON(), nullable=True))
    op.create_index(op.f('ix_questions_is_generated'), 'questions', ['is_generated'], unique=False)
    op.create_index(op.f('ix_questions_generated_by'), 'questions', ['generated_by'], unique=False)
    op.create_foreign_key('fk_questions_generated_by_users', 'questions', 'users', ['generated_by'], ['id'], ondelete='SET NULL')


def downgrade():
    op.drop_constraint('fk_questions_generated_by_users', 'questions', type_='foreignkey')
    op.drop_index(op.f('ix_questions_generated_by'), table_name='questions')
    op.drop_index(op.f('ix_questions_is_generated'), table_name='questions')
    op.drop_column('questions', 'generation_meta')
    op.drop_column('questions', 'generated_by')
    op.drop_column('questions', 'is_generated')

    op.drop_constraint('fk_users_school_id_schools', 'users', type_='foreignkey')
    op.drop_index(op.f('ix_users_school_id'), table_name='users')
    op.drop_index(op.f('ix_users_role'), table_name='users')
    op.drop_column('users', 'school_id')
    op.drop_column('users', 'role')

    op.drop_index(op.f('ix_schools_slug'), table_name='schools')
    op.drop_index(op.f('ix_schools_name'), table_name='schools')
    op.drop_table('schools')
