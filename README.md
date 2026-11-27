# Sync-Image

Mirrors upstream container images (Docker Hub, GHCR, Elastic, ...) into Aliyun
ACR at `registry.cn-shanghai.aliyuncs.com/ahoo-mirror`, for clusters and CI runners
that cannot reach the upstream registries.

## Adding an image

`images/<repository>.txt` lists the images mirrored into
`registry.cn-shanghai.aliyuncs.com/ahoo-mirror/<repository>`, one per line:

| Line | Mirrored to |
|---|---|
| `nginx:1.31.10-alpine` | `ahoo-mirror/nginx:1.31.10-alpine` |
| `otel/autoinstrumentation-java:2.34.0 autoinstrumentation-java` | `ahoo-mirror/otel:autoinstrumentation-java_2.34.0` |

A line is `<image>:<tag>`, optionally followed by a tag prefix. Lines that
start with `#` are comments. Add a line, or a file for a new repository, and
merge it into `main`: the image is in ACR a few minutes later.

Lists whose tags must change only by hand live in
[`images/frozen/`](./images/frozen), which Renovate does not read.

## Lanes: one line per major version

Each line in `images/*.txt` is a lane. Renovate keeps its tag current inside
the range where the upstream promises compatibility, and never moves it
beyond:

| Upstream version | Lane | Example |
|---|---|---|
| 1.0 and later (semver) | the major version | `node:24.22.3-alpine3.24` stays on 24 |
| 0.x (semver) | the minor version | `metabase/metabase:v0.65.3.7` stays on 0.65 |
| a release line per minor version | the minor version, by a minor-lane rule in [`renovate.json`](./renovate.json) | `alpine/k8s:1.37.2` stays on 1.37 |

A newer lane is listed on the Dependency Dashboard issue under "Pending
Approval". Start it by adding a line, for example `docker:29-dind` next to
`docker:27-dind`; the old lane keeps getting its updates. Approving it on the
dashboard instead would open a pull request that replaces the old line, and
`mirror/check` marks that pull request as failed. A tag that is not a version
(`latest`, `act-22.04`) is never updated by Renovate.

## How it works

Everything is done by [`mirror.py`](./mirror.py) (Python 3, standard library
only).

- **Sync** ([`sync.yml`](./.github/workflows/sync.yml), `mirror.py sync`) runs
  after every change on `main`, daily, and on demand. For each line it reads
  the source digest with a HEAD request, which does not count against Docker
  Hub's pull limit. If the destination was built from that digest, there is
  nothing to do. Otherwise it copies the `linux/amd64` and `linux/arm64`
  manifests by digest and pushes an OCI index of just those, annotated with the
  source name and digest. Attestation manifests are left out, because ACR
  rejects them (`unknown manifest class for application/vnd.oci.empty.v1+json`).
  So a destination always holds what its source holds now: tags that move
  upstream (`latest`, `27-dind`, rebuilt patch releases) are followed within a
  day, and a failed copy is retried by the next run.
- **Check** ([`check.yml`](./.github/workflows/check.yml), `mirror.py check`)
  runs on every pull request and push to `main`, and marks each problem on its
  line:
  1. every file in `images/` is `<repository>.txt` or `frozen/<repository>.txt`,
     in lowercase;
  2. every line matches the grammar above, which is also the expression
     Renovate uses (`renovate.json` must contain it verbatim), so every line
     that is mirrored is a line Renovate can update;
  3. every destination has one line: a repeated line is a duplicate, and two
     images with one destination are a collision, which sync refuses;
  4. each lane has one line: two lines of an image in the same lane, with the
     same tag suffix, repository and prefix, would be updated to the same tag;
  5. `renovate.json` contains the lane rules from `mirror.py`;
  6. destination tags are at most 128 characters.
- **Renovate** ([`renovate.yml`](./.github/workflows/renovate.yml)) runs daily.
  It pushes with `GITHUB_TOKEN`, so the Check workflow waits for approval on its
  pull requests. The same workflow therefore runs `mirror.py check-prs`
  afterwards: it checks what each Renovate pull request would merge into
  `main`, and that no lane of `main` disappears, and posts the result as the
  `mirror/check` commit status. Nothing from the pull request is executed.

## Running it locally

```bash
python3 mirror.py check
```

```bash
python3 -m unittest
```

`sync` needs [regctl](https://github.com/regclient/regclient) and a login to
ACR (`regctl registry login registry.cn-shanghai.aliyuncs.com`):

```bash
python3 mirror.py sync
```

## Maintenance

- Secrets: `ALIYUN_CR_USERNAME` and `ALIYUN_CR_PASSWORD` (required);
  `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` (optional, for a higher Docker Hub
  pull limit).
- The regctl release is pinned in [`.regctl-version`](./.regctl-version), and
  Renovate updates it.
- `GITHUB_TOKEN` cannot push changes to `.github/workflows/`, so the actions
  used there are updated by hand. The Dependency Dashboard issue lists the
  updates.
- [`docs/design.md`](./docs/design.md) explains why it works this way.
