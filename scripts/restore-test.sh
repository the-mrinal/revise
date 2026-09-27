#!/usr/bin/env bash
# Prove a backup restores: load a dump into a throwaway Postgres and print
# row counts next to the live database's. Touches nothing live.
#
#   scripts/restore-test.sh                      # newest dump in BACKUP_DIR
#   scripts/restore-test.sh path/to/file.dump
set -euo pipefail

cd "$(dirname "$0")/.."
BACKUP_DIR="${BACKUP_DIR:-$HOME/revise-backups}"
DUMP="${1:-$(ls -t "$BACKUP_DIR"/revise-*.dump | head -1)}"
NAME="revise-restore-test-$$"
COUNTS="SELECT 'users', count(*) FROM users UNION ALL SELECT 'questions', count(*) FROM questions
        UNION ALL SELECT 'question_events', count(*) FROM question_events
        UNION ALL SELECT 'user_profiles', count(*) FROM user_profiles ORDER BY 1"

docker run -d --rm --name "$NAME" -e POSTGRES_PASSWORD=restore postgres:17.6 > /dev/null
trap 'docker rm -f "$NAME" > /dev/null' EXIT
until docker exec "$NAME" pg_isready -U postgres -q; do sleep 1; done

docker exec -i "$NAME" pg_restore -U postgres -d postgres --no-owner < "$DUMP"
echo "Restored $(basename "$DUMP"):"
docker exec "$NAME" psql -U postgres -At -F ' ' -c "$COUNTS"
echo "Live database:"
docker compose exec -T db psql -U revise -d revise -At -F ' ' -c "$COUNTS"
