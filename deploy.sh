#!/bin/sh
# The whole deployment. Run it from anywhere:
#
#   ./deploy.sh
#
# Backs up first, then hands over to Compose, which runs roles -> migrate ->
# grants -> web in order and stops at the first failure. Safe to re-run.
set -eu
cd "$(dirname "$0")"

# The backup passphrase lives in .env so the nightly cron job and a restore at
# 3am both find it without anyone remembering. Generated once, on first deploy.
if ! grep -q '^BACKUP_PASSPHRASE=' .env 2>/dev/null; then
    printf '\nBACKUP_PASSPHRASE=%s\n' "$(head -c 32 /dev/urandom | base64 | tr -d '=+/')" >> .env
    NEW_PASSPHRASE=yes
fi

if docker compose ps --status running --services 2>/dev/null | grep -q '^db$'; then
    echo "==> Backing up before anything changes"
    ./deploy/backup.sh
else
    echo "==> No database running yet — nothing to back up (first install)"
fi

echo "==> Building and starting"
docker compose up -d --build

echo
docker compose ps

if [ "${NEW_PASSPHRASE:-}" = yes ]; then
    cat <<BANNER

  ############################################################
  #  SAVE THIS SOMEWHERE OTHER THAN THIS SERVER
  #
  #  BACKUP_PASSPHRASE=$(grep -m1 '^BACKUP_PASSPHRASE=' .env | cut -d= -f2-)
  #
  #  Every backup is encrypted with it. Without it, a backup
  #  cannot be restored — including after this host is lost,
  #  which is the day you will need one.
  ############################################################
BANNER
fi
