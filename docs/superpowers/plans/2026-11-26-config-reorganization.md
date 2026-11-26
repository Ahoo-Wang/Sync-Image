# 配置目录重整 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `config/` 重整为「一组一文件、全部固定版本、只用 regctl 同步」的目录，并让目录成为 workflow 的唯一事实源。

**Architecture:** 分组即文件 `config/<group>-images.yaml`。新增的 `scripts/list-groups.sh` 扫描目录输出 matrix JSON，`sync-images.yml` 据此为每个分组跑一个 job，统一调用现有的 `scripts/sync-image.sh`。新增的 `scripts/lint-config.sh` 报告重复映射行，由独立的 `lint-config.yml` 在 PR 和 push 时运行。四个不自动升级的分组在 `renovate.json` 的 `packageRules` 里冻结。

**Tech Stack:** bash（必须同时兼容 macOS 自带的 3.2 和 ubuntu-latest 的 5.x）、awk、GitHub Actions matrix、Renovate 44（regex customManager + packageRules）、regctl（经 `scripts/sync-image.sh`，不改）。

**Spec:** `docs/superpowers/specs/2026-11-26-config-reorganization-design.md`

## Global Constraints

- 工作目录 `/Users/ahoo/work/ahoo-git/Sync-Image`，分支 `refactor/config-reorganization`（已存在，基于 `origin/main` @ `78ae146`，含 spec 提交）。每个任务开始前用 `git branch --show-current` 确认在这条分支上。
- 不要 push，不要切回或改动本地 `main`（它落后 `origin/main` 10 个提交，与本计划无关）。
- **禁止在本机运行 `scripts/sync-image.sh`**：它会向真实的阿里云 ACR 推送镜像。
- 不改任何 ACR 目标路径。保留的映射行逐字不变，只有各任务里明确给出完整内容的文件才重写。
- 不改 `scripts/sync-image.sh`。
- 新脚本以 `#!/usr/bin/env bash` 开头，`chmod +x`，shellcheck 零警告。不要用 bash 4+ 才有的特性（`mapfile`、`declare -A`、`${var,,}`、`&>>`）。
- 映射文件里的注释用中文（与现有 `gitea-images.yaml`、`linyi-base-images.yaml` 一致）；脚本注释和 README 用英文（与现有脚本、README 一致）。
- 验证命令里凡是多行的，都包在 `bash <<'SCRIPT' … SCRIPT` 里执行。本机默认 shell 是 zsh，`$var:xxx` 这类写法会被 zsh 吃掉。
- 标注「文件完整内容（路径）：」的代码块，就是要写入该文件的全部内容：用 Write 工具整体覆盖，一个字符都不要增删。
- **不要用 `npx --package renovate renovate-config-validator`**：本机 npx 环境缺 `re2` 模块，会对仓库里现有的正则误报 `Invalid regExp`（对未改动的基线也报）。Renovate 配置一律用 Task 8 里的 Docker 命令校验。
- Task 3 到 Task 8 构成一个整体：中间提交在 `main` 上跑不起来（workflow 与配置互相不匹配），合并时必须一起合。
- 提交信息用 conventional commits，中文描述，与仓库现有风格一致。
- 本计划里的代码和期望输出都已在临时目录实测过（bash 3.2.57、awk 20200816、actionlint、shellcheck、Renovate 44.264.0 官方镜像）。

## 文件结构

| 文件 | 职责 | 任务 |
|---|---|---|
| `scripts/list-groups.sh` | 扫描 `config/`，输出分组 matrix JSON | 1 |
| `scripts/list-groups.test.sh` | 上一项的测试 | 1 |
| `scripts/lint-config.sh` | 报告重复映射行 | 2 |
| `scripts/lint-config.test.sh` | 上一项的测试 | 2 |
| `config/clickhouse-images.yaml`、`flink-images.yaml`、`kafka-ui-images.yaml` | 正则 tag 改为固定版本 | 3 |
| `config/bitnami-images.yaml`、`busybox-images.yaml`、`curl-images.yaml`、`nginx-images.yaml`、`signoz-images.yaml` | 从 `commonly` 拆出的分组 | 4 |
| `config/ahoo-images.yaml`、`commonly-images.yaml` | 删除 | 4 |
| `config/metabase-images.yaml`、`cnpg-images.yaml`、`elasticsearch-images.yaml`、`kibana-images.yaml`、`gitea-images.yaml`、`linyi-base-images.yaml`、`otel-images.yaml` | 去重、恢复、清理注释旧行 | 5 |
| `.github/workflows/sync-images.yml` | 目录驱动的 matrix，单引擎 | 6 |
| `config/auth.yaml`、`.github/workflows/sync-metabase.yml` | 删除 | 6 |
| `.github/workflows/lint-config.yml` | 跑测试和重复行检查 | 7 |
| `renovate.json` | 冻结四个分组 | 8 |
| `README.md` | 新的分组规则与新增分组步骤 | 9 |

不动的文件：`config/altinity-images.yaml`、`lobe-chat-images.yaml`、`mirrord-images.yaml`、`new-api-images.yaml`、`nodejs-images.yaml`、`scripts/sync-image.sh`、`.github/workflows/renovate.yml`。

---

### Task 1: `scripts/list-groups.sh`

**Files:**
- Create: `scripts/list-groups.sh`
- Create: `scripts/list-groups.test.sh`

**Interfaces:**
- Produces: `scripts/list-groups.sh [group]`。stdout 一行 `{"include":[{"group":"altinity"},{"group":"bitnami"}]}`，按字母序。环境变量 `CONFIG_DIR` 覆盖扫描目录，默认是脚本所在仓库的 `config/`。参数为 `all` 或空时输出全部分组，否则只输出指定的那一个。退出码 0 成功，1 失败（原因写在 stderr，stdout 为空）。Task 6 的 workflow 和 Task 7 的 lint workflow 依赖这个契约。

- [x] **Step 1: 写失败的测试**

创建 `scripts/list-groups.test.sh`，内容如下：

```bash
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

make_config altinity-images.yaml auth.yaml notes.md
assert_output 'files that are not *-images.yaml are ignored' '{"include":[{"group":"altinity"}]}' 0

make_config altinity-images.yaml
mkdir -p "$TMP/config/nested"
: > "$TMP/config/nested/deep-images.yaml"
assert_output 'subdirectories are not scanned' '{"include":[{"group":"altinity"}]}' 0

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

echo
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
```

- [x] **Step 2: 运行测试，确认因为缺少脚本而失败**

Run: `bash scripts/list-groups.test.sh; echo "exit=$?"`
Expected: 11 行 `FAIL …`，每个失败块里 `actual   exit 127`，stderr 含 `No such file or directory`；最后一行 `0 passed, 11 failed`，`exit=1`。

- [x] **Step 3: 写实现**

创建 `scripts/list-groups.sh`，内容如下：

```bash
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
```

- [x] **Step 4: 加执行权限并运行测试，确认通过**

Run: `chmod +x scripts/list-groups.sh scripts/list-groups.test.sh && scripts/list-groups.test.sh; echo "exit=$?"`
Expected: 11 行 `ok …`，最后一行 `11 passed, 0 failed`，`exit=0`。

- [x] **Step 5: shellcheck**

Run: `shellcheck scripts/list-groups.sh scripts/list-groups.test.sh; echo "exit=$?"`
Expected: 没有输出，`exit=0`。

- [x] **Step 6: 在真实配置上确认**

Run: `scripts/list-groups.sh`
Expected（当前仓库还是整理前的 17 组）:

```
{"include":[{"group":"ahoo"},{"group":"altinity"},{"group":"clickhouse"},{"group":"cnpg"},{"group":"commonly"},{"group":"elasticsearch"},{"group":"flink"},{"group":"gitea"},{"group":"kafka-ui"},{"group":"kibana"},{"group":"linyi-base"},{"group":"lobe-chat"},{"group":"metabase"},{"group":"mirrord"},{"group":"new-api"},{"group":"nodejs"},{"group":"otel"}]}
```

Run: `scripts/list-groups.sh commonly; echo "exit=$?"`
Expected: `{"include":[{"group":"commonly"}]}`，`exit=0`

Run: `scripts/list-groups.sh nope; echo "exit=$?"`
Expected: stderr 为 `error: unknown group 'nope'; available groups: ahoo altinity clickhouse cnpg commonly elasticsearch flink gitea kafka-ui kibana linyi-base lobe-chat metabase mirrord new-api nodejs otel `，`exit=1`

- [x] **Step 7: 提交**

```bash
git add scripts/list-groups.sh scripts/list-groups.test.sh
git commit -m "feat(scripts): 新增 list-groups.sh——从 config/ 目录生成分组清单"
```

---

### Task 2: `scripts/lint-config.sh`

**Files:**
- Create: `scripts/lint-config.sh`
- Create: `scripts/lint-config.test.sh`

**Interfaces:**
- Consumes: 无。
- Produces: `scripts/lint-config.sh [file ...]`，不传参数时检查仓库的 `config/*-images.yaml`。每发现一处重复，向 stdout 打印一行 `<文件>:<行号>: duplicate of line <首次出现的行号>`。退出码 0 无重复，1 有重复，2 文件不存在。重复的定义：同一个文件里去掉首尾空白后完全相同的两条非注释、非空行。Task 5 用它验证去重，Task 7 的 lint workflow 调用它。

- [x] **Step 1: 写失败的测试**

创建 `scripts/lint-config.test.sh`，内容如下：

```bash
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
```

- [x] **Step 2: 运行测试，确认因为缺少脚本而失败**

Run: `bash scripts/lint-config.test.sh; echo "exit=$?"`
Expected: 7 行 `FAIL …`，每个失败块里 `actual   exit 127`；最后一行 `0 passed, 7 failed`，`exit=1`。

- [x] **Step 3: 写实现**

创建 `scripts/lint-config.sh`，内容如下：

```bash
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
```

- [x] **Step 4: 加执行权限并运行测试，确认通过**

Run: `chmod +x scripts/lint-config.sh scripts/lint-config.test.sh && scripts/lint-config.test.sh; echo "exit=$?"`
Expected: 7 行 `ok …`，最后一行 `7 passed, 0 failed`，`exit=0`。

- [x] **Step 5: shellcheck**

Run: `shellcheck scripts/lint-config.sh scripts/lint-config.test.sh; echo "exit=$?"`
Expected: 没有输出，`exit=0`。

- [x] **Step 6: 在真实配置上确认（这是 Task 5 修复前的 RED）**

Run:

```bash
bash <<'SCRIPT'
scripts/lint-config.sh > /tmp/lint-before.txt
echo "exit=$?"
echo "reported: $(grep -c . /tmp/lint-before.txt)"
sed -E 's/:[0-9]+: duplicate of line [0-9]+$//' /tmp/lint-before.txt | sort | uniq -c
head -4 /tmp/lint-before.txt
SCRIPT
```

Expected: `exit=1`，`reported: 51`，各文件 3/2/1/2/1/42。脚本按自身位置定位仓库，与当前目录无关，所以打印的是绝对路径；下面用 `<repo>` 代表仓库根目录：

```
   3 <repo>/config/cnpg-images.yaml
   2 <repo>/config/elasticsearch-images.yaml
   1 <repo>/config/gitea-images.yaml
   2 <repo>/config/kibana-images.yaml
   1 <repo>/config/linyi-base-images.yaml
  42 <repo>/config/metabase-images.yaml
<repo>/config/cnpg-images.yaml:3: duplicate of line 2
<repo>/config/cnpg-images.yaml:4: duplicate of line 2
<repo>/config/cnpg-images.yaml:5: duplicate of line 2
<repo>/config/elasticsearch-images.yaml:2: duplicate of line 1
```

`uniq -c` 各行的先后随 `sort` 的 locale 变化，数字本身必须一致。

- [x] **Step 7: 提交**

```bash
git add scripts/lint-config.sh scripts/lint-config.test.sh
git commit -m "feat(scripts): 新增 lint-config.sh——检查重复映射行"
```

---

### Task 3: 正则 tag 改为固定版本

**Files:**
- Modify（整体覆盖）: `config/clickhouse-images.yaml`
- Modify（整体覆盖）: `config/flink-images.yaml`
- Modify（整体覆盖）: `config/kafka-ui-images.yaml`

**Interfaces:**
- Consumes: Task 2 的 `scripts/lint-config.sh`（用于验证）。
- Produces: 三个文件里只有固定版本的映射行。`clickhouse-images.yaml` 同时收编原在 `commonly` 里的 `26.8.2-alpine` 那一行，Task 4 删除 `commonly` 时依赖这一点。

- [x] **Step 1: 重写 `config/clickhouse-images.yaml`**

文件完整内容（config/clickhouse-images.yaml）：

```yaml
# ClickHouse Server，固定版本。
# Renovate 冻结（见 renovate.json 的 packageRules）：升级时手动改 tag。
# 24.x 各取原正则范围内最新的补丁版；26.8.2-alpine 原在 commonly 分组。
clickhouse/clickhouse-server:24.9.3.128: "registry.cn-shanghai.aliyuncs.com/ahoo/clickhouse"
clickhouse/clickhouse-server:24.10.4.191: "registry.cn-shanghai.aliyuncs.com/ahoo/clickhouse"
clickhouse/clickhouse-server:24.11.5.49: "registry.cn-shanghai.aliyuncs.com/ahoo/clickhouse"
clickhouse/clickhouse-server:24.12.6.70: "registry.cn-shanghai.aliyuncs.com/ahoo/clickhouse"
clickhouse/clickhouse-server:26.8.2-alpine: "registry.cn-shanghai.aliyuncs.com/ahoo/clickhouse"
```

- [x] **Step 2: 重写 `config/flink-images.yaml`**

文件完整内容（config/flink-images.yaml）：

```yaml
# Apache Flink，固定版本。
# Renovate 冻结（见 renovate.json 的 packageRules）：升级时手动改 tag。
# 1.20.5 是原正则 /^1.+/ 范围内最新的版本；实际在用别的版本时改这一行。
apache/flink:1.20.5: "registry.cn-shanghai.aliyuncs.com/ahoo/flink"
```

- [x] **Step 3: 重写 `config/kafka-ui-images.yaml`**

文件完整内容（config/kafka-ui-images.yaml）：

```yaml
# Kafka UI，固定版本。v0.7.2 是该项目最后一个版本。
# Renovate 冻结（见 renovate.json 的 packageRules）：升级时手动改 tag。
provectuslabs/kafka-ui:v0.7.2: "registry.cn-shanghai.aliyuncs.com/ahoo/kafka-ui"
```

- [x] **Step 4: 验证**

Run:

```bash
bash <<'SCRIPT'
for f in config/clickhouse-images.yaml config/flink-images.yaml config/kafka-ui-images.yaml; do
  printf '%s mapping-lines=%s regex-tag-lines=%s\n' "$f" \
    "$(sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//' "$f" | grep -v '^#' | grep -c .)" \
    "$(grep -c ':/' "$f")"
done
scripts/lint-config.sh config/clickhouse-images.yaml config/flink-images.yaml config/kafka-ui-images.yaml; echo "lint exit=$?"
SCRIPT
```

Expected: `regex-tag-lines=0` 表示文件里不再有正则 tag。

```
config/clickhouse-images.yaml mapping-lines=5 regex-tag-lines=0
config/flink-images.yaml mapping-lines=1 regex-tag-lines=0
config/kafka-ui-images.yaml mapping-lines=1 regex-tag-lines=0
lint exit=0
```

- [x] **Step 5: 提交**

```bash
git add config/clickhouse-images.yaml config/flink-images.yaml config/kafka-ui-images.yaml
git commit -m "refactor(config): clickhouse/flink/kafka-ui 由正则 tag 改为固定版本"
```

---

### Task 4: 拆分 `commonly`，删除 `ahoo`

**Files:**
- Create: `config/bitnami-images.yaml`
- Create: `config/busybox-images.yaml`
- Create: `config/curl-images.yaml`
- Create: `config/nginx-images.yaml`
- Create: `config/signoz-images.yaml`
- Delete: `config/commonly-images.yaml`
- Delete: `config/ahoo-images.yaml`

**Interfaces:**
- Consumes: Task 3 已经把 `clickhouse/clickhouse-server:26.8.2-alpine` 那一行收进 `config/clickhouse-images.yaml`；Task 2 的 `scripts/lint-config.sh`。
- Produces: `commonly` 的 18 行映射全部有了明确归属：bitnami 8、signoz 6、busybox 1、curl 1、nginx 1、clickhouse 1。映射行逐字不变。本任务之后 `config/` 下不再有 `commonly` 和 `ahoo`。

- [x] **Step 1: 创建 `config/bitnami-images.yaml`**

文件完整内容（config/bitnami-images.yaml）：

```yaml
# bitnami
bitnamilegacy/redis-cluster:7.2.4-debian-11-r9: "registry.cn-shanghai.aliyuncs.com/ahoo/bitnami:redis-cluster_7.2.4-debian-11-r9"
bitnamilegacy/kafka:3.6.1-debian-11-r1: "registry.cn-shanghai.aliyuncs.com/ahoo/bitnami:kafka_3.6.1-debian-11-r1"
bitnamilegacy/rabbitmq:3.12.12-debian-11-r3: "registry.cn-shanghai.aliyuncs.com/ahoo/bitnami:rabbitmq_3.12.12-debian-11-r3"
bitnamilegacy/zookeeper:3.9.0-debian-11-r36: "registry.cn-shanghai.aliyuncs.com/ahoo/bitnami:zookeeper_3.9.0-debian-11-r36"
bitnamilegacy/mongodb-sharded:7.0.5-debian-11-r20: "registry.cn-shanghai.aliyuncs.com/ahoo/bitnami:mongodb-sharded_7.0.5-debian-11-r20"
bitnamilegacy/mysql:8.3.0-debian-11-r0: "registry.cn-shanghai.aliyuncs.com/ahoo/bitnami:mysql_8.3.0-debian-11-r0"
bitnamilegacy/mysql:9.4.0-debian-12-r0: "registry.cn-shanghai.aliyuncs.com/ahoo/bitnami:mysql_9.4.0-debian-12-r0"
bitnamilegacy/wordpress:6.4.3-debian-11-r0: "registry.cn-shanghai.aliyuncs.com/ahoo/bitnami:wordpress_6.4.3-debian-11-r0"
```

- [x] **Step 2: 创建 `config/busybox-images.yaml`**

文件完整内容（config/busybox-images.yaml）：

```yaml
# busybox
busybox:1.38: "registry.cn-shanghai.aliyuncs.com/ahoo/busybox"
```

- [x] **Step 3: 创建 `config/curl-images.yaml`**

文件完整内容（config/curl-images.yaml）：

```yaml
# curl
curlimages/curl:8.23.0: "registry.cn-shanghai.aliyuncs.com/ahoo/curl"
```

- [x] **Step 4: 创建 `config/nginx-images.yaml`**

文件完整内容（config/nginx-images.yaml）：

```yaml
# nginx
nginx:1.31.10-alpine: "registry.cn-shanghai.aliyuncs.com/ahoo/nginx"
```

- [x] **Step 5: 创建 `config/signoz-images.yaml`**

文件完整内容（config/signoz-images.yaml）：

```yaml
# signoz
groundnuty/k8s-wait-for:v2.0: "registry.cn-shanghai.aliyuncs.com/ahoo/signoz:k8s-wait-for_v2.0"
signoz/signoz-schema-migrator:v0.144.15: "registry.cn-shanghai.aliyuncs.com/ahoo/signoz:signoz-schema-migrator_v0.144.15"
signoz/frontend:1.1.0: "registry.cn-shanghai.aliyuncs.com/ahoo/signoz:frontend_1.1.0"
signoz/signoz-otel-collector:v0.144.18: "registry.cn-shanghai.aliyuncs.com/ahoo/signoz:signoz-otel-collector_v0.144.18"
signoz/alertmanager:0.23.9: "registry.cn-shanghai.aliyuncs.com/ahoo/signoz:alertmanager_0.23.9"
signoz/query-service:0.76.2: "registry.cn-shanghai.aliyuncs.com/ahoo/signoz:query-service_0.76.2"
```

`commonly` 里被注释掉的 `bitnamilegacy/kafka:2.7.0-debian-10-r64` 那一行不带走：按 spec，注释掉的旧映射一律删除，git 历史里查得到。

- [x] **Step 6: 删除两个旧文件**

```bash
git rm config/commonly-images.yaml config/ahoo-images.yaml
```

- [x] **Step 7: 验证：`commonly` 的每一行都还在**

Run:

```bash
bash <<'SCRIPT'
missing=0
while IFS= read -r line; do
  if ! grep -qxF -- "$line" config/*-images.yaml; then
    echo "MISSING: $line"
    missing=$((missing + 1))
  fi
done < <({ git show HEAD:config/commonly-images.yaml; echo; } | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//' | grep -v '^#' | grep .)
echo "commonly lines not found elsewhere: $missing"
scripts/lint-config.sh config/bitnami-images.yaml config/signoz-images.yaml config/busybox-images.yaml config/curl-images.yaml config/nginx-images.yaml; echo "lint exit=$?"
SCRIPT
```

Expected: 没有 `MISSING:` 行，`commonly lines not found elsewhere: 0`，`lint exit=0`。

- [x] **Step 8: 提交**

```bash
git add config/bitnami-images.yaml config/busybox-images.yaml config/curl-images.yaml config/nginx-images.yaml config/signoz-images.yaml
git commit -m "refactor(config): 拆分 commonly 为明确分组，删除 ahoo 分组"
```

---

### Task 5: 去重、恢复 `linyi-base`、清理注释旧行

**Files:**
- Modify（整体覆盖）: `config/metabase-images.yaml`
- Modify（整体覆盖）: `config/cnpg-images.yaml`
- Modify（整体覆盖）: `config/elasticsearch-images.yaml`
- Modify（整体覆盖）: `config/kibana-images.yaml`
- Modify（整体覆盖）: `config/gitea-images.yaml`
- Modify（整体覆盖）: `config/linyi-base-images.yaml`
- Modify（整体覆盖）: `config/otel-images.yaml`

**Interfaces:**
- Consumes: Task 2 的 `scripts/lint-config.sh`（不传参数时检查整个 `config/`）。
- Produces: `config/` 的最终内容：20 个文件、57 行映射、没有重复行。`config/linyi-base-images.yaml` 恢复为提交 `25025d7` 首次加入时的 7 行映射。Task 6 的 workflow 和 Task 8 的 Renovate 冻结都建立在这个最终目录上。

- [x] **Step 1: 改动之前先确认 RED**

本任务修的是两个已经发生的问题。动手之前先用命令把它们钉住，对应 `AGENTS.md` 的「先写失败的测试、亲眼确认 RED」。

Run:

```bash
bash <<'SCRIPT'
for tag in 'nginx:1.23.3:' 'node:14.16.0:' 'node:14.17.3:' 'node:16.8.0:'; do
  if grep -q "^${tag}" config/linyi-base-images.yaml; then echo "ok      $tag"; else echo "MISSING $tag"; fi
done
grep -nE '^(nginx:1\.31\.10|node:14\.21\.3):' config/linyi-base-images.yaml || echo "no corrupted lines"
scripts/lint-config.sh > /tmp/lint-red.txt; echo "lint exit=$? reported=$(grep -c . /tmp/lint-red.txt)"
SCRIPT
```

Expected:

```
MISSING nginx:1.23.3:
MISSING node:14.16.0:
MISSING node:14.17.3:
ok      node:16.8.0:
14:nginx:1.31.10: "registry.cn-shanghai.aliyuncs.com/ahoo/linyi-base:nginx_1.31.10"
16:node:14.21.3: "registry.cn-shanghai.aliyuncs.com/ahoo/linyi-base:node_14.21.3"
17:node:14.21.3: "registry.cn-shanghai.aliyuncs.com/ahoo/linyi-base:node_14.21.3"
lint exit=1 reported=51
```

两个问题分别是：`linyi-base` 里三个存量版本被 Renovate 改掉了；整个 `config/` 有 51 行重复映射。输出和上面不一致就停下来，不要继续。

- [x] **Step 2: 重写 `config/metabase-images.yaml`**

只留最新的稳定版。另外 4 个版本里，`v0.65.3`、`v0.65.0.14-beta`、`v0.65.0-beta` 在 ACR 中不存在，`v0.50.0-RC2` 是 RC。

文件完整内容（config/metabase-images.yaml）：

```yaml
# metabase
metabase/metabase:v0.65.3.7: "registry.cn-shanghai.aliyuncs.com/ahoo/metabase"
```

- [x] **Step 3: 重写 `config/cnpg-images.yaml`**

文件完整内容（config/cnpg-images.yaml）：

```yaml
ghcr.io/cloudnative-pg/cloudnative-pg:1.30.1: "registry.cn-shanghai.aliyuncs.com/ahoo/cnpg"
ghcr.io/cloudnative-pg/postgresql:18.7-system-trixie: "registry.cn-shanghai.aliyuncs.com/ahoo/cnpg:postgresql_18.7-system-trixie"
```

- [x] **Step 4: 重写 `config/elasticsearch-images.yaml`**

文件完整内容（config/elasticsearch-images.yaml）：

```yaml
docker.elastic.co/elasticsearch/elasticsearch:9.5.7: "registry.cn-shanghai.aliyuncs.com/ahoo/elasticsearch:9.5.7"
```

- [x] **Step 5: 重写 `config/kibana-images.yaml`**

文件完整内容（config/kibana-images.yaml）：

```yaml
docker.elastic.co/kibana/kibana:9.5.7: "registry.cn-shanghai.aliyuncs.com/ahoo/kibana:9.5.7"
```

- [x] **Step 6: 重写 `config/gitea-images.yaml`**

`gitea/act_runner` 原来有两行相同的 0.6.1，只留一行。0.2.11 那一行已被 PR #142 改成 0.6.1，`ahoo/act-runner:0.2.11` 这个 tag 本身还在 ACR 里，不受影响。

文件完整内容（config/gitea-images.yaml）：

```yaml
# gitea CI/CD 基础设施镜像（ACK 节点网络无法直连 docker.io，统一同步至 ACR）
# pg/valkey 用独立仓保留原 tag：gitea helm chart 的 wrapper 仅暴露 image.repository 覆盖键
bitnamilegacy/postgresql:17.6.0-debian-12-r4: "registry.cn-shanghai.aliyuncs.com/ahoo/postgresql"
bitnamilegacy/valkey:8.1.3-debian-12-r3: "registry.cn-shanghai.aliyuncs.com/ahoo/valkey"
# DinD sidecar
gitea/act_runner:0.6.1: "registry.cn-shanghai.aliyuncs.com/ahoo/act-runner"
docker:27-dind: "registry.cn-shanghai.aliyuncs.com/ahoo/docker"
# Gitea Actions job 容器
catthehacker/ubuntu:act-22.04: "registry.cn-shanghai.aliyuncs.com/ahoo/catthehacker"
maven:3.9-eclipse-temurin-21: "registry.cn-shanghai.aliyuncs.com/ahoo/maven"
alpine/k8s:1.37.2: "registry.cn-shanghai.aliyuncs.com/ahoo/alpine-k8s"
```

- [x] **Step 7: 重写 `config/linyi-base-images.yaml`**

恢复为提交 `25025d7` 首次加入时的 7 行映射，文件头的盘点说明原样保留，并追加冻结说明。这 7 个 tag 都已确认在 ACR 中。

文件完整内容（config/linyi-base-images.yaml）：

```yaml
# linyi 基础镜像
#
# linyi-k8s（ACK）与 Gitea CI 访问不到 Docker Hub，而存量服务的 Dockerfile 直接 FROM 这些镜像。
# 这里先同步到个人命名空间的单一仓库 linyi-base，再由 linyi/cicd-assets 的 workflow
# 搬进公司命名空间（registry.cn-shanghai.aliyuncs.com/linyeah），服务最终只引用公司命名空间。
#
# 盘点依据（2026-11-24）：70 个迁移仓当前默认分支里指向 Docker Hub 的 FROM，共 7 个镜像、25 处：
#   adoptopenjdk/openjdk11  11 个仓，JDK_VERSION 全部是 jdk11u-centos-nightly-slim
#   nginx 1.23.3 / latest   4 个仓
#   node 14.16.0 / 14.17.3 / 16.8.0   3 个仓
#   busybox latest          1 个仓
# tag 用「镜像名_原 tag」的复合写法，集中在一个仓库里。
#
# Renovate 冻结（见 renovate.json 的 packageRules）：这些版本必须与存量 Dockerfile 一致，升级时手动改 tag。
adoptopenjdk/openjdk11:jdk11u-centos-nightly-slim: "registry.cn-shanghai.aliyuncs.com/ahoo/linyi-base:adoptopenjdk-openjdk11_jdk11u-centos-nightly-slim"
nginx:1.23.3: "registry.cn-shanghai.aliyuncs.com/ahoo/linyi-base:nginx_1.23.3"
nginx:latest: "registry.cn-shanghai.aliyuncs.com/ahoo/linyi-base:nginx_latest"
node:14.16.0: "registry.cn-shanghai.aliyuncs.com/ahoo/linyi-base:node_14.16.0"
node:14.17.3: "registry.cn-shanghai.aliyuncs.com/ahoo/linyi-base:node_14.17.3"
node:16.8.0: "registry.cn-shanghai.aliyuncs.com/ahoo/linyi-base:node_16.8.0"
busybox:latest: "registry.cn-shanghai.aliyuncs.com/ahoo/linyi-base:busybox_latest"
```

- [x] **Step 8: 重写 `config/otel-images.yaml`**

三行映射不变，只删掉末尾注释掉的 `kube-rbac-proxy` 那一行。

文件完整内容（config/otel-images.yaml）：

```yaml
# otel
otel/autoinstrumentation-java:2.34.0: "registry.cn-shanghai.aliyuncs.com/ahoo/otel:autoinstrumentation-java_2.34.0"
ghcr.io/open-telemetry/opentelemetry-collector-releases/opentelemetry-collector:0.162.0: "registry.cn-shanghai.aliyuncs.com/ahoo/otel:opentelemetry-collector_0.162.0"
ghcr.io/open-telemetry/opentelemetry-operator/opentelemetry-operator:v0.163.0: "registry.cn-shanghai.aliyuncs.com/ahoo/otel:opentelemetry-operator_v0.163.0"
```

- [x] **Step 9: 验证（GREEN）**

Run:

```bash
bash <<'SCRIPT'
for tag in 'nginx:1.23.3:' 'node:14.16.0:' 'node:14.17.3:' 'node:16.8.0:'; do
  if grep -q "^${tag}" config/linyi-base-images.yaml; then echo "ok      $tag"; else echo "MISSING $tag"; fi
done
grep -nE '^(nginx:1\.31\.10|node:14\.21\.3):' config/linyi-base-images.yaml || echo "no corrupted lines"
scripts/lint-config.sh; echo "lint exit=$?"
for f in config/*-images.yaml; do
  printf '%s %s\n' "$f" "$(sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//' "$f" | grep -v '^#' | grep -c .)"
done | LC_ALL=C sort
SCRIPT
```

Expected: 4 行 `ok`，`no corrupted lines`，`lint exit=0`（lint 没有任何输出），然后是下面 20 行，合计 57：

```
ok      nginx:1.23.3:
ok      node:14.16.0:
ok      node:14.17.3:
ok      node:16.8.0:
no corrupted lines
lint exit=0
config/altinity-images.yaml 2
config/bitnami-images.yaml 8
config/busybox-images.yaml 1
config/clickhouse-images.yaml 5
config/cnpg-images.yaml 2
config/curl-images.yaml 1
config/elasticsearch-images.yaml 1
config/flink-images.yaml 1
config/gitea-images.yaml 7
config/kafka-ui-images.yaml 1
config/kibana-images.yaml 1
config/linyi-base-images.yaml 7
config/lobe-chat-images.yaml 1
config/metabase-images.yaml 1
config/mirrord-images.yaml 1
config/new-api-images.yaml 1
config/nodejs-images.yaml 6
config/nginx-images.yaml 1
config/otel-images.yaml 3
config/signoz-images.yaml 6
```

- [x] **Step 10: 提交**

```bash
git add config/metabase-images.yaml config/cnpg-images.yaml config/elasticsearch-images.yaml config/kibana-images.yaml config/gitea-images.yaml config/linyi-base-images.yaml config/otel-images.yaml
git commit -m "fix(config): 去重 51 行重复映射，恢复被 Renovate 改掉的 linyi-base 版本"
```

---

### Task 6: workflow 改为目录驱动、单引擎

**Files:**
- Modify（整体覆盖）: `.github/workflows/sync-images.yml`
- Delete: `.github/workflows/sync-metabase.yml`
- Delete: `config/auth.yaml`

**Interfaces:**
- Consumes: Task 1 的 `scripts/list-groups.sh [group]`（stdout 一行 matrix JSON，`CONFIG_DIR` 默认就是仓库的 `config/`）；Task 3 到 Task 5 之后的最终 `config/`；现有的 `scripts/sync-image.sh <images-file>`，不改。
- Produces: `plan` job 输出 `matrix`；`sync` job 按 `matrix.group` 给每个分组跑一个 job，统一调用 `scripts/sync-image.sh "config/<group>-images.yaml"`。手动触发的输入 `group` 是字符串（分组名或 `all`）。metabase 从此走主 workflow，push 和手动触发都会同步它。

matrix 必须来自某个 job 的输出才能用在 `strategy.matrix` 里，所以多了一个很轻的 `plan` job，而不是把清单写在 workflow 里。

- [x] **Step 1: 重写 `.github/workflows/sync-images.yml`**

文件完整内容（.github/workflows/sync-images.yml）：

```yaml
name: Sync Images
on:
  workflow_dispatch:
    inputs:
      group:
        description: "Image group to sync: a name from config/*-images.yaml, or 'all'"
        required: true
        default: "all"
        type: string
  push:
    branches: [main]
    paths:
      - "config/*-images.yaml"

env:
  REGCTL_VERSION: v0.11.5

jobs:
  plan:
    name: List image groups
    runs-on: ubuntu-latest
    outputs:
      matrix: ${{ steps.list.outputs.matrix }}
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      # The config directory is the single source of truth: a group is a file.
      - name: List groups
        id: list
        env:
          GROUP: ${{ inputs.group || 'all' }}
        run: |
          matrix=$(scripts/list-groups.sh "$GROUP")
          echo "matrix=$matrix" >> "$GITHUB_OUTPUT"

  sync:
    name: Sync ${{ matrix.group }} images
    needs: plan
    runs-on: ubuntu-latest
    # Every group is synced by scripts/sync-image.sh (regctl), which copies
    # linux/amd64 + linux/arm64 and strips the OCI attestation manifests that
    # Aliyun ACR rejects.
    concurrency:
      group: sync-images-${{ matrix.group }}
      cancel-in-progress: true
    strategy:
      fail-fast: false
      matrix: ${{ fromJSON(needs.plan.outputs.matrix) }}
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Setup regctl
        run: |
          curl -fsSL "https://github.com/regclient/regclient/releases/download/${REGCTL_VERSION}/regctl-linux-amd64" -o /usr/local/bin/regctl
          chmod +x /usr/local/bin/regctl
          printf '%s' "$ALIYUN_CR_PASSWORD" | regctl registry login registry.cn-shanghai.aliyuncs.com -u "$ALIYUN_CR_USERNAME" --pass-stdin
        env:
          ALIYUN_CR_USERNAME: ${{ secrets.ALIYUN_CR_USERNAME }}
          ALIYUN_CR_PASSWORD: ${{ secrets.ALIYUN_CR_PASSWORD }}

      - name: Sync ${{ matrix.group }} images
        run: scripts/sync-image.sh "config/${{ matrix.group }}-images.yaml"
```

- [x] **Step 2: 删除不再需要的两个文件**

```bash
git rm .github/workflows/sync-metabase.yml config/auth.yaml
```

- [x] **Step 3: 验证**

Run:

```bash
bash <<'SCRIPT'
actionlint .github/workflows/sync-images.yml; echo "actionlint exit=$?"
scripts/list-groups.sh | tr ',' '\n' | grep -c '"group"'
GITHUB_OUTPUT=$(mktemp)
GROUP=all
matrix=$(scripts/list-groups.sh "$GROUP")
echo "matrix=$matrix" >> "$GITHUB_OUTPUT"
head -c 90 "$GITHUB_OUTPUT"; echo
echo "lines in GITHUB_OUTPUT: $(grep -c . "$GITHUB_OUTPUT")"
rm -f "$GITHUB_OUTPUT"
grep -rn -e 'image-sync-action' -e 'REGCTL_GROUPS' -e 'auth_file' -e 'auth.yaml' -e 'sync-metabase' .github || echo "no stale references"
SCRIPT
```

Expected:

```
actionlint exit=0
20
matrix={"include":[{"group":"altinity"},{"group":"bitnami"},{"group":"busybox"},{"group":"
lines in GITHUB_OUTPUT: 1
no stale references
```

第三行是在本地模拟 `plan` job 写入 `$GITHUB_OUTPUT` 的那一行。最后一项只查 `.github`：`scripts/list-groups.test.sh` 把 `auth.yaml` 当作样例文件名，不算残留引用。README 里还会提到 `sync-metabase`，Task 9 会重写，所以这里不检查 README。

- [x] **Step 4: 提交**

```bash
git add .github/workflows/sync-images.yml
git commit -m "ci(sync): 目录即唯一事实源，所有分组经 regctl 同步"
```

---

### Task 7: `lint-config.yml`

**Files:**
- Create: `.github/workflows/lint-config.yml`

**Interfaces:**
- Consumes: `scripts/list-groups.test.sh` 和 `scripts/lint-config.test.sh`（成功时最后一行是 `N passed, 0 failed`，并用退出码表示结果）；`scripts/lint-config.sh`（不传参数时检查整个 `config/`，重复行时退出码 1）。
- Produces: 一个与 `sync-images.yml` 相互独立的 workflow，只在 `config/`、`scripts/` 或它自己有改动时运行，报告结果但不阻塞同步。

- [x] **Step 1: 创建 `.github/workflows/lint-config.yml`**

文件完整内容（.github/workflows/lint-config.yml）：

```yaml
name: Lint Config
on:
  pull_request:
    paths:
      - "config/**"
      - "scripts/**"
      - ".github/workflows/lint-config.yml"
  push:
    branches: [main]
    paths:
      - "config/**"
      - "scripts/**"
      - ".github/workflows/lint-config.yml"

jobs:
  lint:
    name: Lint config
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Unit tests
        run: |
          scripts/list-groups.test.sh
          scripts/lint-config.test.sh

      - name: Lint image configs
        run: scripts/lint-config.sh
```

- [x] **Step 2: 验证**

在本地按 workflow 里的顺序跑同样的三步，并确认脚本的执行权限被 git 记录下来。

Run:

```bash
bash <<'SCRIPT'
actionlint .github/workflows/lint-config.yml; echo "actionlint exit=$?"
scripts/list-groups.test.sh | tail -1
scripts/lint-config.test.sh | tail -1
scripts/lint-config.sh; echo "lint exit=$?"
git ls-files -s scripts | awk '{print $1, $4}'
SCRIPT
```

Expected:

```
actionlint exit=0
11 passed, 0 failed
7 passed, 0 failed
lint exit=0
100755 scripts/lint-config.sh
100755 scripts/lint-config.test.sh
100755 scripts/list-groups.sh
100755 scripts/list-groups.test.sh
100755 scripts/sync-image.sh
```

- [x] **Step 3: 提交**

```bash
git add .github/workflows/lint-config.yml
git commit -m "ci(lint): 新增 lint-config workflow"
```

---

### Task 8: Renovate 冻结四个分组

**Files:**
- Modify（整体覆盖）: `renovate.json`

**Interfaces:**
- Consumes: 无。
- Produces: `packageRules` 里的一条规则，对 `config/clickhouse-images.yaml`、`config/flink-images.yaml`、`config/kafka-ui-images.yaml`、`config/linyi-base-images.yaml` 设 `enabled: false`。`customManagers` 不变。

冻结在 Renovate 下一次运行时才生效：那时它会自动关闭 #153（`linyi-base` 的 `node:16.8.0`）和 #77（clickhouse 升到 26.11.2）。

- [x] **Step 1: 重写 `renovate.json`**

只新增 `packageRules` 一项，其余逐字不变。

文件完整内容（renovate.json）：

```json
{
  "$schema": "https://docs.renovatebot.com/renovate-schema.json",
  "extends": ["config:recommended"],
  "timezone": "Asia/Shanghai",
  "labels": ["dependencies"],
  "commitMessagePrefix": "fix(deps)",
  "commitMessageTopic": "{{depName}}",
  "rangeStrategy": "bump",
  "lockFileMaintenance": { "enabled": false },
  "prConcurrentLimit": 20,
  "prHourlyLimit": 20,
  "autoReplaceGlobalMatch": true,
  "packageRules": [
    {
      "description": "Frozen groups: pinned versions change only by hand",
      "matchFileNames": [
        "config/clickhouse-images.yaml",
        "config/flink-images.yaml",
        "config/kafka-ui-images.yaml",
        "config/linyi-base-images.yaml"
      ],
      "enabled": false
    }
  ],
  "customManagers": [
    {
      "description": "Update container image tags in config/*-images.yaml; with autoReplaceGlobalMatch the version is replaced in both the source tag and the destination tag",
      "customType": "regex",
      "fileMatch": ["^config/.*-images\\.yaml$"],
      "matchStrings": [
        "(?m)^(?<depName>[^\\s:#][^:\\s]*):(?<currentValue>[A-Za-z0-9][A-Za-z0-9._-]*):\\s\"[^\"]*\"\\s*$"
      ],
      "datasourceTemplate": "docker"
    }
  ]
}
```

- [x] **Step 2: 用官方镜像校验**

Run:

```bash
bash <<'SCRIPT'
docker run --rm -v "$PWD:/tmp/repo" -w /tmp/repo renovate/renovate:latest renovate-config-validator renovate.json > /tmp/renovate-validate.txt 2>&1
echo "validator exit=$?"
tail -1 /tmp/renovate-validate.txt
python3 - <<'PY'
import json
print(json.load(open("renovate.json"))["packageRules"])
PY
SCRIPT
```

Expected: `validator exit=0`，下一行是 ` INFO: Config validated successfully against 1 file(s)`，最后打印出那条规则（一个只含一个元素的列表，`enabled` 为 `False`，`matchFileNames` 是上面四个路径）。

- [x] **Step 3: 提交**

```bash
git add renovate.json
git commit -m "chore(renovate): 冻结 clickhouse/flink/kafka-ui/linyi-base"
```

---

### Task 9: README

**Files:**
- Modify（整体覆盖）: `README.md`

**Interfaces:**
- Consumes: Task 6、Task 7、Task 8 产出的文件路径（README 里链接到它们）。
- Produces: 无代码接口。README 描述的是 Task 1 到 Task 8 之后的真实状态。

- [x] **Step 1: 重写 `README.md`**

文件完整内容（README.md）：

```markdown
# Sync-Image

Sync container images from upstream registries (Docker Hub, GHCR, ...) to
Aliyun ACR (`registry.cn-shanghai.aliyuncs.com/ahoo`).

## Usage

- Image groups live in [`config/`](./config), one `*-images.yaml` per group. A
  group maps to one ACR repository (`bitnami-images.yaml` → `ahoo/bitnami`).
  The one exception is `gitea-images.yaml`, which is grouped by purpose
  (Gitea CI) and covers several repositories.
- Each line maps a pinned source image tag to a destination:
  `"<src-image>:<tag>": "<dst-repo>[:<dst-tag>]"`. Tag regexes are not used:
  they sync far too many tags.
- [`.github/workflows/sync-images.yml`](./.github/workflows/sync-images.yml)
  derives its matrix from the files in `config/`. To add a group, add
  `config/<group>-images.yaml` and push to `main`. The workflow can also be
  triggered by hand with a group name or `all`.
- Every group is synced by [`scripts/sync-image.sh`](./scripts/sync-image.sh)
  (regctl). It copies `linux/amd64` and `linux/arm64` only, and reassembles
  the index without OCI 1.1 attestation manifests, which Aliyun ACR rejects
  with `unknown manifest class for application/vnd.oci.empty.v1+json`. A tag
  that already exists at the destination is skipped.
- [Renovate](./renovate.json) keeps image tags up to date via
  `customManagers`; merging its PRs triggers the sync. Frozen groups
  (`clickhouse`, `flink`, `kafka-ui`, `linyi-base`) are switched off in
  `packageRules`: change their tags by hand.
- [`.github/workflows/lint-config.yml`](./.github/workflows/lint-config.yml)
  runs the script tests and reports duplicate mapping lines. Renovate bumps
  every line of the same image to the same version, so lines that used to be
  different versions can become identical; delete the extra lines.

Required repository secrets: `ALIYUN_CR_USERNAME`, `ALIYUN_CR_PASSWORD`.
```

- [x] **Step 2: 验证**

README 里链接到的路径都必须存在，并且不再提到已经删掉的东西。

Run:

```bash
bash <<'SCRIPT'
for p in config .github/workflows/sync-images.yml scripts/sync-image.sh renovate.json .github/workflows/lint-config.yml; do
  if [ -e "$p" ]; then echo "ok      $p"; else echo "MISSING $p"; fi
done
grep -n -e 'image-syncer' -e 'commonly' -e 'sync-metabase' -e 'auth.yaml' README.md || echo "no stale references in README"
SCRIPT
```

Expected:

```
ok      config
ok      .github/workflows/sync-images.yml
ok      scripts/sync-image.sh
ok      renovate.json
ok      .github/workflows/lint-config.yml
no stale references in README
```

- [x] **Step 3: 提交**

```bash
git add README.md
git commit -m "docs: 更新 README 的分组规则与新增分组步骤"
```

---

### Task 10: 最终核对

**Files:**
- 无新文件，只运行检查。

**Interfaces:**
- Consumes: Task 1 到 Task 9 的全部产出。
- Produces: 一份与 spec 第 9 节一一对应的核对结果。任何一项和期望不符，都回到对应的任务修正，不要在这里绕过。

- [x] **Step 1: 测试与静态检查**

Run:

```bash
bash <<'SCRIPT'
scripts/list-groups.test.sh | tail -1
scripts/lint-config.test.sh | tail -1
scripts/lint-config.sh; echo "lint exit=$?"
shellcheck scripts/*.sh; echo "shellcheck exit=$?"
actionlint .github/workflows/sync-images.yml .github/workflows/lint-config.yml; echo "actionlint exit=$?"
SCRIPT
```

Expected:

```
11 passed, 0 failed
7 passed, 0 failed
lint exit=0
shellcheck exit=0
actionlint exit=0
```

- [x] **Step 2: 映射比对（相对基线 `78ae146`）**

基线是本分支的起点，也就是 `origin/main` 整理前的状态。比对以去重后的整行为单位，差异必须恰好是下面两组：移除 14 行、新增 9 行，其余 48 行逐字相同。多一行少一行都算失败。

分支在 push 前变基到了 `origin/main` @ `d134031`（比 `78ae146` 多 3 个 Renovate 的版本升级提交）。以变基目标为基线重跑这一步，结果相同。

Run:

```bash
bash <<'SCRIPT'
norm() { sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//' | grep -v '^#' | grep . | LC_ALL=C sort -u; }
before=$(for f in $(git ls-tree --name-only 78ae146 config/ | grep -- '-images.yaml$'); do git show "78ae146:${f}"; echo; done | norm)
after=$(for f in config/*-images.yaml; do cat "$f"; echo; done | norm)
echo "before unique: $(printf '%s\n' "$before" | grep -c .)   after unique: $(printf '%s\n' "$after" | grep -c .)   unchanged: $(comm -12 <(printf '%s\n' "$before") <(printf '%s\n' "$after") | grep -c .)"
echo "--- removed:"
comm -23 <(printf '%s\n' "$before") <(printf '%s\n' "$after")
echo "--- added:"
comm -13 <(printf '%s\n' "$before") <(printf '%s\n' "$after")
SCRIPT
```

Expected:

```
before unique: 62   after unique: 57   unchanged: 48
--- removed:
ahoowang/cosky-rest-api:3.3.14: "registry.cn-shanghai.aliyuncs.com/ahoo/cosky"
ahoowang/pigeon-discovery:0.9.30: "registry.cn-shanghai.aliyuncs.com/ahoo/me:pigeon-discovery_0.9.30"
apache/flink:/^1.+/: "registry.cn-shanghai.aliyuncs.com/ahoo/flink"
clickhouse/clickhouse-server:/^24.10.+/: "registry.cn-shanghai.aliyuncs.com/ahoo/clickhouse"
clickhouse/clickhouse-server:/^24.11.+/: "registry.cn-shanghai.aliyuncs.com/ahoo/clickhouse"
clickhouse/clickhouse-server:/^24.12.+/: "registry.cn-shanghai.aliyuncs.com/ahoo/clickhouse"
clickhouse/clickhouse-server:/^24.9.+/: "registry.cn-shanghai.aliyuncs.com/ahoo/clickhouse"
metabase/metabase:v0.50.0-RC2: "registry.cn-shanghai.aliyuncs.com/ahoo/metabase"
metabase/metabase:v0.65.0-beta: "registry.cn-shanghai.aliyuncs.com/ahoo/metabase"
metabase/metabase:v0.65.0.14-beta: "registry.cn-shanghai.aliyuncs.com/ahoo/metabase"
metabase/metabase:v0.65.3: "registry.cn-shanghai.aliyuncs.com/ahoo/metabase"
nginx:1.31.10: "registry.cn-shanghai.aliyuncs.com/ahoo/linyi-base:nginx_1.31.10"
node:14.21.3: "registry.cn-shanghai.aliyuncs.com/ahoo/linyi-base:node_14.21.3"
provectuslabs/kafka-ui:/^v0.7.+/: "registry.cn-shanghai.aliyuncs.com/ahoo/kafka-ui"
--- added:
apache/flink:1.20.5: "registry.cn-shanghai.aliyuncs.com/ahoo/flink"
clickhouse/clickhouse-server:24.10.4.191: "registry.cn-shanghai.aliyuncs.com/ahoo/clickhouse"
clickhouse/clickhouse-server:24.11.5.49: "registry.cn-shanghai.aliyuncs.com/ahoo/clickhouse"
clickhouse/clickhouse-server:24.12.6.70: "registry.cn-shanghai.aliyuncs.com/ahoo/clickhouse"
clickhouse/clickhouse-server:24.9.3.128: "registry.cn-shanghai.aliyuncs.com/ahoo/clickhouse"
nginx:1.23.3: "registry.cn-shanghai.aliyuncs.com/ahoo/linyi-base:nginx_1.23.3"
node:14.16.0: "registry.cn-shanghai.aliyuncs.com/ahoo/linyi-base:node_14.16.0"
node:14.17.3: "registry.cn-shanghai.aliyuncs.com/ahoo/linyi-base:node_14.17.3"
provectuslabs/kafka-ui:v0.7.2: "registry.cn-shanghai.aliyuncs.com/ahoo/kafka-ui"
```

移除的 14 行分别是：`ahoo` 2 行、clickhouse 正则 4 行、flink 和 kafka-ui 正则各 1 行、`linyi-base` 被 Renovate 改出来的 2 行、metabase 的 4 个旧版本。新增的 9 行是：clickhouse 的 4 个固定版本、flink 和 kafka-ui 各 1 个固定版本、`linyi-base` 恢复的 3 行。

- [x] **Step 3: 工作区、提交历史和最终目录**

Run:

```bash
bash <<'SCRIPT'
git status --porcelain
git log --oneline 78ae146..HEAD
LC_ALL=C ls config
SCRIPT
```

Expected: 第一条命令最多只有本计划文件这一行（执行过程中勾选步骤会改动它），不能出现其他文件。提交历史从新到旧是 Task 9 到 Task 1 的 9 个提交，再往上是本计划和 spec 的提交。`config/` 下恰好是这 20 个文件：

```
altinity-images.yaml
bitnami-images.yaml
busybox-images.yaml
clickhouse-images.yaml
cnpg-images.yaml
curl-images.yaml
elasticsearch-images.yaml
flink-images.yaml
gitea-images.yaml
kafka-ui-images.yaml
kibana-images.yaml
linyi-base-images.yaml
lobe-chat-images.yaml
metabase-images.yaml
mirrord-images.yaml
new-api-images.yaml
nodejs-images.yaml
nginx-images.yaml
otel-images.yaml
signoz-images.yaml
```

- [x] **Step 4: 提交计划文件的勾选状态**

执行过程中本计划里的 `- [ ]` 如果被改成了 `- [x]`，在这里提交：

```bash
git add docs/superpowers/plans/2026-11-26-config-reorganization.md
git commit -m "docs(plan): 勾选配置目录重整的已完成步骤" || echo "nothing to commit"
```

到这里为止不要 push。是否 push、开 PR、合并，由用户决定。

---

## 合并之后（不在本计划内执行）

- **首轮同步**：合并会触发 `sync-images.yml`。除 `metabase:v0.65.3.7` 需要真正同步（它在 ACR 里还不存在）之外，其余每个 tag 都应输出 `skip: ... (destination image exists)`。
- **开放的 Renovate PR**：Renovate 下次运行后，#153 和 #77 应被自动关闭，#130 应被重建为只改 1 行。合并后核对这些 PR 的状态。
- **ACR 里多出来的 tag**：`ahoo/linyi-base:nginx_1.31.10`、`ahoo/linyi-base:node_14.21.3`（11-25 的同步产生的）和 `ahoo/flink` 的 461 个历史 tag 不在本次范围内，需要时手动清理。
- **flink 的 `1.20.5` 是占位**：实际在用别的版本时，改 `config/flink-images.yaml` 里那一行即可。
- **`linyi-base` 的去留**：确认 `linyi/cicd-assets` 已把 7 个 tag 搬进 `linyeah` 之后，可以删除 `config/linyi-base-images.yaml`，并从 `renovate.json` 的 `packageRules` 里去掉它。
