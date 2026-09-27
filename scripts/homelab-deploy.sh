#!/usr/bin/env bash
# Deploy Revise on the homelab when the GitHub "Deploy" workflow is run.
#
# GitHub can't reach the homelab, so the homelab asks GitHub instead: every
# two minutes (cron) this checks for a newer successful Deploy run and, if
# there is one, deploys exactly the commit that run was started on. The
# site keeps serving while the new image builds; the swap takes seconds.
#
# Install (as the user that runs Revise's containers):
#   cp scripts/homelab-deploy.sh ~/bin/revise-deploy.sh
#   ~/bin/revise-deploy.sh --install      # adds the cron entry; skips past runs
# Log: ~/revise-deploy.log
set -euo pipefail

REPO="the-mrinal/revise"
REVISE_DIR="${REVISE_DIR:-$HOME/homelab/apps/revise}"
STATE="$HOME/.revise-deploy-last-run"
LOG="$HOME/revise-deploy.log"
API="https://api.github.com/repos/$REPO/actions/workflows/deploy.yml/runs?status=success&per_page=1"

log() { echo "$(date -u +%FT%TZ) $*" >> "$LOG"; }

latest_run() {  # prints: run_id head_sha head_branch  (nothing if unavailable)
  curl -fsS --max-time 20 -H "Accept: application/vnd.github+json" "$API" 2>/dev/null \
    | jq -r '.workflow_runs[0] // empty | "\(.id) \(.head_sha) \(.head_branch)"'
}

if [ "${1:-}" = "--install" ]; then
  read -r id _ _ <<< "$(latest_run)"
  echo "${id:-0}" > "$STATE"
  line="*/2 * * * * $HOME/bin/revise-deploy.sh"
  crontab -l 2>/dev/null | grep -qF "$line" || (crontab -l 2>/dev/null; echo "$line") | crontab -
  echo "Installed. Deploy runs newer than ${id:-none} will be deployed; log: $LOG"
  exit 0
fi

exec 9>"$HOME/.revise-deploy.lock"
flock -n 9 || exit 0  # a deploy is already running

read -r id sha branch <<< "$(latest_run)" || true
[ -n "${id:-}" ] || exit 0  # GitHub unreachable or rate-limited: try next time
last="$(cat "$STATE" 2>/dev/null || echo 0)"
[ "$id" -gt "$last" ] || exit 0

log "deploy run $id: $branch @ ${sha:0:7}"
echo "$id" > "$STATE"  # one attempt per run, even if it fails (no retry loops)
cd "$REVISE_DIR"
if ! git diff --quiet || ! git diff --cached --quiet; then
  log "  FAILED: uncommitted changes in $REVISE_DIR (git status); nothing deployed"
  exit 1
fi
git fetch -q origin >> "$LOG" 2>&1 || { log "  FAILED: git fetch"; exit 1; }
# Once the data lives in our own Postgres (DB_TARGET=local), a commit from
# before that move would quietly serve from Supabase again: refuse it.
if grep -q '^DB_TARGET=local' .env && ! git cat-file -e "$sha:server/cutover.py" 2>/dev/null; then
  log "  REFUSED: ${sha:0:7} predates the move off Supabase; deploying it would split the data"
  exit 1
fi
{
  git checkout -q -B "$branch" "$sha"
  docker compose build -q
  docker compose up -d
} >> "$LOG" 2>&1 || { log "  FAILED during build/start (see above)"; exit 1; }

for _ in $(seq 1 60); do
  if [ "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8765/)" = "200" ]; then
    log "  ok: serving ${sha:0:7}"
    exit 0
  fi
  sleep 2
done
log "  FAILED: site not answering 2 minutes after the restart"
docker compose logs --tail 30 server >> "$LOG" 2>&1
exit 1
