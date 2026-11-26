#!/usr/bin/env bash
# List the image groups that the sync workflow runs for.
#
# A group is a file config/<group>-images.yaml, so adding a group means adding
# a file. The workflow derives its matrix from this output instead of keeping
# a list of its own:
#   {"include":[{"group":"altinity"},{"group":"bitnami"}]}
#
# Usage: list-groups.sh [group]
#   group: "all" (default) or the name of a single group
#
# CONFIG_DIR overrides the directory to scan (default: the repo's config/).
set -euo pipefail

CONFIG_DIR=${CONFIG_DIR:-"$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/config"}
SELECTED=${1:-all}

fail() {
  echo "error: $*" >&2
  exit 1
}

[ -d "$CONFIG_DIR" ] || fail "config directory not found: $CONFIG_DIR"

groups=()
for file in "$CONFIG_DIR"/*-images.yaml; do
  [ -e "$file" ] || continue
  name=$(basename "$file" -images.yaml)
  [[ $name =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]] || fail "invalid group name '$name' ($file): use lowercase letters, digits and hyphens"
  groups+=("$name")
done

[ ${#groups[@]} -gt 0 ] || fail "no *-images.yaml files in $CONFIG_DIR"

sorted=$(printf '%s\n' "${groups[@]}" | LC_ALL=C sort)

if [ "$SELECTED" != all ]; then
  if ! printf '%s\n' "$sorted" | grep -qx -- "$SELECTED"; then
    fail "unknown group '$SELECTED'; available groups: $(printf '%s\n' "$sorted" | tr '\n' ' ')"
  fi
  sorted=$SELECTED
fi

json=
while IFS= read -r name; do
  json+="{\"group\":\"$name\"},"
done <<< "$sorted"

echo "{\"include\":[${json%,}]}"
