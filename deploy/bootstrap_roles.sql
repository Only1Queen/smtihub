-- Creates the restricted application role. Run ONCE as smti_owner, before the
-- first migrate. Substitute the password with psql -v:
--
--   psql "$OWNER_DATABASE_URL" -v web_password="'...'" -f deploy/bootstrap_roles.sql

\set ON_ERROR_STOP on

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'smti_web') THEN
        CREATE ROLE smti_web LOGIN;
    END IF;
END
$$;

ALTER ROLE smti_web PASSWORD :web_password;
GRANT CONNECT ON DATABASE smti TO smti_web;

-- smti_web deliberately gets no CREATE on the schema: it cannot add or drop
-- tables, only read and write the ones migrations made.
REVOKE CREATE ON SCHEMA public FROM smti_web;
