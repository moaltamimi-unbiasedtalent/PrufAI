"""Bounded, synthetic request tests for the disposable MinIO ingress job."""

import hashlib
import http.client
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import urlopen

import boto3
from botocore.config import Config


def request(endpoint, method, path, headers=None):
    conn = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=5)
    try:
        conn.request(
            method,
            path,
            body=b"synthetic" if method == "PUT" else None,
            headers=headers or {},
        )
        response = conn.getresponse()
        return response.status, response.getheader("X-Prufai-Storage-Guard")
    finally:
        conn.close()


def main():
    endpoint_url = os.environ["OBJECT_STORAGE_ENDPOINT"]
    endpoint = urlsplit(endpoint_url)
    assert endpoint.scheme == "http" and endpoint.hostname == "127.0.0.1"
    bucket = os.environ["OBJECT_STORAGE_BUCKET"]
    assert bucket == "o1b1-private"
    access_key = os.environ["OBJECT_STORAGE_ACCESS_KEY"]
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=os.environ["OBJECT_STORAGE_SECRET_KEY"],
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}),
    )
    original = b"original synthetic evidence"
    original_sha = hashlib.sha256(original).hexdigest()

    def assert_retained():
        retained = s3.get_object(Bucket=bucket, Key="original")["Body"].read()
        assert hashlib.sha256(retained).hexdigest() == original_sha
        assert [
            item["Key"] for item in s3.list_objects_v2(Bucket=bucket)["Contents"]
        ] == ["original"]

    if sys.argv[1] == "--verify-retained":
        assert_retained()
        print("Original SHA-256 and object inventory unchanged after peer requests")
        return

    s3.create_bucket(Bucket=bucket)
    s3.put_object(
        Bucket=bucket,
        Key="original",
        Body=original,
        ContentType="application/octet-stream",
    )

    assert request(endpoint, "GET", f"/{bucket}/original") == (403, None)
    assert request(endpoint, "PUT", f"/{bucket}/anonymous") == (403, None)
    unsafe = {"X-Amz-Content-Sha256": "STREAMING-UNSIGNED-PAYLOAD-TRAILER"}
    query = urlencode(
        {"X-Amz-Credential": f"{access_key}/20260101/us-east-1/s3/aws4_request"}
    )
    attempts = [
        (f"/{bucket}/original?{query}", unsafe),
        (f"/{bucket}/snowball", {**unsafe, "X-Minio-Extract": "true"}),
    ]
    for path, headers in attempts:
        assert request(endpoint, "PUT", path, headers) == (
            403,
            "unsigned-trailer-denied",
        )
        retained = s3.get_object(Bucket=bucket, Key="original")["Body"].read()
        assert hashlib.sha256(retained).hexdigest() == original_sha
        assert [
            item["Key"] for item in s3.list_objects_v2(Bucket=bucket)["Contents"]
        ] == ["original"]

    head = s3.head_object(Bucket=bucket, Key="original")
    assert (
        head["ContentLength"] == len(original)
        and head["ContentType"] == "application/octet-stream"
    )
    partial = s3.get_object(Bucket=bucket, Key="original", Range="bytes=2-9")
    assert partial["ResponseMetadata"]["HTTPStatusCode"] == 206
    assert partial["Body"].read() == original[2:10]
    presigned = s3.generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": "original"}, ExpiresIn=60
    )
    with urlopen(presigned, timeout=5) as response:
        assert response.read() == original

    upload_id = s3.create_multipart_upload(Bucket=bucket, Key="multipart")["UploadId"]
    part = s3.upload_part(
        Bucket=bucket, Key="multipart", UploadId=upload_id, PartNumber=1, Body=original
    )
    s3.complete_multipart_upload(
        Bucket=bucket,
        Key="multipart",
        UploadId=upload_id,
        MultipartUpload={"Parts": [{"PartNumber": 1, "ETag": part["ETag"]}]},
    )
    assert s3.get_object(Bucket=bucket, Key="multipart")["Body"].read() == original
    s3.delete_object(Bucket=bucket, Key="multipart")
    assert [item["Key"] for item in s3.list_objects_v2(Bucket=bucket)["Contents"]] == [
        "original"
    ]
    Path(sys.argv[1]).write_text(
        json.dumps(
            {
                "scope": "disposable storage only",
                "unsigned_trailer_routes_denied": [
                    "query credential",
                    "Snowball extraction",
                ],
                "anonymous_read_write_denied": True,
                "original_sha256": original_sha,
                "signed_put_get_head_range_presigned_multipart_delete": "PASS",
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
