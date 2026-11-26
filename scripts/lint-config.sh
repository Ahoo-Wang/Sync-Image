#!/usr/bin/env bash
# Report duplicate mapping lines in image config files.
#
# Renovate bumps every line of the same image to the same version, so lines
# that used to be different versions can become identical. Sync tools dedupe
# them silently; this keeps the files clean.
#
# A duplicate is a non-empty, non-comment line that is identical, ignoring
# leading and trailing whitespace, to an earlier such line in the same file.
#
# Usage: lint-config.sh [file ...]   (default: config/*-images.yaml)
#
# Exit codes: 0 no duplicates, 1 duplicates found, 2 no config files to check,
#             or a file that is missing, is not a regular file or cannot be read
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

if [ $# -eq 0 ]; then
  set -- "$ROOT"/config/*-images.yaml
  if [ ! -f "$1" ]; then
    echo "lint-config: no *-images.yaml files in $ROOT/config" >&2
    exit 2
  fi
fi

for file in "$@"; do
  if [ ! -f "$file" ]; then
    echo "lint-config: $file: no such file" >&2
    exit 2
  fi
  if [ ! -r "$file" ]; then
    echo "lint-config: $file: cannot read" >&2
    exit 2
  fi
done

status=0
for file in "$@"; do
  awk '
    /^[[:space:]]*($|#)/ { next }
    {
      line = $0
      sub(/^[[:space:]]+/, "", line)
      sub(/[[:space:]]+$/, "", line)
      if (line in seen) {
        printf "%s:%d: duplicate of line %d\n", FILENAME, FNR, seen[line]
        found = 1
      } else {
        seen[line] = FNR
      }
    }
    END { exit found }
  ' "$file"
  # awk exits 1 only for duplicates; anything else is a read error.
  case $? in
    0) ;;
    1) [ "$status" -eq 0 ] && status=1 ;;
    *)
      echo "lint-config: $file: read error" >&2
      status=2
      ;;
  esac
done

exit $status
