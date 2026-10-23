"""data imports and trips

Import batches and their staged rows (reviewed by people before commit), and transport
trips. Tenant data: RLS + least-privilege grants. Staged rows can be edited and deleted
(they are a draft); trips are updated by imports, never deleted by the app. Extraction of
reports is a background job claimed through a SECURITY DEFINER function, like workflows
and document ingestion.

Revision ID: 2aa46e6917d0
Revises: a8be8b74cb7f
Create Date: 2026-10-23 21:47:05.807983

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "2aa46e6917d0"
down_revision: str | Sequence[str] | None = "a8be8b74cb7f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "import_batches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("record_type", sa.String(length=32), nullable=False),
        sa.Column(
            "source",
            sa.Enum(
                "manual",
                "csv",
                "document",
                "sample",
                name="batchsource",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("filename", sa.String(length=300), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "extracting",
                "review",
                "committed",
                "discarded",
                "failed",
                name="batchstatus",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("source_text", sa.Text(), nullable=True),
        sa.Column("notes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("committed_by", sa.Uuid(), nullable=True),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('manual', 'csv', 'document', 'sample')",
            name=op.f("ck_import_batches_source_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('extracting', 'review', 'committed', 'discarded', 'failed')",
            name=op.f("ck_import_batches_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_import_batches_org_id_organizations")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_batches")),
    )
    op.create_index(
        "ix_import_batches_queue",
        "import_batches",
        ["available_at"],
        unique=False,
        postgresql_where=sa.text("status = 'extracting'"),
    )
    op.create_table(
        "trips",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("trip_ref", sa.String(length=50), nullable=False),
        sa.Column("origin", sa.String(length=100), nullable=False),
        sa.Column("destination", sa.String(length=100), nullable=False),
        sa.Column("departs_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("arrives_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fare", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("seats_total", sa.Integer(), nullable=True),
        sa.Column("seats_available", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "scheduled",
                "delayed",
                "cancelled",
                "departed",
                name="tripstatus",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("vehicle", sa.String(length=100), nullable=True),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('scheduled', 'delayed', 'cancelled', 'departed')",
            name=op.f("ck_trips_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_trips_org_id_organizations")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trips")),
        sa.UniqueConstraint("org_id", "trip_ref", name=op.f("uq_trips_org_id_trip_ref")),
    )
    op.create_index("ix_trips_org_id_departs_at", "trips", ["org_id", "departs_at"], unique=False)
    op.create_table(
        "import_rows",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("errors", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["import_batches.id"],
            name=op.f("fk_import_rows_batch_id_import_batches"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_import_rows_org_id_organizations")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_rows")),
    )
    op.create_index(
        "ix_import_rows_batch_id_position", "import_rows", ["batch_id", "position"], unique=False
    )
    for table, privileges in {
        "import_batches": "SELECT, INSERT, UPDATE",
        "import_rows": "SELECT, INSERT, UPDATE, DELETE",  # a draft people edit
        "trips": "SELECT, INSERT, UPDATE",
    }.items():
        op.execute(f"GRANT {privileges} ON {table} TO agentflow_app")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (org_id = app_current_org_id())
            WITH CHECK (org_id = app_current_org_id())
        """)
    op.execute("""
        CREATE FUNCTION claim_next_import(max_attempts int, lease_seconds int)
        RETURNS TABLE (claimed_id uuid, claimed_org_id uuid, claimed_attempt int)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        BEGIN
            UPDATE import_batches b
            SET status = 'failed', error = 'worker lease expired on the final attempt',
                locked_until = NULL, updated_at = now()
            WHERE b.status = 'extracting' AND b.locked_until < now()
              AND b.attempts >= max_attempts;

            RETURN QUERY
            UPDATE import_batches b
            SET attempts = b.attempts + 1,
                locked_until = now() + make_interval(secs => lease_seconds), updated_at = now()
            WHERE b.id = (
                SELECT c.id FROM import_batches c
                WHERE c.status = 'extracting' AND c.available_at <= now()
                  AND c.attempts < max_attempts
                  AND (c.locked_until IS NULL OR c.locked_until < now())
                ORDER BY c.available_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING b.id, b.org_id, b.attempts;
        END
        $$
    """)
    op.execute("REVOKE ALL ON FUNCTION claim_next_import(int, int) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION claim_next_import(int, int) TO agentflow_app")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP FUNCTION claim_next_import(int, int)")
    op.drop_index("ix_import_rows_batch_id_position", table_name="import_rows")
    op.drop_table("import_rows")
    op.drop_index("ix_trips_org_id_departs_at", table_name="trips")
    op.drop_table("trips")
    op.drop_index(
        "ix_import_batches_queue",
        table_name="import_batches",
        postgresql_where=sa.text("status = 'extracting'"),
    )
    op.drop_table("import_batches")
