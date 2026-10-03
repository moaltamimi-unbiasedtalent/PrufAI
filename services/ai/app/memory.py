"""Allow-listed memory tools for the AI worker.

The worker can read bounded context and submit a proposal callback. There is no
approval or activation capability in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol


@dataclass(frozen=True)
class MemoryPolicy:
    """Explicit capability policy attached to an agent/workflow invocation."""

    short_term_read: bool = False
    short_term_write: bool = False
    long_term_read: bool = False
    long_term_propose: bool = False
    allowed_categories: frozenset[str] = frozenset()
    allowed_scope: str = "ORGANISATION_PRIVATE"
    maximum_results: int = 20

    def __post_init__(self) -> None:
        if not 0 < self.maximum_results <= 50:
            raise ValueError("maximum_results must be between 1 and 50")

    def allows(self, capability: str) -> bool:
        return {
            SHORT_TERM_MEMORY_READ: self.short_term_read,
            SHORT_TERM_MEMORY_WRITE: self.short_term_write,
            LONG_TERM_MEMORY_READ: self.long_term_read,
            LONG_TERM_MEMORY_PROPOSE: self.long_term_propose,
        }.get(capability, False)


SHORT_TERM_MEMORY_READ = "short_term_memory.read"
SHORT_TERM_MEMORY_WRITE = "short_term_memory.write"
LONG_TERM_MEMORY_READ = "long_term_memory.read"
LONG_TERM_MEMORY_PROPOSE = "long_term_memory.propose"


class MemoryReader(Protocol):
    def retrieve(self, query: str, limit: int = 20) -> list[dict[str, Any]]: ...


class ShortTermMemoryTool:
    name = SHORT_TERM_MEMORY_WRITE

    def __init__(self, backend: Any, tenant_id: Any, run_id: Any) -> None:
        self.backend, self.tenant_id, self.run_id = backend, tenant_id, run_id

    def put(self, namespace: str, key: str, value: Any, ttl_seconds: int = 3600) -> None:
        self.backend.put(self.tenant_id, self.run_id, namespace, key, value, ttl_seconds)

    def get(self, namespace: str, key: str) -> Any:
        return self.backend.get(self.tenant_id, self.run_id, namespace, key)


class ShortTermMemoryReadTool(ShortTermMemoryTool):
    name = SHORT_TERM_MEMORY_READ

    def put(self, namespace: str, key: str, value: Any, ttl_seconds: int = 3600) -> None:
        raise PermissionError("read-only short-term memory tool")


class LongTermMemoryReadTool:
    name = LONG_TERM_MEMORY_READ

    def __init__(self, reader: MemoryReader) -> None:
        self.reader = reader

    def retrieve(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        return self.reader.retrieve(query, min(limit, 50))


class LongTermMemoryProposalTool:
    name = LONG_TERM_MEMORY_PROPOSE

    def __init__(self, submit: Callable[[dict[str, Any]], str]) -> None:
        self.submit = submit

    def propose(self, content: dict[str, Any], evidence_ids: list[str]) -> str:
        return self.submit(
            {"content": content, "evidence_ids": evidence_ids[:50], "status": "REVIEW_REQUIRED"}
        )


APPROVAL_TOOL_NAMES: frozenset[str] = frozenset()
