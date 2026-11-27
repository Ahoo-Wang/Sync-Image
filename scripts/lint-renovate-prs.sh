#!/usr/bin/env bash
# Lint the configuration that each open Renovate pull request would produce,
# and report the result as a commit status on its head commit.
#
# Why: Renovate pushes with GITHUB_TOKEN, so the `pull_request` runs of
# lint-config.yml wait for approval ("action_required") and a Renovate PR can
# show "no checks reported" even when it would introduce duplicate lines. The
# Renovate workflow calls this script instead, where no approval is needed.
#
# For every open pull request authored by Renovate it merges the head into
# origin/main with `git merge-tree`, checks the resulting config/ with
# list-groups.sh and lint-config.sh, and posts a status with the context
# "lint/config":
#   success  no duplicate lines, group file names valid
#   failure  duplicate mapping lines, or a merge conflict with main
#   error    the check could not run
#
# Nothing from the pull request is executed: only its config/ files are read.
#
# Usage: lint-renovate-prs.sh [--dry-run]
#   --dry-run   print the statuses instead of posting them
#
# Environment:
#   GITHUB_REPOSITORY  owner/name of the repository (default: derived from the
#                      origin remote)
#   GH_TOKEN           token used by gh when posting
#   REPO_DIR           git repository to work in (default: this repository)
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
REPO_DIR=${REPO_DIR:-$ROOT}
BASE_REF=origin/main
CONTEXT=lint/config
DRY_RUN=${DRY_RUN:-}

# merged_tree <base> <head>: print the tree object a merge of <head> into
# <base> would produce, or return 1 when the merge conflicts.
merged_tree() {
  local out
  out=$(git -C "$REPO_DIR" merge-tree --write-tree "$1" "$2" 2>/dev/null) || return 1
  printf '%s\n' "$out" | head -1
}

# lint_tree <tree>: check the config/ directory of <tree> and print a one-line
# description of the result. Exit 0 when clean, 1 on duplicate mapping lines,
# 2 when the check cannot run.
lint_tree() {
  local tree=$1 tmp out status count files
  tmp=$(mktemp -d) || return 2
  if ! git -C "$REPO_DIR" archive "$tree" config | tar -x -C "$tmp"; then
    rm -rf "${tmp:?}"
    echo 'config/ not found'
    return 2
  fi
  if ! out=$(CONFIG_DIR="$tmp/config" "$ROOT/scripts/list-groups.sh" 2>&1); then
    rm -rf "${tmp:?}"
    printf '%s\n' "$out" | tail -1 | sed -E "s#${tmp}/##g"
    return 2
  fi
  out=$("$ROOT/scripts/lint-config.sh" "$tmp"/config/*-images.yaml 2>&1)
  status=$?
  rm -rf "${tmp:?}"
  case $status in
    0)
      echo 'No duplicate mapping lines; group file names valid'
      ;;
    1)
      count=$(printf '%s\n' "$out" | grep -c .)
      files=$(printf '%s\n' "$out" | sed -E "s#${tmp}/##g; s/:[0-9]+: duplicate of line [0-9]+$//" | sort -u | paste -sd' ' -)
      printf '%s duplicate mapping line%s in %s\n' "$count" "$([ "$count" -eq 1 ] || echo s)" "$files"
      ;;
    *)
      printf '%s\n' "$out" | tail -1 | sed -E "s#${tmp}/##g"
      ;;
  esac
  return $status
}

# post_status <repository> <sha> <state> <description> <url>
# GitHub truncates descriptions to 140 characters.
post_status() {
  local description
  description=$(printf '%s' "$4" | cut -c1-140)
  if [ -n "$DRY_RUN" ]; then
    printf '%s\t%s\n' "$3" "$description"
    return 0
  fi
  gh api --repo "$1" -X POST "repos/$1/statuses/$2" \
    -f state="$3" -f context="$CONTEXT" -f description="$description" -f target_url="$5" > /dev/null
}

main() {
  local repo pulls number head_sha tree state description target failed=0
  if [ "${1:-}" = --dry-run ]; then
    DRY_RUN=1
  fi
  repo=${GITHUB_REPOSITORY:-$(git -C "$REPO_DIR" remote get-url origin 2>/dev/null | sed -E 's#^(https://[^/]+/|git@[^:]+:)##; s#\.git$##')}
  if [ -z "$repo" ]; then
    echo 'error: cannot determine the repository; set GITHUB_REPOSITORY' >&2
    return 2
  fi
  git -C "$REPO_DIR" fetch --quiet --no-tags origin +refs/heads/main:refs/remotes/origin/main || return 2
  pulls=$(gh pr list --repo "$repo" --state open --author app/github-actions --limit 200 \
    --json number,headRefOid --jq '.[] | "\(.number)\t\(.headRefOid)"') || return 2
  if [ -z "$pulls" ]; then
    echo 'no open Renovate pull requests'
    return 0
  fi
  target=${GITHUB_SERVER_URL:-https://github.com}/$repo/actions/runs/${GITHUB_RUN_ID:-}
  while IFS=$'\t' read -r number head_sha; do
    [ -n "$number" ] || continue
    if ! git -C "$REPO_DIR" fetch --quiet --no-tags origin "+${head_sha}:refs/remotes/pr/${number}/head" 2>/dev/null; then
      state=error
      description='Could not fetch the pull request head'
    elif ! tree=$(merged_tree "$BASE_REF" "$head_sha"); then
      state=failure
      description='Merge conflict with main'
    elif description=$(lint_tree "$tree"); then
      state=success
    else
      case $? in
        1) state=failure ;;
        *) state=error ;;
      esac
    fi
    printf '#%-4s ' "$number"
    if ! post_status "$repo" "$head_sha" "$state" "$description" "$target"; then
      failed=$((failed + 1))
      echo 'error: could not post the status' >&2
    fi
  done <<< "$pulls"
  [ "$failed" -eq 0 ]
}

# Run only when executed, so that the tests can source this file.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
