# Sync-Image 配置目录重整 设计

- 日期：2026-11-26
- 状态：已实现（分支 `refactor/config-reorganization`）
- 基线：`origin/main` @ `78ae146`（设计时的 `origin/main`。实现时分支变基到了 `d134031`，只多出 3 个 Renovate 的版本升级提交；以它为基线核对，结果相同）

## 1. 背景

`config/` 平铺着 17 个分组，存在四类问题，均已逐项核对：

1. **重复映射行 51 行。** `origin/main` 共 113 行映射，去重后 62 行。根因是 Renovate 把同一镜像的每一行都升级到最新版本：
   - `metabase` 47 行只剩 5 个不同版本；
   - `gitea/act_runner` 的 0.2.11 被 PR #142 并成 0.6.1；
   - `linyi-base` 的 `node:14.16.0`、`node:14.17.3` 被 PR #152 并成 `14.21.3`，`nginx:1.23.3` 被 PR #151 改成 `1.31.10`。
2. **分组标准混杂。** `commonly` 同时装着 busybox、curl、signoz、bitnami、clickhouse、nginx。分组的边界由同步引擎（regctl 或 image-syncer）决定，而不是由内容决定。
3. **正则 tag 导致镜像过多。** `apache/flink:/^1.+/` 在 ACR 里同步了 461 个 tag，`clickhouse-server` 的四条正则命中 72 个。
4. **登记点分散。** 新增分组要同时改 `workflow_dispatch` 的 choice、matrix 和 `REGCTL_GROUPS`。`metabase` 游离在主 workflow 之外，自 2026-08-20 起没有同步过，配置里的 `v0.65.3.7` 在 ACR 中不存在。

## 2. 目标与非目标

目标：

- 全部使用固定版本，不再使用正则 tag。
- 一个分组对应 ACR 上的一个仓库；取消 `commonly` 和 `ahoo`。
- 目录是唯一事实源：新增分组只需新增一个文件。
- 只保留一种同步方式（`scripts/sync-image.sh`）。
- 不需要自动升级的分组对 Renovate 冻结。

非目标：

- 不改任何 ACR 目标路径。保留的映射逐行不变，只是换文件或删重复。
- 不清理 ACR 里已有的历史 tag。
- 不恢复被 Renovate 抹平的旧版本线（Elasticsearch 7.x/8.x、PostgreSQL 15/16/17、metabase 历史版本）。`linyi-base` 是例外，见 7.4。
- 不改 `scripts/sync-image.sh`。

## 3. 分组规则

- 每个分组是一个文件 `config/<group>-images.yaml`，保持平铺，不建子目录。
- **一个分组对应 ACR 上的一个仓库**，例如 `bitnami` 对应 `ahoo/bitnami`，`nginx` 对应 `ahoo/nginx`。唯一的例外是 `gitea`：它按用途分组（Gitea CI 基础设施，7 个镜像落在 7 个仓库），文件头注释里写明。
- 分组名只允许 `^[a-z0-9]+(-[a-z0-9]+)*$`。
- **冻结分组**：`clickhouse`、`flink`、`kafka-ui`、`linyi-base`。冻结的含义是 Renovate 不为该文件里的任何行提升级 PR，版本只通过手动改 tag 变更。其余分组交给 Renovate 维护。四个冻结文件的文件头都注明「Renovate 冻结，升级时手动改 tag」。

整理后的目录：

```
config/
  # 跟新（Renovate 维护版本）
  altinity-images.yaml       bitnami-images.yaml      busybox-images.yaml
  cnpg-images.yaml           curl-images.yaml         elasticsearch-images.yaml
  gitea-images.yaml          kibana-images.yaml       lobe-chat-images.yaml
  metabase-images.yaml       mirrord-images.yaml      new-api-images.yaml
  nodejs-images.yaml         nginx-images.yaml        otel-images.yaml
  signoz-images.yaml
  # 冻结（固定版本）
  clickhouse-images.yaml     flink-images.yaml        kafka-ui-images.yaml
  linyi-base-images.yaml
```

## 4. 同步方式

- 所有分组都通过 `scripts/sync-image.sh` 同步，脚本本身不改。
- 删除 `config/auth.yaml` 和对 `hhyasdf/image-sync-action` 的使用。regctl 的登录方式不变（`regctl registry login --pass-stdin`）。
- **行为变化（仅此一处）**：原先走 image-syncer 的 8 个分组（`clickhouse`、`elasticsearch`、`flink`、`kafka-ui`、`kibana`、`lobe-chat`、`new-api`、`nodejs`）全部换到 regctl 脚本。今后新同步的 tag 只带 `linux/amd64` 和 `linux/arm64`，不含 attestation；已有的 tag 不变。这 8 组要同步的源镜像都已用 regctl 匿名读取验证过，两个平台都能取到。

## 5. Workflow

### 5.1 `sync-images.yml`

触发方式不变：push 到 `main` 且改动了 `config/*-images.yaml`，或手动触发。手动触发的 `group` 输入从 `choice` 改为 `string`，默认 `all`。

```yaml
jobs:
  plan:
    # 扫描 config/，输出 matrix: {"include":[{"group":"altinity"},...]}
    outputs:
      matrix: ${{ steps.list.outputs.matrix }}
  sync:
    needs: plan
    strategy:
      fail-fast: false
      matrix: ${{ fromJSON(needs.plan.outputs.matrix) }}
    # 每个分组两个步骤：Setup regctl、./scripts/sync-image.sh ./config/<group>-images.yaml
```

- `REGCTL_GROUPS`、`SELECTED` 和按引擎分支的步骤全部删除。
- `concurrency.group` 仍为 `sync-images-<group>`。
- 删除 `sync-metabase.yml`。`metabase` 成为主 workflow 里的普通分组，合并 Renovate PR 后会自动同步。

### 5.2 `lint-config.yml`（新增）

在 `pull_request` 和 push 到 `main` 时运行，路径过滤为 `config/**`、`scripts/**` 和该 workflow 自身。六个步骤，任何一步失败都算检查失败：

1. 在真实的 `config/` 上运行 `scripts/list-groups.sh`，校验分组文件名。分组名就是文件名，一个不合规的文件会让同步 workflow 的 `plan` job 失败、所有分组都不同步，所以要在 PR 阶段拦住；
2. 跑 `scripts/list-groups.test.sh`；
3. 跑 `scripts/lint-config.test.sh`；
4. 跑 `scripts/lint-renovate-prs.test.sh`；
5. 跑 `shellcheck -x -P SCRIPTDIR scripts/*.sh`；
6. 跑 `scripts/lint-config.sh`。

这个检查只报告，不阻塞同步（与 `sync-images.yml` 是两个独立的 workflow）。两个 workflow 都声明了 `permissions: contents: read`。

Renovate 用 `GITHUB_TOKEN` 推送分支，它触发的 `pull_request` 运行会停在 `action_required`，要人工批准才会执行。因此 `renovate.yml` 在 Renovate 之后追加一个 job，运行 `scripts/lint-renovate-prs.sh`：对每个开放的 Renovate PR 用 `git merge-tree` 算出它合入 `main` 后的 `config/`，做同样的两项检查，并把结果作为 `lint/config` 提交状态写到 PR 的 head 上。这个 job 只读取 PR 的配置文件，不执行 PR 里的任何代码；权限是 `contents: read`、`pull-requests: read`、`statuses: write`。结果在每次 Renovate 运行之后更新。

## 6. 新增脚本

### 6.1 `scripts/list-groups.sh`

用法：`scripts/list-groups.sh [group]`，配置目录取自 `CONFIG_DIR`，默认是仓库里的 `config/`。

- 扫描 `config/*-images.yaml`（不递归），分组名即去掉 `-images.yaml` 的文件名。只有普通文件算分组文件，同名目录会被忽略。
- 参数为 `all` 或空：输出全部分组。参数与某个分组名完全相等（整串比较，不做模式匹配）时：只输出该分组。输出里只会出现校验过的分组名。
- 参数不匹配任何分组，或目录里没有 `*-images.yaml`，或分组名不合法：退出码 1，并在 stderr 打印可用分组列表。
- 输出到 stdout，按字母序：`{"include":[{"group":"altinity"},{"group":"bitnami"}]}`。
- 纯 bash 实现，不依赖 jq。

### 6.2 `scripts/lint-config.sh`

用法：`scripts/lint-config.sh [文件...]`，默认检查 `config/*-images.yaml`。

- **重复行的定义**：同一个文件里，去掉首尾空白后完全相同的两条非注释、非空行。
- 发现重复：对每一处打印 `<文件>:<行号>: duplicate of line <首次出现的行号>`，退出码 1。没有重复则退出码 0。
- 没有可检查的文件，或某个文件不存在、不是普通文件、无法读取：退出码 2，原因写在 stderr。只有退出码 1 表示发现了重复。
- 两个脚本都先写测试再实现，测试为自包含的 bash 脚本，用临时目录构造输入。

## 7. 逐文件变更

「仅移动」表示映射行逐字不变，只是换了文件。所有注释掉的旧映射一律删除。

| 文件 | 行数 | 变更 |
|---|---|---|
| `altinity-images.yaml` | 2 | 不变 |
| `bitnami-images.yaml` | 8 | 新文件，从 `commonly` 移入 8 行 `bitnamilegacy/*` |
| `busybox-images.yaml` | 1 | 新文件，从 `commonly` 移入 |
| `clickhouse-images.yaml` | 5 | 重写，见 7.1 |
| `cnpg-images.yaml` | 2 | 去重，保留 operator 和 PostgreSQL 各 1 行 |
| `curl-images.yaml` | 1 | 新文件，从 `commonly` 移入 |
| `elasticsearch-images.yaml` | 1 | 去重 |
| `flink-images.yaml` | 1 | 重写，见 7.2 |
| `gitea-images.yaml` | 7 | 去重（`act_runner` 留 1 行 0.6.1），其余不变 |
| `kafka-ui-images.yaml` | 1 | 重写，见 7.2 |
| `kibana-images.yaml` | 1 | 去重 |
| `linyi-base-images.yaml` | 7 | 恢复并冻结，见 7.4 |
| `lobe-chat-images.yaml` | 1 | 不变 |
| `metabase-images.yaml` | 1 | 去重，见 7.3 |
| `mirrord-images.yaml` | 1 | 不变 |
| `new-api-images.yaml` | 1 | 不变 |
| `nodejs-images.yaml` | 6 | 不变 |
| `nginx-images.yaml` | 1 | 新文件，从 `commonly` 移入 |
| `otel-images.yaml` | 3 | 删除注释掉的 `kube-rbac-proxy` 行 |
| `signoz-images.yaml` | 6 | 新文件，从 `commonly` 移入（k8s-wait-for 加 5 个 signoz 镜像） |

映射行合计 113 → 57。

### 7.1 `clickhouse-images.yaml`

四条正则各取该版本线最新的补丁版，并并入原在 `commonly` 里的 alpine 行。五个 tag 都已在 ACR 中。文件头注明冻结。

```yaml
clickhouse/clickhouse-server:24.9.3.128: "registry.cn-shanghai.aliyuncs.com/ahoo/clickhouse"
clickhouse/clickhouse-server:24.10.4.191: "registry.cn-shanghai.aliyuncs.com/ahoo/clickhouse"
clickhouse/clickhouse-server:24.11.5.49: "registry.cn-shanghai.aliyuncs.com/ahoo/clickhouse"
clickhouse/clickhouse-server:24.12.6.70: "registry.cn-shanghai.aliyuncs.com/ahoo/clickhouse"
clickhouse/clickhouse-server:26.8.2-alpine: "registry.cn-shanghai.aliyuncs.com/ahoo/clickhouse"
```

### 7.2 `flink-images.yaml` 与 `kafka-ui-images.yaml`

```yaml
apache/flink:1.20.5: "registry.cn-shanghai.aliyuncs.com/ahoo/flink"
```

```yaml
provectuslabs/kafka-ui:v0.7.2: "registry.cn-shanghai.aliyuncs.com/ahoo/kafka-ui"
```

- `kafka-ui`：`v0.7.2` 是原正则范围内最新的，也是该项目最后一个版本。
- `flink`：**`1.20.5` 是占位**。仓库和各项目里都找不到实际在用的 Flink 版本，取的是原正则范围内最新的一个，已在 ACR 中。实际在用别的版本时改这一行即可，不影响其他设计。

### 7.3 `metabase-images.yaml`

```yaml
metabase/metabase:v0.65.3.7: "registry.cn-shanghai.aliyuncs.com/ahoo/metabase"
```

只留最新的稳定版。另外 4 个版本里，`v0.65.3`、`v0.65.0.14-beta`、`v0.65.0-beta` 在 ACR 中不存在，`v0.50.0-RC2` 是 RC。

### 7.4 `linyi-base-images.yaml`（恢复并冻结）

恢复为 `25025d7` 首次提交时的 7 行，文件头的盘点说明保留，并追加冻结说明：

```yaml
adoptopenjdk/openjdk11:jdk11u-centos-nightly-slim: "registry.cn-shanghai.aliyuncs.com/ahoo/linyi-base:adoptopenjdk-openjdk11_jdk11u-centos-nightly-slim"
nginx:1.23.3: "registry.cn-shanghai.aliyuncs.com/ahoo/linyi-base:nginx_1.23.3"
nginx:latest: "registry.cn-shanghai.aliyuncs.com/ahoo/linyi-base:nginx_latest"
node:14.16.0: "registry.cn-shanghai.aliyuncs.com/ahoo/linyi-base:node_14.16.0"
node:14.17.3: "registry.cn-shanghai.aliyuncs.com/ahoo/linyi-base:node_14.17.3"
node:16.8.0: "registry.cn-shanghai.aliyuncs.com/ahoo/linyi-base:node_16.8.0"
busybox:latest: "registry.cn-shanghai.aliyuncs.com/ahoo/linyi-base:busybox_latest"
```

这 7 个 tag 已逐个确认都在 ACR 中。11-25 的同步额外产生的 `nginx_1.31.10` 和 `node_14.21.3` 两个 tag 不在本次处理范围内。

### 7.5 删除的文件

- `config/ahoo-images.yaml`（`cosky-rest-api`、`pigeon-discovery` 两条映射不再需要）
- `config/commonly-images.yaml`（内容已全部拆走）
- `config/auth.yaml`
- `.github/workflows/sync-metabase.yml`

### 7.6 README

- 删除「metabase 使用单独的 workflow 和 regctl 脚本」的说明。
- 「新增分组」改为：在 `config/` 下新增 `<group>-images.yaml`，push 到 `main` 即触发同步；手动触发时填分组名或 `all`。
- 写明分组规则（一个分组对应一个 ACR 仓库，`gitea` 例外）、冻结分组及其含义。

## 8. Renovate

- `customManagers` 的正则不变。
- 新增一条 `packageRules`：对四个冻结分组的文件（`config/clickhouse-images.yaml`、`config/flink-images.yaml`、`config/kafka-ui-images.yaml`、`config/linyi-base-images.yaml`）设 `enabled: false`。

## 9. 验证

1. **单元测试**：两个新脚本先写测试、确认失败，再实现到通过。
2. **静态检查**：`actionlint` 检查两个 workflow，`renovate-config-validator` 检查 `renovate.json`。
3. **映射比对**：脚本比对基线（`78ae146`；分支变基后以变基目标为基线）与新目录的映射集合（以去重后的整行为单位），差异必须恰好是下面两组，不能有其他变化。
   - 移除 14 行：`ahoo` 2 行；clickhouse 正则 4 行；flink、kafka-ui 正则各 1 行；`linyi-base` 的 `nginx:1.31.10` 和 `node:14.21.3`；metabase 的 `v0.65.3`、`v0.50.0-RC2`、`v0.65.0.14-beta`、`v0.65.0-beta`。
   - 新增 9 行：clickhouse 4 行固定版本；flink `1.20.5`；kafka-ui `v0.7.2`；`linyi-base` 的 `nginx:1.23.3`、`node:14.16.0`、`node:14.17.3`。
   - 其余 48 行逐字相同。
4. **合并后的首轮同步**：除 `metabase:v0.65.3.7` 需要真正同步外，其余每个 tag 都应输出 `skip: ... (destination image exists)`。

## 10. 合并后对开放 PR 的影响

合并前用 `git merge-tree` 对当时开放的 Renovate PR 逐一核对过（2026-11-26）：

- #153（`linyi-base` 的 `node:16.8.0`）和 #77（clickhouse 升到 26.11.2）：分组冻结后，Renovate 下次运行会自动关闭。
- 与本分支冲突的有 6 个，需要由 Renovate 在下次运行时重建：#155（`otel-images.yaml`）、#145（`cnpg-images.yaml`）、#130（`metabase-images.yaml`）、#129（`linyi-base-images.yaml` 和 `nodejs-images.yaml`），以及上面的 #153 和 #77。
- 不受影响、可以正常合并的只有 #143、#144、#115。

**不要手工解决这些冲突。** 尤其是 #129：它把 `linyi-base` 的三行 `node` 都改成 `26.13.1`。如果有人按 PR 一侧解决冲突，恢复出来的 `linyi-base` 会被覆盖，`packageRules` 的冻结拦不住人工合并。合并后先手动触发一次 Renovate（`renovate.yml` 支持手动触发），等它重建完这些 PR 再处理。

## 11. 已知限制

- **同一镜像多行仍可能被 Renovate 并成一个版本。** Renovate 的跨大版本 PR 会把该镜像的所有行改成同一个版本。`nodejs` 的 22/24/26 已经用 `packageRules` 挡住：每一行只在自己所在的大版本内升级。`bitnami` 的 MySQL 8/9 不需要处理，`bitnamilegacy` 仓库已归档，没有新版本。以后再给同一个镜像加多行时，照 node 的做法加规则，或者不合并不想升的 PR。
- **ACR 中的历史 tag 不会减少。** 本次整理只让它不再增长。清理存量需要单独进行，并先确认哪些还在用。
- `nodejs-images.yaml` 的分组名与仓库 `ahoo/node` 不一致，本次不改名，避免让开放的 Renovate PR 重建。
