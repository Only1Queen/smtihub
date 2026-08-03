#!/bin/sh
# Nightly backup. Encrypted, because it contains every analyst's appraisal.
#   ./deploy/backup.sh                 # writes backups/smti-YYYY-MM-DD.sql.gz.gpg
#   BACKUP_KEEP_DAYS=30 ./deploy/backup.sh
#
# Needs BACKUP_PASSPHRASE in the environment (keep it somewhere other than this
# host — a backup you cannot decrypt after losing the host is not a backup).
set -eu

: "${BACKUP_PASSPHRASE:?set BACKUP_PASSPHRASE}"
KEEP="${BACKUP_KEEP_DAYS:-30}"
STAMP=$(date +%Y-%m-%d-%H%M)
OUT="backups/smti-${STAMP}.sql.gz.gpg"

docker compose exec -T db pg_dump -U "${POSTGRES_OWNER:-smti_owner}" -d "${POSTGRES_DB:-smti}" \
  | gzip -9 \
  | gpg --batch --yes --symmetric --cipher-algo AES256 \
        --passphrase "$BACKUP_PASSPHRASE" -o "$OUT"

echo "wrote $OUT ($(du -h "$OUT" | cut -f1))"

find backups -name 'smti-*.sql.gz.gpg' -mtime "+${KEEP}" -print -delete
