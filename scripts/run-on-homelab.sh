#!/usr/bin/env bash
# Run a supabase-exit.sh step on the homelab from this Mac.
#
#   scripts/run-on-homelab.sh            # setup + deploy + verify
#   scripts/run-on-homelab.sh cutover    # about a week later
#   scripts/run-on-homelab.sh status
set -euo pipefail
HOST="${REVISE_HOST:-homelab}"
scp -q "$(dirname "$0")/supabase-exit.sh" "$HOST:supabase-exit.sh"
ssh -t "$HOST" "bash ~/supabase-exit.sh ${1:-all}"
