"""Add pgvector support and RAG observability events

Revision ID: f4d91c2e7b11
Revises: e12b47c8a8f1
Create Date: 2026-04-20 09:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f4d91c2e7b11"
down_revision = "e12b47c8a8f1"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        op.execute(
            "ALTER TABLE teacher_document_chunks "
            "ADD COLUMN IF NOT EXISTS embedding_vector_pg vector(1536)"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_teacher_document_chunks_embedding_vector_pg "
            "ON teacher_document_chunks USING ivfflat "
            "(embedding_vector_pg vector_cosine_ops) WITH (lists = 100)"
        )
    else:
        with op.batch_alter_table("teacher_document_chunks") as batch:
            batch.add_column(sa.Column("embedding_vector_pg", sa.Text(), nullable=True))

    op.create_table(
        "rag_retrieval_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("teacher_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("test_id", sa.Integer(), sa.ForeignKey("tests.id", ondelete="SET NULL"), nullable=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("teacher_documents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("generation_mode_requested", sa.String(length=32), nullable=False, server_default="standard"),
        sa.Column("generation_mode_effective", sa.String(length=32), nullable=False, server_default="standard"),
        sa.Column("vector_store_requested", sa.String(length=32), nullable=True),
        sa.Column("vector_store_effective", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="success"),
        sa.Column("fallback_reason", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("selected_doc_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("candidate_chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retrieval_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("avg_similarity", sa.Float(), nullable=False, server_default="0"),
        sa.Column("max_similarity", sa.Float(), nullable=False, server_default="0"),
        sa.Column("provenance_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("coverage", sa.Float(), nullable=False, server_default="0"),
        sa.Column("relevance", sa.Float(), nullable=False, server_default="0"),
        sa.Column("duplication", sa.Float(), nullable=False, server_default="0"),
        sa.Column("requested_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("generated_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("service_latency_ms", sa.Float(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_index("ix_rag_retrieval_events_teacher_id", "rag_retrieval_events", ["teacher_id"])
    op.create_index("ix_rag_retrieval_events_test_id", "rag_retrieval_events", ["test_id"])
    op.create_index("ix_rag_retrieval_events_document_id", "rag_retrieval_events", ["document_id"])
    op.create_index("ix_rag_retrieval_events_created_at", "rag_retrieval_events", ["created_at"])
    op.create_index("ix_rag_retrieval_events_status", "rag_retrieval_events", ["status"])
    op.create_index("ix_rag_retrieval_events_fallback_reason", "rag_retrieval_events", ["fallback_reason"])
    op.create_index("ix_rag_retrieval_events_generation_mode_requested", "rag_retrieval_events", ["generation_mode_requested"])
    op.create_index("ix_rag_retrieval_events_generation_mode_effective", "rag_retrieval_events", ["generation_mode_effective"])
    op.create_index("ix_rag_retrieval_events_vector_store_effective", "rag_retrieval_events", ["vector_store_effective"])


def downgrade():
    op.drop_index("ix_rag_retrieval_events_vector_store_effective", table_name="rag_retrieval_events")
    op.drop_index("ix_rag_retrieval_events_generation_mode_effective", table_name="rag_retrieval_events")
    op.drop_index("ix_rag_retrieval_events_generation_mode_requested", table_name="rag_retrieval_events")
    op.drop_index("ix_rag_retrieval_events_fallback_reason", table_name="rag_retrieval_events")
    op.drop_index("ix_rag_retrieval_events_status", table_name="rag_retrieval_events")
    op.drop_index("ix_rag_retrieval_events_created_at", table_name="rag_retrieval_events")
    op.drop_index("ix_rag_retrieval_events_document_id", table_name="rag_retrieval_events")
    op.drop_index("ix_rag_retrieval_events_test_id", table_name="rag_retrieval_events")
    op.drop_index("ix_rag_retrieval_events_teacher_id", table_name="rag_retrieval_events")
    op.drop_table("rag_retrieval_events")

    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_teacher_document_chunks_embedding_vector_pg")
        op.execute("ALTER TABLE teacher_document_chunks DROP COLUMN IF EXISTS embedding_vector_pg")
    else:
        with op.batch_alter_table("teacher_document_chunks") as batch:
            batch.drop_column("embedding_vector_pg")
