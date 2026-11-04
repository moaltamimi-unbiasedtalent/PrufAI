"""Unit boundary proof; real private S3 execution is covered separately."""

import io
from typing import Any

import pytest

from app.knowledge.storage import S3CompatibleObjectStorage, StorageError


class BoundedBody(io.BytesIO):
    def __init__(self, value: bytes) -> None:
        super().__init__(value)
        self.read_bounds: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.read_bounds.append(size)
        return super().read(size)


class PrivateClient:
    def __init__(self, content: bytes, declared_size: int) -> None:
        self.body = BoundedBody(content)
        self.declared_size = declared_size
        self.calls = 0

    def get_object(self, **_kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        return {"ContentLength": self.declared_size, "Body": self.body}


def storage(client: PrivateClient) -> S3CompatibleObjectStorage:
    return S3CompatibleObjectStorage(
        endpoint="http://test.invalid",
        bucket="private",
        access_key="test",
        secret_key="test",
        client=client,
    )


def test_bounded_private_audio_read_caps_body_and_closes_it() -> None:
    client = PrivateClient(b"original", 8)
    assert (
        storage(client).get_bounded("private-original", max_bytes=10, expected_size=8)
        == b"original"
    )
    assert client.body.read_bounds == [9]
    assert client.body.closed


@pytest.mark.parametrize(
    "content,declared_size", [(b"oversized body", 8), (b"short", 8), (b"original", 99)]
)
def test_bounded_private_audio_read_rejects_metadata_and_body_mismatch(
    content: bytes, declared_size: int
) -> None:
    client = PrivateClient(content, declared_size)
    with pytest.raises(StorageError):
        storage(client).get_bounded("private-original", max_bytes=10, expected_size=8)
    assert client.body.closed
    assert all(0 <= amount <= 9 for amount in client.body.read_bounds)


def test_invalid_pinned_audio_size_fails_before_storage_io() -> None:
    client = PrivateClient(b"original", 8)
    with pytest.raises(StorageError):
        storage(client).get_bounded("private-original", max_bytes=7, expected_size=8)
    assert client.calls == 0
