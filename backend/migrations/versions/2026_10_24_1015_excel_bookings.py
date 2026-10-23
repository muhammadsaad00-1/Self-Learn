"""XLSX staging, OCR source retention, CNIC digest and seat reservations.

Revision ID: 7ea0d2c48f15
Revises: 2d4aa2719b60
Create Date: 2026-10-24 10:15:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7ea0d2c48f15"
down_revision: str | Sequence[str] | None = "2d4aa2719b60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("import_batches", sa.Column("source_data", sa.LargeBinary(), nullable=True))
    op.add_column("import_batches", sa.Column("source_content_type", sa.String(100), nullable=True))
    op.drop_constraint(op.f("ck_import_batches_source_valid"), "import_batches", type_="check")
    op.create_check_constraint(
        op.f("ck_import_batches_source_valid"),
        "import_batches",
        "source IN ('manual', 'csv', 'xlsx', 'document', 'sample')",
    )
    op.add_column("ledger_accounts", sa.Column("cnic_digest", sa.String(64), nullable=True))
    op.execute(
        "GRANT UPDATE (customer_name, phone, username, email, cnic_digest) "
        "ON ledger_accounts TO agentflow_app"
    )
    op.create_table(
        "seat_bookings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("trip_id", sa.Uuid(), nullable=False),
        sa.Column("customer_ref", sa.String(100), nullable=False),
        sa.Column("booking_ref", sa.String(20), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("seats", sa.Integer(), nullable=False),
        sa.Column("unit_fare", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('held', 'confirmed', 'cancelled', 'expired')",
            name=op.f("ck_seat_bookings_status_valid"),
        ),
        sa.CheckConstraint("seats BETWEEN 1 AND 6", name=op.f("ck_seat_bookings_seats_range")),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_seat_bookings_org_id_organizations")
        ),
        sa.ForeignKeyConstraint(
            ["trip_id"], ["trips.id"], name=op.f("fk_seat_bookings_trip_id_trips")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_seat_bookings")),
        sa.UniqueConstraint(
            "org_id", "booking_ref", name=op.f("uq_seat_bookings_org_id_booking_ref")
        ),
        sa.UniqueConstraint(
            "org_id", "idempotency_key", name=op.f("uq_seat_bookings_org_id_idempotency_key")
        ),
    )
    op.create_index(
        "ix_seat_bookings_expiry",
        "seat_bookings",
        ["expires_at"],
        postgresql_where=sa.text("status = 'held'"),
    )
    op.create_index(
        "ix_seat_bookings_customer", "seat_bookings", ["org_id", "customer_ref", "created_at"]
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON seat_bookings TO agentflow_app")
    op.execute("ALTER TABLE seat_bookings ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation ON seat_bookings
        USING (org_id = app_current_org_id())
        WITH CHECK (org_id = app_current_org_id())
    """)
    op.execute("""
        CREATE FUNCTION expire_seat_holds()
        RETURNS TABLE(org_id uuid, booking_id uuid)
        LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
            WITH expired AS (
                SELECT id, org_id, trip_id, seats
                FROM public.seat_bookings
                WHERE status = 'held' AND expires_at < now()
                ORDER BY expires_at
                LIMIT 200
                FOR UPDATE SKIP LOCKED
            ), inventory AS (
                UPDATE public.trips t
                SET seats_available = least(coalesce(t.seats_total, 2147483647),
                                            t.seats_available + e.seats),
                    updated_at = now()
                FROM expired e
                WHERE t.id = e.trip_id AND t.org_id = e.org_id
                RETURNING e.id
            ), released AS (
                UPDATE public.seat_bookings b
                SET status = 'expired', expires_at = NULL, updated_at = now()
                FROM expired e
                WHERE b.id = e.id
                RETURNING b.org_id, b.id
            )
            SELECT released.org_id, released.id FROM released
        $$
    """)
    op.execute("REVOKE ALL ON FUNCTION expire_seat_holds() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION expire_seat_holds() TO agentflow_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION expire_seat_holds()")
    op.drop_table("seat_bookings")
    op.execute(
        "REVOKE UPDATE (customer_name, phone, username, email, cnic_digest) "
        "ON ledger_accounts FROM agentflow_app"
    )
    op.drop_column("ledger_accounts", "cnic_digest")
    op.drop_constraint(op.f("ck_import_batches_source_valid"), "import_batches", type_="check")
    op.create_check_constraint(
        op.f("ck_import_batches_source_valid"),
        "import_batches",
        "source IN ('manual', 'csv', 'document', 'sample')",
    )
    op.drop_column("import_batches", "source_content_type")
    op.drop_column("import_batches", "source_data")
