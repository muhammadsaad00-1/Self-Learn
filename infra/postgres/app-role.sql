-- Runtime database role for the AgentFlow API and workers.
--
-- Migrations run as the schema owner (POSTGRES_USER locally, the RDS master user on AWS).
-- The app connects as this separate role, which owns nothing, is not a superuser and has no
-- BYPASSRLS, so Postgres row-level security policies actually apply to every app query.
-- (Superusers and table owners skip RLS, which would make it decoration.)
--
-- Idempotent. Run by: docker compose (first start of an empty volume), the test suite,
-- and Terraform in Phase 7 (with a password from Secrets Manager instead of this local one).
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'agentflow_app') THEN
    CREATE ROLE agentflow_app LOGIN PASSWORD 'agentflow_app' NOSUPERUSER NOBYPASSRLS;
  END IF;
END
$$;
