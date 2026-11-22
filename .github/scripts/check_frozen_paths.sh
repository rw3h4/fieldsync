#!/usr/bin/env bash
# Fail if a PR touches frozen or M2-owned code (owner review required).
#   - any change under fieldsync/crdt/, runtime/, net/, ui/node/, ui/components/
#   - any change other than an addition to fieldsync/sim/, except scenarios.py
# Usage: check_frozen_paths.sh <base-ref>   (compares <base-ref>...HEAD)
set -euo pipefail
base="${1:?usage: check_frozen_paths.sh <base-ref>}"

offending=()
while IFS=$'\t' read -r status path; do
  case "$path" in
    fieldsync/crdt/* | fieldsync/runtime/* | fieldsync/net/* | fieldsync/ui/node/* | fieldsync/ui/components/*)
      offending+=("$status $path") ;;
    fieldsync/sim/scenarios.py) ;;
    fieldsync/sim/*)
      [[ "$status" == A ]] || offending+=("$status $path") ;;
  esac
done < <(git diff --name-status --no-renames "$base...HEAD")

if ((${#offending[@]})); then
  printf '%s\n' "${offending[@]}"
  echo "Touches frozen or M2-owned code: owner review required"
  exit 1
fi
echo "No frozen or M2-owned paths touched."
