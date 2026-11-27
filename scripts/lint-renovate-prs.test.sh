#!/usr/bin/env bash
# Tests for scripts/lint-renovate-prs.sh. Run: scripts/lint-renovate-prs.test.sh
set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SCRIPT=$SCRIPT_DIR/lint-renovate-prs.sh

if [ ! -f "$SCRIPT" ]; then
  echo "FAIL $SCRIPT does not exist"
  exit 1
fi

TMP=$(mktemp -d)
trap 'rm -rf "${TMP:?}"' EXIT

pass=0
fail=0

ok() {
  echo "ok   $1"
  pass=$((pass + 1))
}

bad() {
  local desc=$1
  shift
  echo "FAIL $desc"
  local line
  for line in "$@"; do
    echo "  $line"
  done
  fail=$((fail + 1))
}

# The script runs main() only when executed, so it can be sourced here.
# shellcheck source=lint-renovate-prs.sh
. "$SCRIPT"
# Read by the sourced script; listed here so shellcheck does not report them.
# shellcheck disable=SC2034
REPO_DIR=$TMP/repo
# shellcheck disable=SC2034
DRY_RUN=1

# commit_all <repo> <message>
commit_all() {
  git -C "$1" add -A
  git -C "$1" commit -q -m "$2"
}

# git_head <repo> <branch-or-commit>
git_head() {
  git -C "$1" rev-parse "$2"
}

# A small repository: main, and one branch per situation a pull request can be in.
git init -q "$REPO_DIR"
git -C "$REPO_DIR" config user.email test@example.com
git -C "$REPO_DIR" config user.name Test
git -C "$REPO_DIR" config commit.gpgsign false
mkdir "$REPO_DIR/config"
printf '%s\n' 'busybox:1.38: "registry.example.com/ahoo/busybox"' > "$REPO_DIR/config/busybox-images.yaml"
commit_all "$REPO_DIR" base
BASE=$(git_head "$REPO_DIR" HEAD)

git -C "$REPO_DIR" checkout -q -b clean
printf '%s\n' 'curlimages/curl:8.23.0: "registry.example.com/ahoo/curl"' > "$REPO_DIR/config/curl-images.yaml"
commit_all "$REPO_DIR" add curl
CLEAN=$(git_head "$REPO_DIR" HEAD)

git -C "$REPO_DIR" checkout -q -b duplicate "$BASE"
printf '%s\n' \
  'busybox:1.38: "registry.example.com/ahoo/busybox"' \
  'busybox:1.38: "registry.example.com/ahoo/busybox"' > "$REPO_DIR/config/busybox-images.yaml"
commit_all "$REPO_DIR" duplicate line
DUPLICATE=$(git_head "$REPO_DIR" HEAD)

git -C "$REPO_DIR" checkout -q -b misnamed "$BASE"
: > "$REPO_DIR/config/MyGroup-images.yaml"
commit_all "$REPO_DIR" misnamed file
MISNAMED=$(git_head "$REPO_DIR" HEAD)

git -C "$REPO_DIR" checkout -q -b conflicting "$BASE"
printf '%s\n' 'busybox:1.36: "registry.example.com/ahoo/busybox"' > "$REPO_DIR/config/busybox-images.yaml"
commit_all "$REPO_DIR" bump on the branch
CONFLICTING=$(git_head "$REPO_DIR" HEAD)
git -C "$REPO_DIR" checkout -q -b other "$BASE"
printf '%s\n' 'busybox:1.37: "registry.example.com/ahoo/busybox"' > "$REPO_DIR/config/busybox-images.yaml"
commit_all "$REPO_DIR" bump on the other side
OTHER=$(git_head "$REPO_DIR" HEAD)

EMPTY_TREE=$(git -C "$REPO_DIR" hash-object -t tree /dev/null)
NOCONFIG=$(git -C "$REPO_DIR" commit-tree "$EMPTY_TREE" -m 'no files')

# assert_lint_tree <description> <expected-exit> <expected-description> <tree>
assert_lint_tree() {
  local desc=$1 expected_code=$2 expected_out=$3 tree=$4 out code
  out=$(lint_tree "$tree" 2>"$TMP/stderr")
  code=$?
  if [ "$code" = "$expected_code" ] && [ "$out" = "$expected_out" ]; then
    ok "$desc"
  else
    bad "$desc" \
      "expected exit $expected_code and description: $expected_out" \
      "actual   exit $code and description: $out" \
      "stderr: $(cat "$TMP/stderr")"
  fi
}

out=$(merged_tree "$BASE" "$CLEAN")
code=$?
if [ "$code" = 0 ] && printf '%s' "$out" | grep -qE '^[0-9a-f]{40}$'; then
  ok 'merged_tree prints the tree of a clean merge'
else
  bad 'merged_tree prints the tree of a clean merge' "exit $code, output: $out"
fi

out=$(merged_tree "$OTHER" "$CONFLICTING" 2>/dev/null)
code=$?
if [ "$code" = 1 ] && [ -z "$out" ]; then
  ok 'merged_tree fails on a conflicting merge'
else
  bad 'merged_tree fails on a conflicting merge' "exit $code, output: $out"
fi

assert_lint_tree \
  'a clean merged tree passes' 0 \
  'No duplicate mapping lines; group file names valid' \
  "$(merged_tree "$BASE" "$CLEAN")"

assert_lint_tree \
  'duplicate lines fail the tree' 1 \
  '1 duplicate mapping line in config/busybox-images.yaml' \
  "$(merged_tree "$BASE" "$DUPLICATE")"

assert_lint_tree \
  'an invalid group file name cannot be checked' 2 \
  "error: invalid group name 'MyGroup' (config/MyGroup-images.yaml): use lowercase letters, digits and hyphens" \
  "$(merged_tree "$BASE" "$MISNAMED")"

assert_lint_tree \
  'a tree without config/ cannot be checked' 2 \
  'config/ not found' \
  "$NOCONFIG"

out=$(post_status owner/repo 0000000000000000000000000000000000000000 failure 'Merge conflict with main' 'https://example.com/run')
code=$?
if [ "$code" = 0 ] && [ "$out" = $'failure\tMerge conflict with main' ]; then
  ok 'post_status prints the status in dry-run mode'
else
  bad 'post_status prints the status in dry-run mode' "exit $code, output: $out"
fi

LONG=$(printf 'x%.0s' $(seq 1 200))
out=$(post_status owner/repo 0000000000000000000000000000000000000000 error "$LONG" 'https://example.com/run')
code=$?
if [ "$code" = 0 ] && [ "${#out}" = 146 ]; then
  ok 'post_status keeps the description within the 140 characters GitHub allows'
else
  bad 'post_status keeps the description within the 140 characters GitHub allows' "exit $code, printed ${#out} characters: ${out:0:40}…"
fi

# Record what post_status passes to gh, without running it: the exact
# arguments matter, gh api has no --repo flag. post_status discards gh's
# output, so the stub writes to a file.
gh() {
  printf '%s\n' "$@" > "$TMP/gh-args"
}
DRY_RUN=
SHA=0000000000000000000000000000000000000000
post_status owner/repo "$SHA" failure 'Merge conflict with main' 'https://example.com/run'
code=$?
unset -f gh
DRY_RUN=1
out=$(cat "$TMP/gh-args" 2>/dev/null)
expected="api
-X
POST
repos/owner/repo/statuses/$SHA
-f
state=failure
-f
context=lint/config
-f
description=Merge conflict with main
-f
target_url=https://example.com/run"
if [ "$code" = 0 ] && [ "$out" = "$expected" ]; then
  ok 'post_status calls gh api with the documented arguments'
else
  bad 'post_status calls gh api with the documented arguments' "exit $code" "expected: $(printf '%s' "$expected" | tr '\n' ' ')" "actual:   $(printf '%s' "$out" | tr '\n' ' ')"
fi

# The real gh must accept that command; the dry run returns before gh runs. A
# fake token makes the request fail at authentication, so nothing is posted.
# Requiring a non-zero exit proves that gh really ran.
DRY_RUN=
out=$(GH_TOKEN=not-a-real-token post_status owner/repo "$SHA" failure 'Merge conflict with main' 'https://example.com/run' 2>"$TMP/stderr")
code=$?
DRY_RUN=1
if [ "$code" -ne 0 ] && [ -s "$TMP/stderr" ] &&
  ! grep -q -e 'unknown flag' -e 'unknown shorthand' -e 'Usage:' -e 'unbound variable' "$TMP/stderr"; then
  ok 'the real gh accepts the command post_status builds'
else
  bad 'the real gh accepts the command post_status builds' "exit $code, stderr: $(head -2 "$TMP/stderr" | tr '\n' ' ')"
fi

echo
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
