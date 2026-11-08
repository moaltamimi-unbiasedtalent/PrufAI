#!/usr/bin/env bash
# Verify the scanned image on a fresh volume in the disposable GitHub runner.
set -euo pipefail
out="${1:?container evidence directory required}"
: "${GITHUB_ACTIONS:?run in the hosted O1A evidence job}"
[[ "$GITHUB_ACTIONS" == true ]]
image="$(jq -er .image_id "$out/minio-source.identity.json")"
export PRUFAI_LID_PG_PASSWORD=o1a-disposable PRUFAI_DISPOSABLE_DATABASE=o1a_disposable
export OBJECT_STORAGE_ACCESS_KEY=o1a-synthetic OBJECT_STORAGE_SECRET_KEY=o1a-synthetic-only
export OBJECT_STORAGE_BUCKET=o1a-private OBJECT_STORAGE_SECURE=0 PRUFAI_REQUIRE_OBJECT_STORAGE_INTEGRATION=1

for file in infra/docker-compose.yml infra/compose.lid-demo.yml; do
  docker compose -f "$file" config --format json |
    jq -e '.services.minio.build.dockerfile == "minio-source.Dockerfile" and
      any(.services.minio.volumes[]; .type == "volume" and .target == "/data")' >/dev/null
done

volume="$(docker volume create)"
container=""
cleanup() {
  if [[ -n "$container" ]]; then
    docker rm -f "$container" >/dev/null
  fi
  docker volume rm "$volume" >/dev/null
}
trap cleanup EXIT

container="$(docker run --detach --publish 127.0.0.1::9000 \
  --mount "type=volume,source=$volume,target=/data" \
  --env "MINIO_ROOT_USER=$OBJECT_STORAGE_ACCESS_KEY" --env "MINIO_ROOT_PASSWORD=$OBJECT_STORAGE_SECRET_KEY" "$image")"
export OBJECT_STORAGE_ENDPOINT="http://$(docker port "$container" 9000/tcp)"
if ! curl --silent --show-error --fail --retry 20 --retry-connrefused --retry-delay 1 \
  --max-time 5 "$OBJECT_STORAGE_ENDPOINT/minio/health/ready" >/dev/null; then
  docker logs "$container"
  exit 1
fi

python - <<'PY'
import os

import boto3

s3 = boto3.client(
    "s3", endpoint_url=os.environ["OBJECT_STORAGE_ENDPOINT"],
    aws_access_key_id=os.environ["OBJECT_STORAGE_ACCESS_KEY"],
    aws_secret_access_key=os.environ["OBJECT_STORAGE_SECRET_KEY"],
    region_name="us-east-1",
)
bucket = os.environ["OBJECT_STORAGE_BUCKET"]
s3.create_bucket(Bucket=bucket)
s3.put_object(Bucket=bucket, Key="private-check", Body=b"synthetic")
PY
status="$(curl --silent --show-error -o /dev/null -w '%{http_code}' "$OBJECT_STORAGE_ENDPOINT/$OBJECT_STORAGE_BUCKET/private-check")"
[[ "$status" == 403 ]]
cd services/api
python -m pytest tests/test_object_storage_integration.py -v --junitxml="$out/minio-source-compatibility.xml"
python - "$out/minio-source-compatibility.xml" <<'PY'
import sys
from xml.etree import ElementTree

cases = ElementTree.parse(sys.argv[1]).findall(".//testcase")
if len(cases) != 1 or any(case.find("skipped") is not None for case in cases):
    raise SystemExit("The real private S3 integration must run without skips")
PY
