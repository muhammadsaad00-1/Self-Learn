"""tenant row level security

Defense in depth for tenant isolation. Application code already filters every query by
org_id; these policies make Postgres refuse cross-tenant rows even when that code has a bug.

The app sets two transaction-local settings (db.set_tenant_context):
  app.current_user_id  - the authenticated user
  app.current_org_id   - the organization the request acts in
With neither set, tenant tables look empty to the app role.

Requires the `agentflow_app` role (infra/postgres/app-role.sql).

Revision ID: 562924d824f0
Revises: 65ba12b8592f
Create Date: 2026-10-11 13:37:41.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "562924d824f0"
down_revision: str | Sequence[str] | None = "65ba12b8592f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "agentflow_app"


def upgrade() -> None:
    # Least privilege: data access only (no DDL), and only on the tables that exist now.
    # Each future migration grants its own tables explicitly, so a new table is never
    # reachable by the app before someone has decided its RLS policy.
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON organizations, users, memberships TO {APP_ROLE}"
    )

    # NULLIF: after a transaction ends, a set_config value reads back as '' rather than NULL.
    op.execute("""
        CREATE FUNCTION app_current_org_id() RETURNS uuid LANGUAGE sql STABLE AS
        $$ SELECT NULLIF(current_setting('app.current_org_id', true), '')::uuid $$
    """)
    op.execute("""
        CREATE FUNCTION app_current_user_id() RETURNS uuid LANGUAGE sql STABLE AS
        $$ SELECT NULLIF(current_setting('app.current_user_id', true), '')::uuid $$
    """)

    # memberships: rows of the current org, plus the user's own memberships in any org
    # (needed for /me and for checking membership before an org is chosen).
    op.execute("ALTER TABLE memberships ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation ON memberships
        USING (org_id = app_current_org_id() OR user_id = app_current_user_id())
        WITH CHECK (org_id = app_current_org_id())
    """)

    # organizations: the current org, plus orgs the user belongs to (visible via memberships,
    # which is itself filtered by the policy above).
    op.execute("ALTER TABLE organizations ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation ON organizations
        USING (
            id = app_current_org_id()
            OR id IN (SELECT org_id FROM memberships WHERE user_id = app_current_user_id())
        )
        WITH CHECK (id = app_current_org_id())
    """)
    # users has no RLS on purpose: accounts are global (one login, many orgs), and login
    # must find a user before any tenant is known. No users column is tenant data.


def downgrade() -> None:
    for table in ("organizations", "memberships"):
        op.execute(f"DROP POLICY tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION app_current_user_id()")
    op.execute("DROP FUNCTION app_current_org_id()")
    op.execute(
        f"REVOKE SELECT, INSERT, UPDATE, DELETE ON organizations, users, memberships "
        f"FROM {APP_ROLE}"
    )
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {APP_ROLE}")
