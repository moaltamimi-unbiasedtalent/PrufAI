from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol
from urllib import request

from app.services.retrieval import CitationReadyResult


class RerankerError(RuntimeError):
    pass


@dataclass(frozen=True)
class RerankingProfile:
    profile_id: str
    name: str
    version: str
    provider: str
    model: str
    candidate_limit: int = 50
    final_limit: int = 10
    configuration: Optional[Dict[str, Any]] = None


class Reranker(Protocol):
    provider: str
    model: str

    def rerank(
        self, query: str, candidates: list[CitationReadyResult], profile: RerankingProfile
    ) -> list[CitationReadyResult]: ...


class DeterministicTestReranker:
    provider = "test-deterministic"
    model = "deterministic-reranker-v1"

    def rerank(
        self, query: str, candidates: list[CitationReadyResult], profile: RerankingProfile
    ) -> list[CitationReadyResult]:
        terms = {term.lower() for term in query.split() if term}
        ranked = sorted(
            candidates,
            key=lambda item: (
                sum(term in item.text.lower() for term in terms),
                hashlib.sha256((query + str(item.chunk_id)).encode()).hexdigest(),
            ),
            reverse=True,
        )[: profile.final_limit]
        for rank, candidate in enumerate(ranked, 1):
            candidate.rerank_score = float(sum(term in candidate.text.lower() for term in terms))
            candidate.rerank_rank = rank
        return ranked


class OpenAICompatibleReranker:
    provider = "openai-compatible"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        endpoint: str = "https://api.openai.com/v1/rerank",
        timeout_seconds: int = 20,
        retries: int = 2,
    ) -> None:
        if not api_key:
            raise RerankerError("reranker API key is not configured")
        self.api_key = api_key
        self.model = model
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds
        self.retries = retries

    def rerank(
        self, query: str, candidates: list[CitationReadyResult], profile: RerankingProfile
    ) -> list[CitationReadyResult]:
        payload = json.dumps(
            {
                "model": self.model,
                "query": query,
                "documents": [
                    candidate.text for candidate in candidates[: profile.candidate_limit]
                ],
                "top_n": profile.final_limit,
            }
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
                output = []
                for item in body["results"]:
                    index = int(item["index"])
                    candidate = candidates[index]
                    candidate.rerank_score = float(item["relevance_score"])
                    output.append(candidate)
                for rank, candidate in enumerate(output, 1):
                    candidate.rerank_rank = rank
                return output
            except Exception as exc:
                if attempt >= self.retries:
                    raise RerankerError("reranking provider request failed") from exc
                time.sleep(0.1 * (attempt + 1))
        raise RerankerError("reranking provider request failed")


def build_reranker(settings: Any) -> Reranker:
    backend = settings.knowledge_reranker_backend
    if backend == "deterministic":
        if settings.environment == "production":
            raise RerankerError("production reranking cannot use the deterministic test backend")
        return DeterministicTestReranker()
    if backend == "openai":
        return OpenAICompatibleReranker(
            api_key=settings.knowledge_reranker_api_key,
            model=settings.knowledge_reranker_model,
            timeout_seconds=settings.knowledge_reranker_timeout_seconds,
        )
    raise RerankerError("unsupported reranker backend")
