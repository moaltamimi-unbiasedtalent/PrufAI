#!/usr/bin/env bash
set -euo pipefail
repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
name="prufai-audio-minio"
image="prufai-minio:guarded-2025-10-15"
case "${1:-}" in
  start)
    : "${OBJECT_STORAGE_ACCESS_KEY:?Set a dedicated local development access key}"
    : "${OBJECT_STORAGE_SECRET_KEY:?Set a dedicated local development secret key}"
    : "${OBJECT_STORAGE_BUCKET:?Set a dedicated private local bucket}"
    export OBJECT_STORAGE_ENDPOINT="${OBJECT_STORAGE_ENDPOINT:-http://127.0.0.1:19000}"
    export OBJECT_STORAGE_REGION="${OBJECT_STORAGE_REGION:-us-east-1}"
    export OBJECT_STORAGE_BACKEND=s3
    # No reset, anonymous policy, or deletion. The named volume survives stop.
    if docker container inspect "$name" >/dev/null 2>&1; then
      test "$(docker inspect --format '{{index .Config.Labels "org.prufai.purpose"}}' "$name")" = local-audio-storage
      if [[ "$(docker inspect --format '{{index .Config.Labels "org.prufai.storage-ingress"}}' "$name")" != guarded-v1 ]]; then
        echo 'Existing audio storage container is unguarded. A separately authorized recreation is required.' >&2
        exit 1
      fi
      docker start "$name" >/dev/null
    else
      docker build --file "$repository_root/infra/minio-source.Dockerfile" --tag "$image" "$repository_root/infra"
      export MINIO_ROOT_USER="$OBJECT_STORAGE_ACCESS_KEY" MINIO_ROOT_PASSWORD="$OBJECT_STORAGE_SECRET_KEY"
      docker run --detach --name "$name" --label org.prufai.purpose=local-audio-storage \
        --publish 127.0.0.1:19000:9000 --publish 127.0.0.1:19001:9001 \
        --mount type=volume,source=prufai-audio-minio-data,target=/data \
        --env MINIO_ROOT_USER --env MINIO_ROOT_PASSWORD \
        --env MINIO_BROWSER_REDIRECT_URL=http://127.0.0.1:19001 "$image" >/dev/null
    fi
    for _ in $(seq 1 45); do
      if curl --fail --silent --connect-timeout 1 --max-time 1 "$OBJECT_STORAGE_ENDPOINT/minio/health/ready" >/dev/null; then
        PYTHONPATH="$repository_root/services/api" python3 "$repository_root/scripts/initialize-audio-storage.py"
        echo 'Private audio storage ready. Console: http://127.0.0.1:19001 (local credentials from your environment).'
        exit 0
      fi
      sleep 1
    done
    echo 'Local audio MinIO did not become ready; inspect the dedicated container logs.' >&2
    exit 1
    ;;
  stop)
    if docker container inspect "$name" >/dev/null 2>&1; then
      test "$(docker inspect --format '{{index .Config.Labels "org.prufai.purpose"}}' "$name")" = local-audio-storage
      docker stop "$name" >/dev/null
    fi
    echo 'Audio MinIO stopped; objects and volume preserved.'
    ;;
  *) echo 'Usage: bash scripts/local-audio-storage.sh start|stop' >&2; exit 2 ;;
esac
