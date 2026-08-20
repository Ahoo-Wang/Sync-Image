#!/usr/bin/env bash
# Sync container images listed in an images file from source registries to a
# destination registry, keeping only the given platforms.
#
# This replaces image-syncer for images whose OCI index contains OCI 1.1
# attestation manifests (platform unknown/unknown, config.mediaType
# application/vnd.oci.empty.v1+json): Aliyun ACR rejects those with
# "denied: unknown manifest class for application/vnd.oci.empty.v1+json".
# Per-platform copies are pushed by digest and reassembled into an index
# without the attestation entries.
#
# Usage: sync-image.sh <images-file> [platform ...]
#   images-file: YAML mapping of "<src-image:tag>": "<dst-repo[:dst-tag]>"
#   platform:    defaults to "linux/amd64 linux/arm64"
#
# Registry credentials must be configured beforehand, e.g.
#   regctl registry login registry.example.com -u "$USER" --pass-stdin <<< "$PASS"
set -euo pipefail

IMAGES_FILE=${1:?usage: sync-image.sh <images-file> [platform ...]}
shift
PLATFORMS=("$@")
if [ ${#PLATFORMS[@]} -eq 0 ]; then
  PLATFORMS=(linux/amd64 linux/arm64)
fi

sync_one() {
  local src=$1 dst=$2 dst_repo dst_tag target
  local last=${dst##*/}
  if [[ $last == *:* ]]; then
    dst_repo=${dst%:*}
    dst_tag=${last#*:}
  else
    dst_repo=$dst
    dst_tag=${src##*:}
  fi
  target="$dst_repo:$dst_tag"

  if regctl manifest head "$target" >/dev/null 2>&1; then
    echo "skip: $src -> $target (destination image exists)"
    return 0
  fi

  local digests=() p d
  for p in "${PLATFORMS[@]}"; do
    if d=$(regctl manifest head "$src" --platform "$p" --format '{{.GetDescriptor.Digest}}' 2>/dev/null); then
      echo "copy: $src ($p, $d) -> $dst_repo"
      regctl image copy --platform "$p" "$src" "$dst_repo@$d" >/dev/null
      digests+=("$d")
    else
      echo "warn: $src has no manifest for $p, skipping platform"
    fi
  done
  if [ ${#digests[@]} -eq 0 ]; then
    echo "error: no platform manifest found for $src" >&2
    return 1
  fi

  local create_args=()
  for d in "${digests[@]}"; do
    create_args+=(--digest "$d")
  done
  regctl index create "$target" "${create_args[@]}" >/dev/null
  echo "synced: $src -> $target"
}

entries=$(python3 - "$IMAGES_FILE" <<'PY'
import sys, yaml

with open(sys.argv[1]) as f:
    data = yaml.safe_load(f)
for src, dst in data.items():
    print(f"{src}\t{dst}")
PY
)

status=0
while IFS=$'\t' read -r src dst; do
  sync_one "$src" "$dst" || status=1
done <<< "$entries"

if [ $status -ne 0 ]; then
  echo "failed tasks exist" >&2
fi
exit $status
