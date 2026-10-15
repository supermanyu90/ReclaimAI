#!/usr/bin/env bash
# PII leak scan. Runs in CI.
# Fails on PII-shaped literals in source and on log calls that interpolate runtime
# user data. Route log content through reclaim_api/lib/log.py helpers instead.
set -euo pipefail
cd "$(dirname "$0")/.."

SRC=(apps/api/src workers/src apps/web/src)
INC=(--include=*.py --include=*.ts --include=*.tsx)
FAILURES=0

fail() { echo "FAIL: $1"; FAILURES=$((FAILURES + 1)); }

echo "Scanning ${SRC[*]} for PII patterns..."

grep -rnE "${INC[@]}" '(^|[^0-9])[0-9]{12}([^0-9]|$)' "${SRC[@]}" && fail "possible Aadhaar number"
grep -rnE "${INC[@]}" '[A-Z]{5}[0-9]{4}[A-Z]([^A-Z]|$)' "${SRC[@]}" && fail "possible PAN"
grep -rnE "${INC[@]}" '["'"'"']\+91[0-9]{10}["'"'"']' "${SRC[@]}" && fail "Indian phone number literal"
grep -rnE "${INC[@]}" '["'"'"'][0-9]{11,18}["'"'"']' "${SRC[@]}" && fail "possible account number literal"

# Log calls: Python logging/print and JS console.
LOG_LINES=$(grep -rnE "${INC[@]}" '(log|logger|logging)\.(debug|info|warning|error|exception|critical)\(|print\(|console\.(log|info|warn|error|debug)\(' "${SRC[@]}" || true)

check_log() {
  local hits
  hits=$(printf '%s\n' "$LOG_LINES" | grep -E -- "$1" || true)
  if [ -n "$hits" ]; then printf '%s\n' "$hits"; fail "$2 — use a helper from reclaim_api/lib/log.py"; fi
}

check_log 'json\.dumps\(|JSON\.stringify\('                         'log call serialises an object wholesale'
check_log '\{(text|message|body|content|prompt|document)[^a-z_]'    'log call interpolates raw user text'
check_log '\{(err|error|exc|e)\}|\$\{(err|error)\}|str\((err|exc|e)\)' 'log call stringifies an error (may echo request bodies)'
check_log '\{(asset|nominee|beneficiary|account|reference)[.}\[]'   'log call interpolates record data'

if [ "$FAILURES" -eq 0 ]; then echo "PII scan clean."; exit 0; fi
echo "Found $FAILURES PII pattern(s). Fix before committing."; exit 1
