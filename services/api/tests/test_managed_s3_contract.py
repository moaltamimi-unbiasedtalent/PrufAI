"""Offline Bucket owner enforced request contract using the real botocore serializer."""

import base64
import io
import zlib
from urllib.parse import parse_qs, urlsplit

import pytest
from botocore.awsrequest import AWSResponse
from botocore.exceptions import ClientError
from botocore.httpsession import URLLib3Session

from app.config import Settings
from app.knowledge.storage import S3CompatibleObjectStorage, StorageError, build_object_storage

BUCKET = "synthetic-prufai-trial"
KEY = "o1b3/example/source.txt"
PAYLOAD = b"original synthetic evidence"
ENDPOINT = "https://s3.eu-central-1.amazonaws.com"


def trial_settings(**overrides) -> Settings:
    return Settings(
        **{
            "environment": "test",
            "object_storage_backend": "aws-s3-trial",
            "object_storage_aws_trial_approved": True,
            "object_storage_endpoint": ENDPOINT,
            "object_storage_region": "eu-central-1",
            "object_storage_secure": True,
            "object_storage_bucket": BUCKET,
            "object_storage_access_key": "synthetic-access",
            "object_storage_secret_key": "synthetic-secret",
            "object_storage_session_token": "synthetic-token",
            **overrides,
        }
    )


class ResponseBody(io.BytesIO):
    def stream(self, **_):
        yield self.read()


@pytest.fixture
def contract_transport(monkeypatch):
    requests = []

    def send(_session, request):
        url = urlsplit(request.url)
        assert url.scheme == "https"
        assert (url.hostname, url.path) in {
            ("s3.eu-central-1.amazonaws.com", f"/{BUCKET}/{KEY}"),
            (f"{BUCKET}.s3.eu-central-1.amazonaws.com", f"/{KEY}"),
        }
        headers = {
            k.lower(): v.decode("ascii") if isinstance(v, bytes) else v
            for k, v in request.headers.items()
        }
        auth = headers["authorization"]
        assert auth.startswith("AWS4-HMAC-SHA256 Credential=synthetic-access/")
        assert "/eu-central-1/s3/aws4_request" in auth
        assert "x-amz-security-token" in auth
        assert headers["x-amz-security-token"] == "synthetic-token"
        assert len(auth.split("Signature=", 1)[1]) == 64

        body = request.body.read() if hasattr(request.body, "read") else request.body
        query = parse_qs(url.query, keep_blank_values=True)
        requests.append((request.method, query, headers, body))
        response_headers = {"content-type": "application/xml"}
        status = 200
        if any(h == "x-amz-acl" or h.startswith("x-amz-grant-") for h in headers):
            status = 400
            xml = b"<Error><Code>AccessControlListNotSupported</Code></Error>"
        elif "uploads" in query:
            xml = (
                b"<InitiateMultipartUploadResult><UploadId>synthetic-upload</UploadId>"
                b"</InitiateMultipartUploadResult>"
            )
        elif request.method == "POST":
            xml = (
                b'<CompleteMultipartUploadResult><ETag>"completed"</ETag>'
                b"</CompleteMultipartUploadResult>"
            )
        else:
            xml = b""
            response_headers["etag"] = '"synthetic-part"'
        return AWSResponse(request.url, status, response_headers, ResponseBody(xml))

    monkeypatch.setattr(URLLib3Session, "send", send)
    return requests


def assert_no_acl(headers):
    assert not any(h == "x-amz-acl" or h.startswith("x-amz-grant-") for h in headers)


def assert_body_and_checksum(headers, body):
    assert headers["x-amz-sdk-checksum-algorithm"] == "CRC32"
    checksum = base64.b64encode(zlib.crc32(PAYLOAD).to_bytes(4, "big"))
    if "x-amz-trailer" in headers:
        assert headers["x-amz-trailer"] == "x-amz-checksum-crc32"
        assert headers["x-amz-decoded-content-length"] == str(len(PAYLOAD))
        assert body == (
            f"{len(PAYLOAD):x}\r\n".encode()
            + PAYLOAD
            + b"\r\n0\r\nx-amz-checksum-crc32:"
            + checksum
            + b"\r\n\r\n"
        )
    else:
        assert headers["x-amz-checksum-crc32"] == checksum.decode()
        assert body == PAYLOAD


def test_application_put_is_signed_and_acl_free(contract_transport):
    storage = build_object_storage(trial_settings())
    assert isinstance(storage, S3CompatibleObjectStorage)
    storage.put(KEY, PAYLOAD, "text/plain")
    [(method, query, headers, body)] = contract_transport
    assert (method, query) == ("PUT", {})
    assert_no_acl(headers)
    assert headers["content-type"] == "text/plain"
    assert_body_and_checksum(headers, body)


def test_multipart_sdk_contract_preserves_metadata_without_acls(contract_transport):
    storage = build_object_storage(trial_settings())
    assert isinstance(storage, S3CompatibleObjectStorage)
    s3 = storage.client
    upload_id = s3.create_multipart_upload(
        Bucket=BUCKET, Key=KEY, ContentType="text/plain", Metadata={"purpose": "synthetic"}
    )["UploadId"]
    part = s3.upload_part(Bucket=BUCKET, Key=KEY, UploadId=upload_id, PartNumber=1, Body=PAYLOAD)
    s3.complete_multipart_upload(
        Bucket=BUCKET,
        Key=KEY,
        UploadId=upload_id,
        MultipartUpload={"Parts": [{"PartNumber": 1, "ETag": part["ETag"]}]},
    )
    start, part_request, completion = contract_transport
    assert start[:2] == ("POST", {"uploads": [""]})
    assert start[2]["content-type"] == "text/plain"
    assert start[2]["x-amz-meta-purpose"] == "synthetic"
    assert part_request[:2] == ("PUT", {"uploadId": ["synthetic-upload"], "partNumber": ["1"]})
    assert_body_and_checksum(part_request[2], part_request[3])
    assert completion[:2] == ("POST", {"uploadId": ["synthetic-upload"]})
    assert b"<PartNumber>1</PartNumber>" in completion[3]
    for _, _, headers, _ in contract_transport:
        assert_no_acl(headers)


@pytest.mark.parametrize(
    "acl_params",
    [
        {"ACL": "private"},
        {"ACL": "public-read"},
        {"GrantRead": 'id="synthetic-canonical-id"'},
    ],
)
def test_acl_disabled_fixture_rejects_acl_or_grants(contract_transport, acl_params):
    storage = build_object_storage(trial_settings())
    assert isinstance(storage, S3CompatibleObjectStorage)
    with pytest.raises(ClientError) as raised:
        storage.client.put_object(Bucket=BUCKET, Key=KEY, Body=PAYLOAD, **acl_params)
    assert raised.value.response["Error"]["Code"] == "AccessControlListNotSupported"
    assert len(contract_transport) == 1


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"object_storage_aws_trial_approved": False}, "approved synthetic"),
        ({"environment": "development"}, "approved synthetic"),
        ({"object_storage_region": "eu-west-1"}, "Frankfurt HTTPS endpoint"),
        (
            {"object_storage_endpoint": "https://s3.eu-west-1.amazonaws.com"},
            "Frankfurt HTTPS endpoint",
        ),
        ({"object_storage_secure": False}, "Frankfurt HTTPS endpoint"),
        ({"object_storage_bucket": "prufai-local"}, "explicit DNS-compatible bucket"),
        ({"object_storage_session_token": ""}, "temporary credentials"),
        ({"object_storage_access_key": ""}, "not fully configured"),
    ],
)
def test_invalid_trial_configuration_fails_before_network(monkeypatch, overrides, message):
    def unexpected_client(*args, **kwargs):
        pytest.fail("invalid configuration reached SDK construction")

    monkeypatch.setattr("boto3.client", unexpected_client)
    with pytest.raises(StorageError, match=message):
        build_object_storage(trial_settings(**overrides))
