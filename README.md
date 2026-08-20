# Sync-Image

Sync container images from upstream registries (Docker Hub, GHCR, ...) to
Aliyun ACR (`registry.cn-shanghai.aliyuncs.com/ahoo`).

## Usage

- Image groups live in [`config/`](./config), one `*-images.yaml` per group.
  Each line maps a source image tag to a destination repository:
  `"<src-image>:<tag>": "<dst-repo>[:<dst-tag>]"`. Tag regexes like
  `/^24\..+/` are supported by the [image-syncer](https://github.com/AliyunContainerService/image-syncer)
  based groups.
- [`.github/workflows/sync-images.yml`](./.github/workflows/sync-images.yml)
  syncs these groups (manual run picks a group, or all). Pushing changes to
  `config/*-images.yaml` on `main` triggers a sync automatically.
  Metabase uses [`.github/workflows/sync-metabase.yml`](./.github/workflows/sync-metabase.yml)
  and [`scripts/sync-image.sh`](./scripts/sync-image.sh) instead: it reassembles
  multi-arch indexes without OCI 1.1 attestation manifests, which Aliyun ACR
  rejects with `unknown manifest class for application/vnd.oci.empty.v1+json`.
- [Renovate](./renovate.json) keeps image tags in the config files up to date
  via `customManagers`; merging its PRs triggers the sync.

Required repository secrets: `ALIYUN_CR_USERNAME`, `ALIYUN_CR_PASSWORD`.
