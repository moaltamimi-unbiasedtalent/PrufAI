#!/usr/bin/env bash
# Inspect the CI/Compose images without running services against data or credentials.
set -euo pipefail
umask 077
mkdir -p "${1:?evidence directory required}"
out="$(cd "$1" && pwd)"
cache="$(mktemp -d)"
trap 'rm -rf -- "$cache"' EXIT
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
revision="$(git rev-parse --short=12 HEAD)"
platform="linux/amd64"

trivy --cache-dir "$cache" image --download-db-only --no-progress
trivy --cache-dir "$cache" version --format json > "$out/trivy-db.json"
jq -e '.Version == "0.76.2" and (.VulnerabilityDB.UpdatedAt | type == "string")' \
  "$out/trivy-db.json" >/dev/null

scan() {
  local name="$1" image="$2" mode="$3" reference="$2"
  if [[ "$mode" == pull ]]; then
    docker pull --platform "$platform" "$image"
    reference="$(docker image inspect --format '{{index .RepoDigests 0}}' "$image")"
    [[ "$reference" =~ @sha256:[a-f0-9]{64}$ ]]
    if [[ "$image" == *@sha256:* ]]; then
      [[ "${reference##*@}" == "${image##*@}" ]]
    fi
  fi
  docker image inspect "$reference" | jq -e --arg ref "$reference" --arg source "$image" '
    .[0] | {source:$source,reference:$ref,image_id:.Id,repo_digests:.RepoDigests,
      os:.Os,architecture:.Architecture,rootfs_layers:.RootFS.Layers}
    | select(.os=="linux" and .architecture=="amd64" and (.image_id|test("^sha256:[0-9a-f]{64}$")))
  ' > "$out/$name.identity.json"
  trivy --cache-dir "$cache" image --image-src docker --platform "$platform" \
    --skip-db-update --scanners vuln --list-all-pkgs --no-progress --timeout 10m \
    --format json --output "$out/$name.trivy.json" "$reference"
  jq -e --slurpfile identity "$out/$name.identity.json" '.Metadata.ImageID == $identity[0].image_id and .ArtifactType == "container_image" and (.Results | length > 0) and
    any(.Results[]; .Class=="os-pkgs" and (.Packages | length > 0))' \
    "$out/$name.trivy.json" >/dev/null
  trivy convert --format cyclonedx --output "$out/$name.cdx.json" "$out/$name.trivy.json"
}

scan_built() {
  local name="$1" dockerfile="$2" image="$3"
  docker build --platform "$platform" -f "$dockerfile" -t "$image" infra
  scan "$name" "$image" built
}

scan_decoder() {
  local image="$1"
  scan_built decoder infra/media-probe.Dockerfile "$image"
  docker run --rm --network=none --read-only --cap-drop=ALL --entrypoint /bin/sh "$image" -ec '
    for package in apt libapt-pkg6.0t64; do
      version="$(dpkg-query -W -f="\${Version}" "$package")"
      dpkg --compare-versions "$version" ge 2.8.4ubuntu0.1
      printf "%s\t%s\n" "$package" "$version"
    done
  ' > "$out/decoder-apt-versions.tsv"
  jq -e 'all(.Results[].Vulnerabilities[]?; .VulnerabilityID != "CVE-2026-109518")' \
    "$out/decoder.trivy.json" >/dev/null
}

scan_go() {
  local name="$1" binary="$2"
  (
    cd "$(dirname "$binary")"
    sha256sum "$(basename "$binary")"
  ) > "$out/$name.sha256"
  go version -m "$binary" | sed "1s|^.*:|$name:|" > "$out/$name.go-build-info.txt"
  if [[ "$name" == minio-container-binary ]]; then
    grep -q 'v0.0.0-20251015172955-9e49d5e7a648' "$out/$name.go-build-info.txt"
  else
    [[ "$(head -1 "$out/$name.go-build-info.txt")" == "$name: go1.26.10" ]]
  fi
  govulncheck -mode binary -format json "$binary" | jq -c . > "$out/$name.govulncheck.jsonl"
  jq -e -s 'any(.[]; .config.protocol_version != null)' "$out/$name.govulncheck.jsonl" >/dev/null
}

scan_container_go() (
  local name="$1" image="$2" binary="$3" container
  container="$(docker create --platform "$platform" "$image")"
  trap 'docker rm "$container" >/dev/null' EXIT
  docker cp "$container:/usr/local/bin/$binary" "$cache/$binary"
  scan_go "$name" "$cache/$binary"
)

failed=0
record() {
  local name="$1" result
  shift
  # Run each target independently so one registry/build failure preserves other results.
  set +e
  (set -e; "$@")
  result=$?
  set -e
  printf '%s\n' "$result" > "$out/$name.scan-exit.txt"
  if [[ "$result" != 0 ]]; then
    failed=1
  fi
}

decoder="prufai-o1a-decoder:$revision"
minio="prufai-o1a-minio:$revision"
record decoder scan_decoder "$decoder"
record minio-source scan_built minio-source infra/minio-source.Dockerfile "$minio"
record ci-postgresql scan ci-postgresql "$(sed -n 's/^        image: \(pgvector\/pgvector[^ ]*\)$/\1/p' .github/workflows/ci.yml | sort -u)" pull
record ci-redis scan ci-redis "$(sed -n 's/^        image: \(redis:[^ ]*\)$/\1/p' .github/workflows/ci.yml | sort -u)" pull
record dev-postgresql scan dev-postgresql postgres:16-alpine pull
record demo-redis scan demo-redis redis:7-alpine pull
record minio-go-builder scan minio-go-builder "$(sed -n 's/^FROM \(golang:[^ ]*\) AS build$/\1/p' infra/minio-source.Dockerfile)" pull
record minio-container-binary scan_container_go minio-container-binary "$minio" minio
record minio-ingress-binary scan_container_go minio-ingress-binary "$minio" prufai-minio-ingress

exit "$failed"
