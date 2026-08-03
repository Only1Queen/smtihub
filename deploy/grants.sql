-- Make the audit trail append-only at the database, not just in application code.
--
-- Two roles, deliberately:
--   smti_owner  owns the schema and runs migrations. Not used by the running app.
--   smti_web    the role Django connects as. Can INSERT and SELECT audit rows,
--               and nothing else — so no application bug, and nobody who gains
--               application-level access, can rewrite the history.
--
-- Run as smti_owner AFTER every `manage.py migrate`: a migration that creates a
-- table grants privileges on it afresh.
--
--   psql "$OWNER_DATABASE_URL" -f deploy/grants.sql
--
-- Verify afterwards with deploy/verify_grants.sql.

\set ON_ERROR_STOP on

-- Ordinary tables: the app needs full DML.
GRANT USAGE ON SCHEMA public TO smti_web;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO smti_web;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO smti_web;

-- Tables from later migrations inherit the same baseline.
ALTER DEFAULT PRIVILEGES FOR ROLE smti_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO smti_web;
ALTER DEFAULT PRIVILEGES FOR ROLE smti_owner IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO smti_web;

-- The exception that matters: audit rows may be written and read, never changed.
REVOKE UPDATE, DELETE, TRUNCATE ON hub_auditevent FROM smti_web;

-- Scores and tasks stay mutable at the database level; their immutability rules
-- live in the models, where they can explain themselves to the user. Only the
-- audit trail gets a hard database guarantee, because it is the record that has
-- to be trusted when someone disputes a mark months later.
