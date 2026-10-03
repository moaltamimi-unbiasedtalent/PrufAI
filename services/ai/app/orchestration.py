"""Provider-neutral, deterministic AI orchestration boundary for Phase 4 Step 1.

This module deliberately keeps domain approval in the API database. LangGraph is an
execution dependency for deployed workers; the small runtime protocol below keeps
CI network-free and makes the state/interrupt contract testable without credentials.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Generic, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field

MAX_MODEL_ATTEMPTS = 3
MAX_STRUCTURED_OUTPUT_ATTEMPTS = 2
MAX_TOOL_CALLS_PER_STEP = 3
MAX_TOOL_CALLS_PER_RUN = 12
MAX_RETRIEVAL_RESULTS = 20


class ProviderFailure(RuntimeError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


RETRYABLE_FAILURES = {"TIMEOUT", "RATE_LIMIT", "PROVIDER_UNAVAILABLE"}
NON_RETRYABLE_FAILURES = {
    "INVALID_OUTPUT",
    "CONFIGURATION_ERROR",
    "POLICY_BLOCK",
    "INTERNAL_ERROR",
}


class ModelProfileStatus(str, Enum):
    TEST = "TEST"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class AgentProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposal: str = Field(min_length=1, max_length=4000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=50)
    requested_facts: dict[str, Any] = Field(default_factory=dict)
    output_schema_version: str = "1"


class ReviewProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agrees: bool
    concerns: list[str] = Field(default_factory=list, max_length=20)
    evidence_ids: list[str] = Field(default_factory=list, max_length=50)
    output_schema_version: str = "1"


class DeterministicModel(Protocol):
    profile_code: str

    def invoke(
        self, messages: list[dict[str, str]], output_schema: type[BaseModel]
    ) -> BaseModel: ...


class DeterministicTestModel:
    """Network-free TEST provider; never used as a production fallback."""

    profile_code = "test.deterministic"

    def __init__(self, responses: Mapping[str, Mapping[str, Any]] | None = None) -> None:
        self.responses = dict(responses or {})

    def invoke(self, messages: list[dict[str, str]], output_schema: type[BaseModel]) -> BaseModel:
        key = messages[-1].get("content", "") if messages else ""
        payload = self.responses.get(key)
        if payload is None:
            if output_schema is AgentProposal:
                payload = {"proposal": "Synthetic proposal", "evidence_ids": []}
            else:
                payload = {"agrees": True, "concerns": [], "evidence_ids": []}
        return output_schema.model_validate(payload)

    def invoke_with_retry(
        self,
        messages: list[dict[str, str]],
        output_schema: type[BaseModel],
        max_attempts: int = MAX_MODEL_ATTEMPTS,
    ) -> BaseModel:
        attempts = min(max_attempts, MAX_MODEL_ATTEMPTS)
        for attempt in range(attempts):
            try:
                return self.invoke(messages, output_schema)
            except ProviderFailure as exc:
                if exc.category not in RETRYABLE_FAILURES or attempt == attempts - 1:
                    raise
        raise ProviderFailure("INTERNAL_ERROR", "provider retry exhausted")


class OpenAICompatibleProvider:
    """Production adapter seam. Credentials are supplied by the host, never persisted."""

    def __init__(
        self, invoke_fn: Callable[..., Mapping[str, Any]], timeout_seconds: float = 30.0
    ) -> None:
        self.invoke_fn = invoke_fn
        self.timeout_seconds = timeout_seconds

    def invoke(self, messages: list[dict[str, str]], output_schema: type[BaseModel]) -> BaseModel:
        started = time.monotonic()
        payload = self.invoke_fn(
            messages=messages, timeout=self.timeout_seconds, response_schema=output_schema
        )
        if time.monotonic() - started > self.timeout_seconds:
            raise ProviderFailure("TIMEOUT", "model provider timeout")
        try:
            return output_schema.model_validate(payload)
        except Exception as exc:
            raise ProviderFailure("INVALID_OUTPUT", "structured output validation failed") from exc


class LangChainCompatibleModel:
    """Small provider adapter with the same invoke shape used by LangChain chat models."""

    def __init__(self, provider: OpenAICompatibleProvider) -> None:
        self.provider = provider

    def invoke(self, messages: list[dict[str, str]], output_schema: type[BaseModel]) -> BaseModel:
        return self.provider.invoke(messages, output_schema)


@dataclass(frozen=True)
class RetrievalScope:
    provider_code: str | None = None
    assessment_family: str | None = None
    allowed_document_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    provider_code: str
    citation: str
    provenance: dict[str, Any]


class KnowledgeRetrievalTool:
    name = "KNOWLEDGE_RETRIEVAL"

    def __init__(self, evidence: list[Evidence]) -> None:
        self._evidence = tuple(evidence)

    def search(self, query: str, scope: RetrievalScope) -> list[Evidence]:
        if not query.strip():
            raise ValueError("query is required")
        result = [
            item
            for item in self._evidence
            if scope.provider_code is None or item.provider_code == scope.provider_code
        ]
        if scope.allowed_document_ids:
            result = [item for item in result if item.evidence_id in scope.allowed_document_ids]
        return result[:MAX_RETRIEVAL_RESULTS]


class StandardPackReadTool:
    name = "STANDARD_PACK_READ"

    def __init__(self, packs: Mapping[str, Mapping[str, Any]]) -> None:
        self._packs = dict(packs)

    def read(self, standard_pack_version_id: str) -> Mapping[str, Any]:
        if standard_pack_version_id not in self._packs:
            raise KeyError("standard pack version not found")
        return self._packs[standard_pack_version_id]


class RulesEvaluationTool:
    name = "RULES_EVALUATE"

    def __init__(self, evaluator: Callable[[Mapping[str, Any]], Any]) -> None:
        self._evaluator = evaluator

    def evaluate(self, facts: Mapping[str, Any]) -> Any:
        return self._evaluator(facts)


@dataclass
class ToolBudget:
    per_step: int = MAX_TOOL_CALLS_PER_STEP
    per_run: int = MAX_TOOL_CALLS_PER_RUN
    _step_calls: int = 0
    _run_calls: int = 0

    def consume(self) -> None:
        if self._step_calls >= self.per_step or self._run_calls >= self.per_run:
            raise ProviderFailure("POLICY_BLOCK", "tool-call limit exceeded")
        self._step_calls += 1
        self._run_calls += 1

    def next_step(self) -> None:
        self._step_calls = 0


@dataclass
class WorkflowState:
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    evidence: list[Evidence] = field(default_factory=list)
    proposal: AgentProposal | None = None
    review: ReviewProposal | None = None
    rule_report: Any = None
    status: str = "RUNNING"
    checkpoint: str | None = None


T = TypeVar("T")


class LangGraphRuntime(Generic[T]):
    """Safe symbolic graph runner with explicit interrupt/resume semantics."""

    def __init__(self, max_steps: int = 12) -> None:
        self.max_steps = max_steps

    def run(self, state: T, nodes: list[tuple[str, Callable[[T], T]]], start: int = 0) -> T:
        if len(nodes) > self.max_steps:
            raise ValueError("workflow exceeds iteration limit")
        current = state
        for _, node in nodes[start:]:
            current = node(current)
        return current


def canonical_checksum(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def validate_tool_policy(tools: list[str]) -> None:
    allowed = {KnowledgeRetrievalTool.name, StandardPackReadTool.name, RulesEvaluationTool.name}
    if any(tool not in allowed for tool in tools):
        raise ValueError("tool is not allow-listed")
