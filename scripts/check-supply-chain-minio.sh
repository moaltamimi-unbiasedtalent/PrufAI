#!/usr/bin/env bash
# Verify the scanned image on a fresh network and volume.
set -euo pipefail
out="$(cd "${1:?container evidence directory required}" && pwd)"
[[ "${GITHUB_ACTIONS:-}" == true || "${PRUFAI_DISPOSABLE_MINIO_TEST:-}" == 1 ]]
image="$(jq -er .image_id "$out/minio-source.identity.json")"
export PRUFAI_LID_PG_PASSWORD=o1b1-disposable PRUFAI_DISPOSABLE_DATABASE=o1b1_disposable
export OBJECT_STORAGE_ACCESS_KEY=o1b1-synthetic OBJECT_STORAGE_SECRET_KEY=o1b1-synthetic-only
export OBJECT_STORAGE_BUCKET=o1b1-private OBJECT_STORAGE_SECURE=0 PRUFAI_REQUIRE_OBJECT_STORAGE_INTEGRATION=1

for file in infra/docker-compose.yml infra/compose.lid-demo.yml; do
  docker compose -f "$file" config --format json |
    jq -e '.services.minio.build.dockerfile == "minio-source.Dockerfile" and
      (.services.minio.ports | length > 0 and
        all(.host_ip == "127.0.0.1" and (.target == 9000 or .target == 9001))) and
      (.services.minio.command == null and .services.minio.entrypoint == null) and
      any(.services.minio.volumes[]; .type == "volume" and .target == "/data")' >/dev/null
done

volume=""
network=""
container=""
cleanup() {
  if [[ -n "$container" ]]; then
    docker rm -f "$container" >/dev/null
  fi
  if [[ -n "$network" ]]; then
    docker network rm "$network" >/dev/null
  fi
  if [[ -n "$volume" ]]; then
    docker volume rm "$volume" >/dev/null
  fi
}
trap cleanup EXIT

volume="$(docker volume create)"
network="$(docker network create "prufai-o1b1-$$-$RANDOM")"
[[ "$(docker image inspect --format '{{index .Config.Labels "org.prufai.storage-ingress"}}' "$image")" == guarded-v1 ]]
container="$(docker run --detach --network "$network" --network-alias guarded-storage \
  --publish 127.0.0.1::9000 \
  --mount "type=volume,source=$volume,target=/data" \
  --env "MINIO_ROOT_USER=$OBJECT_STORAGE_ACCESS_KEY" --env "MINIO_ROOT_PASSWORD=$OBJECT_STORAGE_SECRET_KEY" "$image")"
export OBJECT_STORAGE_ENDPOINT="http://$(docker port "$container" 9000/tcp)"
ready=0
for _ in $(seq 1 30); do
  if curl --silent --fail --max-time 2 "$OBJECT_STORAGE_ENDPOINT/minio/health/ready" >/dev/null; then
    ready=1
    break
  fi
  [[ "$(docker inspect --format '{{.State.Running}}' "$container")" == true ]] || break
  sleep 1
done
if [[ "$ready" != 1 ]]; then
  docker logs "$container"
  echo "The scanned MinIO image did not become ready"
  exit 1
fi

docker exec "$container" awk '
  $4 == "0A" && $2 ~ /:4A3[89]$/ {
    print $2
    if ($2 !~ /^0100007F:4A3[89]$/) unsafe = 1
    ports[$2] = 1
  }
  END { exit (unsafe || !ports["0100007F:4A38"] || !ports["0100007F:4A39"]) }
' /proc/net/tcp /proc/net/tcp6 > "$out/minio-backend-listeners.txt"
python scripts/check-minio-ingress.py "$out/minio-request-containment.json"

# A peer attached to the same bridge can reach the protected ingress, but
# must fail to connect to either actual MinIO listener by container address.
docker run --rm --network "$network" --entrypoint /bin/bash "$image" -ec '
  for port in 9000 9001; do
    response="$(timeout 5 bash -ec '\''
      exec 3<>"/dev/tcp/guarded-storage/$1"
      printf "PUT /o1b1-private/peer HTTP/1.1\r\nHost: guarded-storage\r\nConnection: close\r\nX-Amz-Content-Sha256: UNSIGNED-PAYLOAD\r\nX-Amz-Content-Sha256: STREAMING-UNSIGNED-PAYLOAD-TRAILER\r\nContent-Length: 0\r\n\r\n" >&3
      cat <&3
    '\'' _ "$port")"
    printf "%s\n" "$response" | grep -Eq "^HTTP/1\.1 403"
    printf "%s\n" "$response" | grep -Eiq "^x-prufai-storage-guard: unsigned-trailer-denied"
    echo "Peer ingress port $port: unsigned trailer denied by guard"
  done
  for port in 19000 19001; do
    status=0
    diagnostic="$(LC_ALL=C timeout 5 bash -c '\''exec 3<>"/dev/tcp/guarded-storage/$1"'\'' _ "$port" 2>&1)" || status=$?
    if [[ "$status" != 1 || "$diagnostic" != *"Connection refused"* ]]; then
      echo "Expected direct backend port $port to refuse a peer connection, got status $status" >&2
      exit 1
    fi
    echo "Peer direct backend port $port: connection refused"
  done
' > "$out/minio-network-boundary.txt"
python scripts/check-minio-ingress.py --verify-retained >> "$out/minio-network-boundary.txt"
cat "$out/minio-network-boundary.txt"
cd services/api
python -m pytest tests/test_object_storage_integration.py -v --junitxml="$out/minio-source-compatibility.xml"
python - "$out/minio-source-compatibility.xml" <<'PY'
import sys
from xml.etree import ElementTree

cases = ElementTree.parse(sys.argv[1]).findall(".//testcase")
if len(cases) != 1 or any(case.find("skipped") is not None for case in cases):
    raise SystemExit("The real private S3 integration must run without skips")
PY
