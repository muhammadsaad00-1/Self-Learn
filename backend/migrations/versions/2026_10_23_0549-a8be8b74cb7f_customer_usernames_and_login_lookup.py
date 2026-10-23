"""customer usernames and login lookup

Customers can sign in without their bank's link: phone + username identify them. Finding
which bank(s) they belong to crosses tenants, which RLS forbids for the app role, so it goes
through one narrow SECURITY DEFINER function: exact phone AND username match, returns only
the bank's id and name, never account data. The API rate-limits callers.

Revision ID: a8be8b74cb7f
Revises: 8901bf30be6d
Create Date: 2026-10-23 05:49:37.695863

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a8be8b74cb7f"
down_revision: str | Sequence[str] | None = "8901bf30be6d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("ledger_accounts", sa.Column("username", sa.String(length=64), nullable=True))
    op.create_unique_constraint(
        op.f("uq_ledger_accounts_org_id_username"), "ledger_accounts", ["org_id", "username"]
    )
    op.execute("""
        CREATE FUNCTION customer_login_banks(login_phone text, login_username text)
        RETURNS TABLE (bank_id uuid, bank_name text)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
            SELECT o.id, o.name
            FROM ledger_accounts a JOIN organizations o ON o.id = a.org_id
            WHERE a.phone = login_phone
              AND a.username = lower(login_username)
              AND o.status = 'active'
            ORDER BY o.created_at DESC
            LIMIT 10
        $$
    """)
    op.execute("REVOKE ALL ON FUNCTION customer_login_banks(text, text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION customer_login_banks(text, text) TO agentflow_app")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP FUNCTION customer_login_banks(text, text)")
    op.drop_constraint(
        op.f("uq_ledger_accounts_org_id_username"), "ledger_accounts", type_="unique"
    )
    op.drop_column("ledger_accounts", "username")
