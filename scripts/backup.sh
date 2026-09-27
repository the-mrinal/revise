#!/usr/bin/env bash
# Nightly backup of Revise: the Postgres database and the avatar files.
#
# Keeps BACKUP_KEEP_DAYS (default 30) days of backups in BACKUP_DIR and, when
# BACKUP_REMOTE is set to an rclone remote (e.g. r2:revise-backups), copies
# each new backup off the server. Every dump is checked to be readable.
#
# Run from cron on the server, e.g. at 03:15 UTC:
#   15 3 * * * cd ~/github-personal/revise && scripts/backup.sh >> ~/revise-backups/backup.log 2>&1
set -euo pipefail

cd "$(dirname "$0")/.."
BACKUP_DIR="${BACKUP_DIR:-$HOME/revise-backups}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-30}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DUMP="$BACKUP_DIR/revise-$STAMP.dump"
AVATARS="$BACKUP_DIR/avatars-$STAMP.tar.gz"

mkdir -p "$BACKUP_DIR"

docker compose exec -T db pg_dump -U revise -d revise --format=custom > "$DUMP.partial"
docker compose exec -T db pg_restore --list < "$DUMP.partial" > /dev/null  # readable?
mv "$DUMP.partial" "$DUMP"

docker compose exec -T server tar -C /data -czf - avatars > "$AVATARS.partial"
mv "$AVATARS.partial" "$AVATARS"

if [ -n "${BACKUP_REMOTE:-}" ]; then
  rclone copy "$BACKUP_DIR" "$BACKUP_REMOTE" --include "*-$STAMP.*"
fi

find "$BACKUP_DIR" -maxdepth 1 \( -name 'revise-*.dump' -o -name 'avatars-*.tar.gz' \) \
  -mtime +"$KEEP_DAYS" -delete

echo "$(date -u +%FT%TZ) backup ok: $(basename "$DUMP") ($(du -h "$DUMP" | cut -f1)), $(basename "$AVATARS")"
