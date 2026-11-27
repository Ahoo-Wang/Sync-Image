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
  triggered by hand with a group name or `all`. The group name is the file
  name without `-images.yaml` and must match `^[a-z0-9]+(-[a-z0-9]+)*$`: the
  workflow lists groups from the directory, so one mis-named file stops every
  sync.
- Every group is synced by [`scripts/sync-image.sh`](./scripts/sync-image.sh)
  (regctl). It copies `linux/amd64` and `linux/arm64` only, and reassembles
  the index without OCI 1.1 attestation manifests, which Aliyun ACR rejects
  with `unknown manifest class for application/vnd.oci.empty.v1+json`. A tag
  that already exists at the destination is skipped.
- [Renovate](./renovate.json) keeps image tags up to date via
  `customManagers`; merging its PRs triggers the sync. Frozen groups
  (`clickhouse`, `flink`, `kafka-ui`, `linyi-base`) are switched off in
  `packageRules`: change their tags by hand. The three `node` lines (22, 24,
  26) each update only within their own major, so a major-update PR cannot
  turn them into duplicates.
- [`.github/workflows/lint-config.yml`](./.github/workflows/lint-config.yml)
  checks group file names, runs the script tests and shellcheck, and reports
  duplicate mapping lines. Renovate bumps every line of the same image to the
  same version, so lines that used to be different versions can become
  identical; delete the extra lines. Renovate pushes with `GITHUB_TOKEN`, so
  on its own pull requests that workflow waits for approval. The Renovate
  workflow therefore runs
  [`scripts/lint-renovate-prs.sh`](./scripts/lint-renovate-prs.sh) afterwards
  and posts the result as a `lint/config` status on each Renovate PR.

Required repository secrets: `ALIYUN_CR_USERNAME`, `ALIYUN_CR_PASSWORD`.
