-- Proves the append-only rule is actually in force. Run as smti_owner.
-- Every row must report the expected answer; anything else means the audit
-- trail is rewritable and grants.sql has not been applied since the last migrate.

\set ON_ERROR_STOP on

SELECT
    privilege_type,
    CASE privilege_type
        WHEN 'INSERT' THEN 'expected: present'
        WHEN 'SELECT' THEN 'expected: present'
        ELSE 'UNEXPECTED — audit table must not be updatable'
    END AS verdict
FROM information_schema.table_privileges
WHERE grantee = 'smti_web' AND table_name = 'hub_auditevent'
ORDER BY privilege_type;

-- The check that must return true.
SELECT
    NOT has_table_privilege('smti_web', 'hub_auditevent', 'UPDATE')
    AND NOT has_table_privilege('smti_web', 'hub_auditevent', 'DELETE')
    AND NOT has_table_privilege('smti_web', 'hub_auditevent', 'TRUNCATE')
    AND has_table_privilege('smti_web', 'hub_auditevent', 'INSERT')
    AND has_table_privilege('smti_web', 'hub_auditevent', 'SELECT')
    AS audit_append_only_ok;
