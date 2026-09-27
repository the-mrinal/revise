#!/usr/bin/env bash
# Leaving Supabase, phase 1: every step you run on the server, in order.
#
#   scripts/supabase-exit.sh setup     add the new settings to .env (asks for the
#                                      Supabase connection string) — before merging
#   scripts/supabase-exit.sh deploy    pull main and restart, still on Supabase
#                                      (or use the Deploy workflow instead)
#   scripts/supabase-exit.sh verify    prove the new code on real data, move avatars,
#                                      set up nightly backups, rehearse the copy
#   scripts/supabase-exit.sh cutover   ~1 week later, at a quiet hour: switch to our
#                                      own Postgres (requests pause for ~1 second)
#   scripts/supabase-exit.sh status    where things stand
#
# Each step checks the previous one and stops on the first problem, leaving
# the site as it was. Every step is safe to re-run.
#
# Before the PR is merged, the script isn't on the server yet. Get it with:
#   cd ~/github-personal/revise && git fetch origin supabase-exit/phase-1 &&
#   git show origin/supabase-exit/phase-1:scripts/supabase-exit.sh > ~/supabase-exit.sh &&
#   bash ~/supabase-exit.sh setup
set -euo pipefail

REVISE_DIR="${REVISE_DIR:-$HOME/github-personal/revise}"
ENV_FILE="$REVISE_DIR/.env"
PG_IMAGE="postgres:17.6"

bold() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok() { printf '  \033[32m✓\033[0m %s\n' "$*"; }
fail() { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

cd "$REVISE_DIR" 2>/dev/null || fail "Revise isn't at $REVISE_DIR. Set REVISE_DIR=/path/to/revise and re-run."
[ -f "$ENV_FILE" ] || fail "No .env in $REVISE_DIR."

env_get() { grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- || true; }

env_set() {  # replace or append KEY=VALUE, keeping the file private
  local key="$1" value="$2" tmp
  tmp="$(mktemp)"
  grep -v -E "^$key=" "$ENV_FILE" > "$tmp" || true
  printf '%s=%s\n' "$key" "$value" >> "$tmp"
  cat "$tmp" > "$ENV_FILE"
  rm -f "$tmp"
  chmod 600 "$ENV_FILE"
}

backup_env() {
  local copy
  copy="$ENV_FILE.bak-$(date -u +%Y%m%dT%H%M%SZ)"
  cp "$ENV_FILE" "$copy" && chmod 600 "$copy"
  ok "saved a copy of .env as $(basename "$copy")"
}

server_target() {
  docker compose exec -T server python cutover.py status 2>/dev/null \
    | grep '"target"' | sed -E 's/.*"target": "([a-z]+)".*/\1/'
}

wait_for_site() {
  for _ in $(seq 1 60); do
    if [ "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8765/ || true)" = "200" ]; then
      return 0
    fi
    sleep 2
  done
  docker compose logs --tail 40 server
  fail "The site didn't come back within 2 minutes (logs above)."
}

cmd_setup() {
  bold "1/4  Saving a copy of .env"
  backup_env

  bold "2/4  Password for our own Postgres"
  if [ -n "$(env_get POSTGRES_PASSWORD)" ]; then
    ok "POSTGRES_PASSWORD already set; keeping it"
  else
    # hex: no characters that need escaping inside a database URL
    env_set POSTGRES_PASSWORD "$(openssl rand -hex 24)"
    ok "generated POSTGRES_PASSWORD"
  fi

  bold "3/4  Which database serves requests"
  local target
  target="$(env_get DB_TARGET)"
  if [ -z "$target" ]; then
    env_set DB_TARGET supabase
    ok "DB_TARGET=supabase (it becomes local at the cutover)"
  else
    ok "DB_TARGET already set to '$target'; leaving it"
  fi

  bold "4/4  Connection to the Supabase database"
  local url
  url="$(env_get SUPABASE_DB_URL)"
  if [ -n "$url" ]; then
    ok "SUPABASE_DB_URL already set; testing it"
  else
    echo "  In the Supabase dashboard: Connect (top bar) → Session pooler → copy the URI,"
    echo "  and put your database password in place of [YOUR-PASSWORD]."
    echo "  (Forgot it? Project Settings → Database → Reset database password.)"
    read -r -s -p "  Paste the URI (hidden): " url
    echo
    [ -n "$url" ] || fail "Nothing pasted. Re-run 'setup' when you have it."
    case "$url" in
      *"[YOUR-PASSWORD]"*) fail "The URI still has [YOUR-PASSWORD] in it. Replace it with the real password and re-run." ;;
      postgres://*|postgresql://*) ;;
      *) fail "That doesn't look like a postgres:// URI." ;;
    esac
  fi
  local users
  users="$(docker run --rm "$PG_IMAGE" psql "$url" -Atc 'SELECT count(*) FROM auth.users' 2>&1)" \
    || fail "Couldn't connect with that URI: $users
  If the password has characters like @ # / ?, reset it to letters and digits, or percent-encode them."
  env_set SUPABASE_DB_URL "$url"
  ok "connected to Supabase: $users accounts"

  docker compose config -q || fail "docker compose rejects the settings (see above)."
  bold "Setup done. Next: merge PR #44, then run the Deploy workflow (or 'deploy' here)."
}

cmd_deploy() {
  bold "Checking that PR #44 is merged"
  git fetch -q origin main
  git cat-file -e origin/main:server/migrate.py 2>/dev/null \
    || fail "main doesn't contain the Phase 1 code yet. Merge PR #44 first."
  [ "$(env_get DB_TARGET)" = "supabase" ] || [ "$(env_get DB_TARGET)" = "local" ] \
    || fail "Run 'setup' first."
  ok "merged"

  bold "Pulling and restarting"
  git pull -q --ff-only origin main
  docker compose up -d --build
  wait_for_site
  docker compose logs server 2>&1 | grep '\[migrate\]' | tail -4 | sed 's/^/  /'
  ok "site is up, serving from: $(server_target)"
  bold "Deployed. Next: 'verify'."
}

cmd_verify() {
  [ "$(server_target)" = "supabase" ] || fail "Expected the server to be on Supabase at this stage (it's on '$(server_target)')."

  bold "1/5  Comparing the old and new data code on every user's real data (read-only)"
  docker compose exec -T server python compare_implementations.py \
    || fail "The new code returned something different. Nothing was changed. Send me the output above; to back out, redeploy the previous main."

  bold "2/5  Moving avatars off Supabase Storage"
  docker compose exec -T server python copy_avatars.py

  bold "3/5  Nightly backups (03:15 UTC)"
  local line="15 3 * * * cd $REVISE_DIR && scripts/backup.sh >> \$HOME/revise-backups/backup.log 2>&1"
  mkdir -p "$HOME/revise-backups"
  if crontab -l 2>/dev/null | grep -q 'scripts/backup.sh'; then
    ok "backup already scheduled"
  else
    (crontab -l 2>/dev/null; echo "$line") | crontab -
    ok "added to crontab"
  fi

  bold "4/5  Taking a backup now and restoring it into a throwaway database"
  scripts/backup.sh
  scripts/restore-test.sh

  bold "5/5  Rehearsing the copy into our Postgres (the live site is untouched)"
  docker compose exec -T server python copy_to_local.py rehearse | tail -3

  bold "All checks passed. Leave it running for about a week, then run 'cutover' at a quiet hour."
}

cmd_cutover() {
  [ "$(server_target)" = "supabase" ] || fail "The server isn't on Supabase (it's on '$(server_target)'); nothing to cut over."

  bold "1/4  Final rehearsal (the live site is untouched)"
  docker compose exec -T server python copy_to_local.py rehearse | tail -3

  echo
  read -r -p "Switch Revise to its own Postgres now? Requests pause for about a second. Type CUTOVER to go: " answer
  [ "$answer" = "CUTOVER" ] || fail "Not confirmed; nothing changed."

  bold "2/4  Cutover"
  docker compose exec -T server python copy_to_local.py run \
    || fail "The cutover stopped and the site is still on Supabase, unchanged. Send me the output above."

  bold "3/4  Recording the switch in .env"
  backup_env
  env_set DB_TARGET local
  ok "DB_TARGET=local"

  bold "4/4  Backup of the new database"
  scripts/backup.sh
  wait_for_site
  ok "site is up, serving from: $(server_target)"
  bold "Done. Revise's data now lives in its own Postgres. Supabase is untouched and can stay as it is until Phase 4."
}

cmd_status() {
  echo "DB_TARGET in .env:       $(env_get DB_TARGET)"
  echo "SUPABASE_DB_URL in .env: $([ -n "$(env_get SUPABASE_DB_URL)" ] && echo set || echo missing)"
  echo "server serving from:     $(server_target || echo 'not running')"
  echo "nightly backup:          $(crontab -l 2>/dev/null | grep -q scripts/backup.sh && echo scheduled || echo 'not scheduled')"
  echo "latest backup:           $(ls -t "$HOME"/revise-backups/revise-*.dump 2>/dev/null | head -1 || echo none)"
}

case "${1:-}" in
  setup) cmd_setup ;;
  deploy) cmd_deploy ;;
  verify) cmd_verify ;;
  cutover) cmd_cutover ;;
  status) cmd_status ;;
  *) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
