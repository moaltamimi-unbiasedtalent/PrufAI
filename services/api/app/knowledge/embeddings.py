from __future__ import annotations

import hashlib
import json
import math
import time
from typing import Iterable, Protocol
from urllib import request


class EmbeddingError(RuntimeError):
    pass


class EmbeddingProvider(Protocol):
    provider: str
    model: str
    dimension: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class DeterministicTestEmbeddingProvider:
    provider = "test-deterministic"

    def __init__(self, dimension: int = 384, model: str = "deterministic-v1") -> None:
        if dimension != 384:
            raise ValueError("the deterministic test embedding dimension is fixed at 384")
        self.dimension = dimension
        self.model = model

    def _embed(self, text: str) -> list[float]:
        values: list[float] = []
        for index in range(self.dimension):
            digest = hashlib.sha256(f"{index}:{text}".encode()).digest()
            values.append((int.from_bytes(digest[:4], "big") / 2**31) - 1.0)
        norm = math.sqrt(sum(value * value for value in values)) or 1.0
        return [value / norm for value in values]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class OpenAICompatibleEmbeddingProvider:
    provider = "openai-compatible"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        dimension: int = 384,
        endpoint: str = "https://api.openai.com/v1/embeddings",
        timeout_seconds: int = 20,
        retries: int = 2,
    ) -> None:
        if not api_key:
            raise EmbeddingError("embedding provider API key is not configured")
        if dimension != 384:
            raise ValueError("the MVP embedding dimension is fixed at 384")
        self.api_key = api_key
        self.model = model
        self.dimension = dimension
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds
        self.retries = retries

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload = json.dumps(
            {"model": self.model, "input": texts, "dimensions": self.dimension}
        ).encode()
        for attempt in range(self.retries + 1):
            try:
                req = request.Request(
                    self.endpoint,
                    data=payload,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with request.urlopen(req, timeout=self.timeout_seconds) as response:  # noqa: S310
                    body = json.loads(response.read().decode("utf-8"))
                vectors = [
                    item["embedding"]
                    for item in sorted(body["data"], key=lambda item: item["index"])
                ]
                if any(len(vector) != self.dimension for vector in vectors):
                    raise EmbeddingError("embedding provider returned the wrong dimension")
                return vectors
            except EmbeddingError:
                raise
            except Exception as exc:
                if attempt >= self.retries:
                    raise EmbeddingError("embedding provider request failed") from exc
                time.sleep(0.1 * (attempt + 1))
        raise EmbeddingError("embedding provider request failed")

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


def validate_dimension(vector: Iterable[float], dimension: int) -> list[float]:
    values = list(vector)
    if len(values) != dimension:
        raise EmbeddingError("embedding dimension does not match the index profile")
    return values
