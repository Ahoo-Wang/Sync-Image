# Sync-Image

Mirrors container images from Docker Hub, GHCR and other upstream registries
into Aliyun ACR, for clusters and CI runners that cannot reach those
registries.

## Pulling an image

Every mirrored image is at

```
registry.cn-shanghai.aliyuncs.com/ahoo-mirror/<repository>:<tag>
```

Each file in [`images/`](./images) is a group: one image (`nginx`), a product
(`otel`), or the images one application needs (`gitea`). Each line in it is
one tag. A line goes to the repository named after its group, unless it names
another one:

| File | Line | Pull |
|---|---|---|
| `images/nginx.txt` | `nginx:1.31.10-alpine` | `registry.cn-shanghai.aliyuncs.com/ahoo-mirror/nginx:1.31.10-alpine` |
| `images/gitea.txt` | `gitea/runner:6.0.1 gitea-runner` | `registry.cn-shanghai.aliyuncs.com/ahoo-mirror/gitea-runner:6.0.1` |
| `images/otel.txt` | `otel/autoinstrumentation-java:2.34.0 otel:autoinstrumentation-java` | `registry.cn-shanghai.aliyuncs.com/ahoo-mirror/otel:autoinstrumentation-java_2.34.0` |

A mirrored tag holds the `linux/amd64` and `linux/arm64` images of its
upstream tag, as they are now. It is compared with upstream after every
change to `images/` and once a day, and copied again when upstream has moved.

## Adding an image

1. Open the group's file `images/<group>.txt`, or create it. Names are
   lowercase letters, digits, `.`, `_` and `-`.
2. Add a line `<image>:<tag>`, optionally followed by where it goes:
   - `<repository>` mirrors it to that repository instead of the group's;
   - `<repository>:<prefix>` also puts `<prefix>_` before the tag, so that
     several images can share one repository.

   Lines that start with `#` are comments.
3. Open a pull request. The Check workflow marks any problem on its line.
   After the merge, the image is in ACR within minutes.

## Keeping versions current

Renovate keeps each line up to date inside its **lane**, the range in which
its upstream promises compatibility, and never moves a line out of it:

| Upstream version | Lane | Example |
|---|---|---|
| 1.0 or later | major version | `node:24.22.3-alpine3.24` stays on 24 |
| 0.x | minor version | `metabase/metabase:v0.65.3.7` stays on 0.65 |
| one release line per minor version | minor version | `alpine/k8s:1.37.2` stays on 1.37 |

The last row needs the image in the minor-lane rule of
[`renovate.json`](./renovate.json).

- **New lane.** Add a line next to the old one, for example
  `docker:29-dind` next to `docker:27-dind`. Both lanes then stay current.
  Renovate lists newer lanes under "Pending Approval" on the Dependency
  Dashboard issue. Don't approve them there: that opens a pull request that
  replaces the old line, and `mirror/check` fails it.
- **Fixed versions.** Put the list in [`images/frozen/`](./images/frozen).
  Renovate doesn't read it, so its tags change only by hand.
- **Tags that are not versions.** `latest` or `ubuntu-24.04-v26.11.25` are
  never changed by Renovate, but the mirror still follows what upstream
  publishes under them.

## How it works

[`mirror.py`](./mirror.py) does everything (Python 3, standard library only).
Three workflows run it:

| Workflow | Runs | Does |
|---|---|---|
| [Sync](./.github/workflows/sync.yml) | on changes to `main`, daily at 04:00 UTC, on demand | `mirror.py sync` |
| [Check](./.github/workflows/check.yml) | on every pull request and push to `main` | `mirror.py check` and the unit tests |
| [Renovate](./.github/workflows/renovate.yml) | daily at 02:00 UTC, on demand | Renovate, then `mirror.py check-prs` |

**Sync.** For each line it reads the upstream digest with a HEAD request,
which doesn't count against Docker Hub's pull limit. Every mirrored index
records the digest it was built from. When the two match, there is nothing to
do. Otherwise sync copies the `linux/amd64` and `linux/arm64` images by digest
and pushes a new index with just those two. Attestation manifests are left out,
because ACR rejects them.

**Check.**
- Every file in `images/` is named `<group>.txt` or `frozen/<group>.txt`.
- Every line matches the line format above. Renovate reads lines with the same
  expression, and `renovate.json` must use it verbatim, so every mirrored line
  can be updated.
- Every mirrored tag comes from one line. A destination with two sources is
  never synced.
- Every lane has one line. Renovate would update two lines in the same lane to
  the same tag.
- `renovate.json` contains the lane rules.
- Mirrored tags are at most 128 characters.

**Renovate pull requests.** Renovate pushes with `GITHUB_TOKEN`, so the Check
workflow waits for approval on its pull requests. After each Renovate run,
`mirror.py check-prs` checks what each of them would merge into `main`,
including that no lane of `main` disappears, and posts the result as the
`mirror/check` commit status. It only reads the files in the pull request; it
never runs them.

[`docs/design.md`](./docs/design.md) explains why it works this way.

## Running it locally

```bash
python3 mirror.py check
```

```bash
python3 -m unittest
```

`sync` needs [regctl](https://github.com/regclient/regclient) and a login to
ACR:

```bash
regctl registry login registry.cn-shanghai.aliyuncs.com
```

```bash
python3 mirror.py sync
```

## Maintenance

- **Secrets.** `ALIYUN_CR_USERNAME` and `ALIYUN_CR_PASSWORD` are required.
  `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` are optional; with them, Docker
  Hub allows more pulls.
- **A failed sync.** The run's summary and annotations list each image that
  failed, with the reason. The next daily run tries again; to retry sooner,
  run the Sync workflow by hand. If Docker Hub refuses with a rate-limit error
  (HTTP 429), set the Docker Hub secrets.
- **A Renovate pull request without checks.** Read its `mirror/check` status.
- **regctl.** The version is in [`.regctl-version`](./.regctl-version), and
  Renovate updates it.
- **GitHub Actions.** `GITHUB_TOKEN` cannot push changes to
  `.github/workflows/`, so the actions there are updated by hand. The
  Dependency Dashboard issue lists the available updates.
