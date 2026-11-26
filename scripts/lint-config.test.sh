#!/usr/bin/env bash
# Tests for scripts/lint-config.sh. Run: scripts/lint-config.test.sh
set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SCRIPT=$SCRIPT_DIR/lint-config.sh
TMP=$(mktemp -d)
trap 'rm -rf "${TMP:?}"' EXIT

pass=0
fail=0

# indent: print stdin with a four-space indent, for the expected/actual blocks
indent() {
  sed 's/^/    /'
}

# assert_lint <description> <expected-exit-code> <expected-stdout> -- <file>...
assert_lint() {
  local desc=$1 expected_code=$2 expected_out=$3
  shift 4
  local out code
  out=$("$SCRIPT" "$@" 2>"$TMP/stderr")
  code=$?
  if [ "$code" = "$expected_code" ] && [ "$out" = "$expected_out" ]; then
    echo "ok   $desc"
    pass=$((pass + 1))
  else
    echo "FAIL $desc"
    echo "  expected exit $expected_code and stdout:"
    indent <<< "$expected_out"
    echo "  actual   exit $code and stdout:"
    indent <<< "$out"
    echo "  stderr: $(cat "$TMP/stderr")"
    fail=$((fail + 1))
  fi
}

# write <file> [lines...]: create the file with the given lines
write() {
  local file=$1
  shift
  printf '%s\n' "$@" > "$file"
}

A='nginx:1.31.10: "registry.cn-shanghai.aliyuncs.com/ahoo/nginx"'
B='busybox:1.38: "registry.cn-shanghai.aliyuncs.com/ahoo/busybox"'
TAB=$(printf '\t')

write "$TMP/clean.yaml" "$A" "$B"
assert_lint 'distinct lines pass' 0 '' -- "$TMP/clean.yaml"

write "$TMP/dup.yaml" "$A" "$B" "$A"
assert_lint 'an identical line is reported' 1 "$TMP/dup.yaml:3: duplicate of line 1" -- "$TMP/dup.yaml"

write "$TMP/space.yaml" "$A" "  $B  " "$TAB$A$TAB"
assert_lint 'surrounding whitespace is ignored' 1 "$TMP/space.yaml:3: duplicate of line 1" -- "$TMP/space.yaml"

write "$TMP/comments.yaml" "# nginx" "$A" "# nginx" "" "  #   " "$B"
assert_lint 'blank and comment lines are never duplicates' 0 '' -- "$TMP/comments.yaml"

write "$TMP/one.yaml" "$A"
write "$TMP/two.yaml" "$A"
assert_lint 'the same line in two files is not a duplicate' 0 '' -- "$TMP/one.yaml" "$TMP/two.yaml"

write "$TMP/three.yaml" "$A" "$A" "$A"
assert_lint 'every repeat is reported' 1 "$TMP/three.yaml:2: duplicate of line 1
$TMP/three.yaml:3: duplicate of line 1" -- "$TMP/three.yaml"

out=$("$SCRIPT" "$TMP/clean.yaml" "$TMP/nope.yaml" 2>"$TMP/stderr")
code=$?
if [ "$code" = 2 ] && grep -qF "$TMP/nope.yaml: no such file" "$TMP/stderr"; then
  echo 'ok   a missing file is a usage error'
  pass=$((pass + 1))
else
  echo 'FAIL a missing file is a usage error'
  fail=$((fail + 1))
fi

echo
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
