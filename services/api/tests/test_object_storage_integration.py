import os

import pytest

from app.knowledge.storage import S3CompatibleObjectStorage

REQUIRE_OBJECT_STORAGE_INTEGRATION = os.getenv("PRUFAI_REQUIRE_OBJECT_STORAGE_INTEGRATION") == "1"
pytestmark = pytest.mark.skipif(
    not os.getenv("OBJECT_STORAGE_ENDPOINT") and not REQUIRE_OBJECT_STORAGE_INTEGRATION,
    reason="OBJECT_STORAGE_ENDPOINT is required for object-storage integration",
)


def test_private_object_storage_supports_bounded_audio_ranges() -> None:
    endpoint = os.getenv("OBJECT_STORAGE_ENDPOINT")
    bucket = os.getenv("OBJECT_STORAGE_BUCKET")
    access_key = os.getenv("OBJECT_STORAGE_ACCESS_KEY")
    secret_key = os.getenv("OBJECT_STORAGE_SECRET_KEY")
    if not all((endpoint, bucket, access_key, secret_key)):
        pytest.fail(
            "PRUFAI_REQUIRE_OBJECT_STORAGE_INTEGRATION=1 requires complete object-storage settings"
        )
    storage = S3CompatibleObjectStorage(
        endpoint=endpoint or "",
        bucket=bucket or "",
        access_key=access_key or "",
        secret_key=secret_key or "",
        secure=os.getenv("OBJECT_STORAGE_SECURE", "0") == "1",
    )
    key = "integration/candidate-audio-range-proof"
    payload = b"OggS" + bytes(range(256)) * 32
    storage.put(key, payload, "audio/ogg")
    try:
        assert storage.size(key) == len(payload)
        assert storage.read_range(key, 100, 199) == payload[100:200]
        assert storage.read_range(key, 100, len(payload) - 1) == payload[100:]
    finally:
        storage.client.delete_object(Bucket=storage.bucket, Key=key)
