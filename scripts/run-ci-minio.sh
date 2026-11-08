#!/usr/bin/env bash
# Real guarded storage for isolated GitHub Actions jobs only.
set -euo pipefail
[[ "${GITHUB_ACTIONS:-}" == true ]]
name=prufai-ci-minio
case "${1:-}" in
  start)
    : "${MINIO_ROOT_USER:?synthetic CI user required}"
    : "${MINIO_ROOT_PASSWORD:?synthetic CI password required}"
    docker build -f infra/minio-source.Dockerfile -t "$name:guarded" infra
    docker image inspect --format '{{.Id}}' "$name:guarded"
    docker run --detach --name "$name" --label org.prufai.purpose=hosted-ci-minio \
      --publish 127.0.0.1:9000:9000 --mount type=volume,target=/data \
      --env MINIO_ROOT_USER --env MINIO_ROOT_PASSWORD --env MINIO_BROWSER=off "$name:guarded"
    for _ in $(seq 1 45); do
      if curl --fail --silent --max-time 2 http://127.0.0.1:9000/minio/health/ready >/dev/null; then
        docker exec "$name" sha256sum /usr/local/bin/minio /usr/local/bin/prufai-minio-ingress
        docker exec "$name" /usr/local/bin/minio --version
        exit 0
      fi
      [[ "$(docker inspect --format '{{.State.Running}}' "$name")" == true ]] || break
      sleep 1
    done
    docker logs "$name"
    exit 1
    ;;
  stop)
    if docker container inspect "$name" >/dev/null 2>&1; then
      [[ "$(docker inspect --format '{{index .Config.Labels "org.prufai.purpose"}}' "$name")" == hosted-ci-minio ]]
      docker stop "$name" >/dev/null
      docker rm --volumes "$name" >/dev/null
    fi
    ;;
  *) echo "Usage: bash scripts/run-ci-minio.sh start|stop" >&2; exit 2 ;;
esac
