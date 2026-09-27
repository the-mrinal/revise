#!/usr/bin/env bash
# Run a supabase-exit.sh step on the homelab from this Mac.
#
#   scripts/run-on-homelab.sh phase2     # GitHub app keys, signing secret
#   scripts/run-on-homelab.sh status
#   scripts/run-on-homelab.sh            # list every step
set -euo pipefail
HOST="${REVISE_HOST:-homelab}"
scp -q "$(dirname "$0")/supabase-exit.sh" "$HOST:supabase-exit.sh"
ssh -t "$HOST" "bash ~/supabase-exit.sh ${1:-help}"
