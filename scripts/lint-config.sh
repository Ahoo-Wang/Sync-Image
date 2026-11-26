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
# Exit codes: 0 no duplicates, 1 duplicates found, 2 a file does not exist
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

[ $# -gt 0 ] || set -- "$ROOT"/config/*-images.yaml

for file in "$@"; do
  if [ ! -e "$file" ]; then
    echo "lint-config: $file: no such file" >&2
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
  ' "$file" || status=1
done

exit $status
