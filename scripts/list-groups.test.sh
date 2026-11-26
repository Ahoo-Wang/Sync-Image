#!/usr/bin/env bash
# Tests for scripts/list-groups.sh. Run: scripts/list-groups.test.sh
set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SCRIPT=$SCRIPT_DIR/list-groups.sh
TMP=$(mktemp -d)
trap 'rm -rf "${TMP:?}"' EXIT

pass=0
fail=0

# assert_output <description> <expected-stdout> <expected-exit-code> [args...]
assert_output() {
  local desc=$1 expected_out=$2 expected_code=$3
  shift 3
  local out code
  out=$(CONFIG_DIR="$TMP/config" "$SCRIPT" "$@" 2>"$TMP/stderr")
  code=$?
  if [ "$out" = "$expected_out" ] && [ "$code" = "$expected_code" ]; then
    echo "ok   $desc"
    pass=$((pass + 1))
  else
    echo "FAIL $desc"
    echo "  expected exit $expected_code and stdout: $expected_out"
    echo "  actual   exit $code and stdout: $out"
    echo "  stderr: $(cat "$TMP/stderr")"
    fail=$((fail + 1))
  fi
}

# assert_stderr <description> <expected-stderr-substring> <expected-exit-code> [args...]
assert_stderr() {
  local desc=$1 expected_err=$2 expected_code=$3
  shift 3
  local out code
  out=$(CONFIG_DIR="$TMP/config" "$SCRIPT" "$@" 2>"$TMP/stderr")
  code=$?
  if [ "$code" = "$expected_code" ] && [ -z "$out" ] && grep -qF -- "$expected_err" "$TMP/stderr"; then
    echo "ok   $desc"
    pass=$((pass + 1))
  else
    echo "FAIL $desc"
    echo "  expected exit $expected_code, empty stdout, stderr containing: $expected_err"
    echo "  actual   exit $code, stdout: $out, stderr: $(cat "$TMP/stderr")"
    fail=$((fail + 1))
  fi
}

# make_config <filename>...: replace the test config dir with these empty files
make_config() {
  rm -rf "${TMP:?}/config"
  mkdir -p "$TMP/config"
  local file
  for file in "$@"; do
    : > "$TMP/config/$file"
  done
}

ALL='{"include":[{"group":"altinity"},{"group":"bitnami"},{"group":"signoz"}]}'

make_config signoz-images.yaml altinity-images.yaml bitnami-images.yaml
assert_output 'no argument lists every group, sorted' "$ALL" 0
assert_output 'an empty argument means all' "$ALL" 0 ''
assert_output '"all" lists every group' "$ALL" 0 all
assert_output 'a known group is listed alone' '{"include":[{"group":"bitnami"}]}' 0 bitnami
assert_stderr 'an unknown group fails and lists the available ones' 'available groups: altinity bitnami signoz ' 1 nope
assert_stderr 'a partial name is not a match' "unknown group 'bit'" 1 bit
assert_stderr 'a regex that happens to match is not a group name' "unknown group 'alt.nity'" 1 'alt.nity'
assert_stderr 'a multi-line argument is not a group name' "unknown group 'bit" 1 $'bit\nbitnami'

make_config altinity-images.yaml auth.yaml notes.md
assert_output 'files that are not *-images.yaml are ignored' '{"include":[{"group":"altinity"}]}' 0

make_config altinity-images.yaml
mkdir -p "$TMP/config/nested"
: > "$TMP/config/nested/deep-images.yaml"
assert_output 'subdirectories are not scanned' '{"include":[{"group":"altinity"}]}' 0

mkdir "$TMP/config/decoy-images.yaml"
assert_output 'a directory named like a group file is ignored' '{"include":[{"group":"altinity"}]}' 0

make_config 'Bad_Name-images.yaml'
assert_stderr 'an invalid group name fails' "invalid group name 'Bad_Name'" 1

make_config
assert_stderr 'a config dir without groups fails' 'no *-images.yaml files' 1

out=$(CONFIG_DIR="$TMP/missing" "$SCRIPT" 2>"$TMP/stderr")
code=$?
if [ "$code" = 1 ] && [ -z "$out" ] && grep -qF 'config directory not found' "$TMP/stderr"; then
  echo 'ok   a missing config dir fails'
  pass=$((pass + 1))
else
  echo 'FAIL a missing config dir fails'
  fail=$((fail + 1))
fi

# Without CONFIG_DIR the script scans the config/ directory of the repo it lives in.
mkdir -p "$TMP/repo/scripts" "$TMP/repo/config"
cp "$SCRIPT" "$TMP/repo/scripts/list-groups.sh"
: > "$TMP/repo/config/only-images.yaml"
out=$(env -u CONFIG_DIR "$TMP/repo/scripts/list-groups.sh" 2>"$TMP/stderr")
code=$?
if [ "$code" = 0 ] && [ "$out" = '{"include":[{"group":"only"}]}' ]; then
  echo 'ok   the default config dir is the one next to the script'
  pass=$((pass + 1))
else
  echo 'FAIL the default config dir is the one next to the script'
  echo "  expected exit 0 and stdout: {\"include\":[{\"group\":\"only\"}]}"
  echo "  actual   exit $code and stdout: $out"
  echo "  stderr: $(cat "$TMP/stderr")"
  fail=$((fail + 1))
fi

echo
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
