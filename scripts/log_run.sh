#!/usr/bin/env bash
# Run a command while teeing its stdout+stderr to BOTH the console and the
# append-only research log (data/research.log).
#
# Usage:
#     scripts/log_run.sh "label describing the step" -- <command...>
#
# The label and the full command line are written to the log before the run,
# and an exit-code footer after it, so long jobs are easy to grep for.
# NOTE: never pass secrets through the command line here; set them via env.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="$ROOT/data/research.log"

label="${1:-}"
if [ -z "$label" ]; then
    echo "usage: $0 <label> -- <command...>" >&2
    exit 2
fi
shift
[ "${1:-}" = "--" ] && shift

mkdir -p "$(dirname "$LOG")"
TS() { date '+%Y-%m-%d %H:%M:%S'; }

{
    printf '\n[%s] RUN  : %s\n' "$(TS)" "$label"
    printf '[%s] CMD : %s\n' "$(TS)" "$*"
} | tee -a "$LOG"

"$@" 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}

printf '[%s] DONE : %s (exit=%s)\n' "$(TS)" "$label" "$rc" | tee -a "$LOG"
exit "$rc"
