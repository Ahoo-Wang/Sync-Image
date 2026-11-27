# Sync-Image 设计

- 日期：2026-11-27
- 取代：`docs/superpowers/` 下 2026-11-26 的配置目录重整 spec 和计划（已删除，见 git 历史）

## 1. 要解决的问题

linyi-k8s（ACK）和 Gitea CI 访问不到 Docker Hub、GHCR 等上游仓库，能访问 Aliyun ACR（`registry.cn-shanghai.aliyuncs.com/ahoo-mirror`）。GitHub Actions 的 runner 两边都能访问。所以要做的只有一件事：

> 对每个声明的上游镜像，让 ACR 里对应的 tag 与上游 tag 当前的内容一致，只保留 `linux/amd64` 和 `linux/arm64`。

其余都是约束：

1. ACR 拒收 OCI 1.1 attestation manifest（`unknown manifest class for application/vnd.oci.empty.v1+json`），只能重建 index，不能整份复制。
2. Docker Hub 对 manifest 的 GET 限流，HEAD 不计数。
3. 版本靠 Renovate 升级；它用 `GITHUB_TOKEN` 推送，因此它的 PR 上的 `pull_request` workflow 要人工批准，而且它推不了 `.github/workflows/` 里的改动。
4. ACR 里的目标路径被集群、Dockerfile 和 `linyi/cicd-assets` 引用，是对外的契约。这次重写把命名空间从 `ahoo` 换成了 `ahoo-mirror`，仓库名和 tag 不变：57 条映射逐条比对过，除命名空间外新旧完全相同。旧命名空间 `ahoo` 里的镜像保留，但不再更新。

## 2. 数据：从文件名和一行推出全部信息

旧格式 `"<src>:<tag>": "<registry>/ahoo/<repo>[:<dst-tag>]"` 每行写两遍版本号和一遍 registry，靠 `autoReplaceGlobalMatch` 让 Renovate 同时改两处，再靠 lint 检查两处一致。观察现有的 57 行：目标 tag 一律是 `<tag>` 或 `<prefix>_<tag>`，registry 和命名空间一律相同，一个文件基本对应一个仓库。能推出来的就不写：

```
images/<repository>.txt          → registry.cn-shanghai.aliyuncs.com/ahoo-mirror/<repository>
<image>:<tag>                    → <repository>:<tag>
<image>:<tag> <prefix>           → <repository>:<prefix>_<tag>
```

于是版本号每行只出现一次，旧格式下的三类错误从结构上不可能出现：目标 tag 与源 tag 不一致、registry 写错、一个文件写进别的仓库。`ls images/` 就是 ACR 里的仓库清单。原来按用途合在 `gitea-images.yaml` 里的 7 个镜像拆成了 7 个文件，用途写在各自的注释里。

**冻结是目录，不是名单。** 原来冻结靠 `renovate.json` 里列出文件名，改名或新增时容易漏（linyi-base 的版本就被 Renovate 改过）。现在 `images/frozen/` 下的文件 Renovate 根本不读，放进去就冻结了，不需要另外登记。

**一套语法。** 原来有三个解析器：同步脚本用 YAML，lint 按原始行，Renovate 用正则。三者能接受的内容并不相同。现在语法只在 `mirror.py` 里定义一次（`LINE`、`RENOVATE_MATCH`、`RENOVATE_FILES`），`check` 要求 `renovate.json` 里逐字使用同一个表达式。凡是会被同步的行，Renovate 都能看到。

## 3. 同步：镜像语义

旧实现是「目标 tag 存在就跳过」。这样只保证了 tag 存在，不保证内容一致：`latest`、`27-dind`、`act-22.04`、`3.9-eclipse-temurin-21` 这类会移动的 tag，以及上游为修 CVE 重建的补丁 tag，同步一次之后就永远停在当时的内容；手工推上去的单架构 tag 也永远不会被纠正。

现在每个目标 index 上记录它的来源（注解 `io.github.ahoo-wang.sync-image.source.name` 和 `.digest`）。每次运行：

1. HEAD 源 tag，得到 digest（不计入 Docker Hub 限流）；
2. 读目标 index 的注解，digest 相同就结束；
3. 否则按 digest 读源 manifest（之后的每一步看到的都是同一个版本），选出 `linux/amd64` 和 `linux/arm64`，按 digest 逐个复制，再推一个只含这两项、带注解的 OCI index。源是单个 manifest 时，读它的 config 得到平台，包成只有一项的 index。

结果：目标始终等于源的当前内容；稳态下每个镜像只有两次请求；复制失败了，下一次运行会重试，所以 workflow 每天定时跑一次。上游同一 tag 的内容不变时，重复运行不会改动目标。

一个目标只能有一个来源。两个不同的源指向同一个目标时，sync 拒绝同步它们并报错，不让结果取决于执行顺序。完全相同的重复行只同步一次。

## 4. 升级：每个主版本一行

升级的边界应该由上游的兼容承诺决定，而不是由「最新版本」决定。上游在哪一位上声明可能破坏兼容，哪一位就是边界：

| 上游的版本 | 兼容承诺 | 一条线（lane） |
|---|---|---|
| 1.0 及以后，semver | 主版本内兼容 | 主版本，如 node 的 22 / 24 / 26 |
| 0.x，semver | 只在次版本内兼容 | 次版本，如 metabase 的 0.65、OTel Collector 的 0.162 |
| 每个次版本是一条发行线 | 次版本内兼容 | 次版本，在 `renovate.json` 的 minor-lane 规则里登记（`alpine/k8s` 跟随 Kubernetes，kubectl 与集群最多差一个次版本） |

`images/*.txt` 的每一行就是一条线。`renovate.json` 用两条规则表达默认策略（`mirror.py` 的 `LANE_RULES`，`check` 要求它们存在）：跨主版本的升级，以及 0.x 的跨次版本升级，都要在 Dependency Dashboard 上批准，不会直接开 PR。所以 Renovate 只会在一条线内升级，不同线的行永远不会被并成一行。以前只有 node 有三条 `allowedVersions` 规则，现在两条规则覆盖所有镜像、所有大版本。

新的一条线靠人加一行：Dashboard 的「Pending Approval」列出上游的新主版本，作为提醒。如果在 Dashboard 上批准，Renovate 会开一个**替换**旧行的 PR，旧的线就丢了；所以 `check-prs` 比较合入前后的线，丢了线的 PR 标为 failure，提示改为新增一行。Renovate 只改 tag、不删行，所以合入后少了某条线，就说明它把一行挪到了别的线上。

这对 0.x 的 12 行（altinity、act_runner、metabase、new-api、OTel Collector/Operator、SigNoz 的几个组件）和 `alpine/k8s` 是行为变化：它们以后只自动升补丁版本，新的次版本要手动加行。这正是这些上游的兼容承诺：OTel Collector 的每个 0.x 次版本都列出了不兼容变更，metabase 的 0.65 就是它的第 65 个大版本。

## 5. 检查：每条规则对应一种真实发生过或会发生的故障

| 规则 | 防止的故障 |
|---|---|
| 文件名必须是 `images/<repository>.txt` 或 `images/frozen/<repository>.txt` | 文件名写错后被所有工具静默忽略 |
| 每行符合语法；`renovate.json` 逐字使用同一表达式 | Renovate 看不到某一行，这一行永远不升级 |
| 一个目标只有一行 | 重复行；两个源争同一个目标 |
| 每条线只有一行 | 同一条线上的两行会被 Renovate 升级成同一个 tag（metabase 47 行剩 5 个版本、act_runner、node 14.x 都发生过） |
| `renovate.json` 含有 `LANE_RULES` | 线的规则被删掉后，Renovate 又开始跨线升级 |
| 目标 tag 不超过 128 个字符 | 推送时才失败 |

「同一条线」按 Renovate docker versioning 的规则判断：同一镜像、同一 tag 后缀、同样多的版本位数、同一仓库和前缀，并且在同一个主版本（0.x 和 minor-lane 镜像是同一个次版本）。

Renovate 的 PR 上 Check 要人工批准才会运行，所以 `renovate.yml` 在 Renovate 之后运行 `mirror.py check-prs`：对每个开放的 Renovate PR，用 `git merge-tree` 算出合入 `main` 后的树，对其中的 `images/` 和 `renovate.json` 做同样的检查，再确认 `main` 的每条线都还在，结果作为 `mirror/check` 提交状态写到 PR 的 head 上。它只读取文件，不执行 PR 里的任何东西。

## 6. 实现

一个文件 `mirror.py`，只用 Python 标准库（runner 和 macOS 自带 Python 3）。原来的 6 个 bash 脚本，大量代码花在退出码传递、临时目录、用字符串拼 JSON 和用 sed 改路径上。检查逻辑是对 `{路径: 内容}` 的纯函数，所以同一个函数可以检查工作区、git 树和测试数据。sync 通过注入的 regctl 调用测试：`test_mirror.py` 里有一个内存中的假 registry。

regctl 的版本写在 `.regctl-version` 里，而不是 workflow 里，这样 Renovate 才能升级它（它推不了 workflow 文件）。升级合入后会触发一次 sync，相当于在真实环境里试一遍新版本。

## 7. 已知限制

- **`ahoo-mirror` 命名空间要先在 ACR 控制台创建**，并打开「自动创建仓库」，否则第一次推送新仓库会被拒绝。集群需要匿名拉取的话，默认仓库类型要设为公开。
- **合入后的第一次运行会完整复制全部 57 个镜像**：新命名空间是空的，所有层都要重新上传，耗时取决于镜像总大小。57 个镜像里有 50 个来自 Docker Hub，每个要读 3 次 manifest（index 和两个平台；两次 GET 记一次 pull），合计约 75 次 pull 配额；如果被限流，第二天的运行会接着做完。配置 `DOCKERHUB_USERNAME`/`DOCKERHUB_TOKEN` 可以提高限额。
- **使用方要改地址。** 集群清单、Dockerfile 和 `linyi/cicd-assets` 里引用 `ahoo/...` 的地方都要改成 `ahoo-mirror/...`；改之前它们拿到的是旧命名空间里不再更新的镜像。
- **ACR 接受这种 index 尚未实测。** 本地对 distribution registry 跑通了完整流程；ACR 以前接受的是 `regctl index create` 生成的 OCI index，新 index 多了注解，属于 OCI 1.0 的标准字段。如果 ACR 拒收，sync 会失败并报错，已有的 tag 不受影响。
- **跟踪上游意味着内容会变。** 想要不变的内容，就选不会移动的 tag，或者把它放进 `images/frozen/` 并选具体版本。
- **workflow 里的 action 需要手动升级**（`GITHUB_TOKEN` 的限制）。换成 GitHub App 的 token 才能让 Renovate 自己升级。
