#!/bin/sh
# Restore from an encrypted backup. DESTRUCTIVE: replaces the current database.
#   BACKUP_PASSPHRASE=... ./deploy/restore.sh backups/smti-2026-10-14-0200.sql.gz.gpg
#
# Practise this against a scratch database before you need it. An untested
# backup is a guess.
set -eu

: "${BACKUP_PASSPHRASE:?set BACKUP_PASSPHRASE}"
FILE="${1:?usage: restore.sh <backup file>}"
DB="${POSTGRES_DB:-smti}"
OWNER="${POSTGRES_OWNER:-smti_owner}"

printf 'This will REPLACE the contents of "%s". Type the database name to continue: ' "$DB"
read -r confirm
[ "$confirm" = "$DB" ] || { echo "aborted"; exit 1; }

docker compose stop web

gpg --batch --quiet --decrypt --passphrase "$BACKUP_PASSPHRASE" "$FILE" \
  | gunzip \
  | docker compose exec -T db psql -U "$OWNER" -d postgres \
      -c "DROP DATABASE IF EXISTS ${DB}_restore;" \
      -c "CREATE DATABASE ${DB}_restore OWNER $OWNER;" >/dev/null

gpg --batch --quiet --decrypt --passphrase "$BACKUP_PASSPHRASE" "$FILE" \
  | gunzip \
  | docker compose exec -T db psql -U "$OWNER" -d "${DB}_restore" >/dev/null

echo "Restored into ${DB}_restore. Verify it, then swap:"
echo "  docker compose exec db psql -U $OWNER -d postgres \\"
echo "    -c 'ALTER DATABASE $DB RENAME TO ${DB}_old;' \\"
echo "    -c 'ALTER DATABASE ${DB}_restore RENAME TO $DB;'"
echo "  docker compose exec db psql -U $OWNER -d $DB -f /deploy/grants.sql"
echo "  docker compose start web"
