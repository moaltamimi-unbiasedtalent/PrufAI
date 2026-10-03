"""Allow-listed memory tools for the AI worker.

The worker can read bounded context and submit a proposal callback. There is no
approval or activation capability in this module.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol


class MemoryReader(Protocol):
    def retrieve(self, query: str, limit: int = 20) -> list[dict[str, Any]]: ...


class ShortTermMemoryTool:
    name = "short_term_memory.read_write"

    def __init__(self, backend: Any, tenant_id: Any, run_id: Any) -> None:
        self.backend, self.tenant_id, self.run_id = backend, tenant_id, run_id

    def put(self, namespace: str, key: str, value: Any, ttl_seconds: int = 3600) -> None:
        self.backend.put(self.tenant_id, self.run_id, namespace, key, value, ttl_seconds)

    def get(self, namespace: str, key: str) -> Any:
        return self.backend.get(self.tenant_id, self.run_id, namespace, key)


class LongTermMemoryReadTool:
    name = "long_term_memory.retrieve"

    def __init__(self, reader: MemoryReader) -> None:
        self.reader = reader

    def retrieve(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        return self.reader.retrieve(query, min(limit, 50))


class LongTermMemoryProposalTool:
    name = "long_term_memory.propose"

    def __init__(self, submit: Callable[[dict[str, Any]], str]) -> None:
        self.submit = submit

    def propose(self, content: dict[str, Any], evidence_ids: list[str]) -> str:
        return self.submit(
            {"content": content, "evidence_ids": evidence_ids[:50], "status": "REVIEW_REQUIRED"}
        )


APPROVAL_TOOL_NAMES: frozenset[str] = frozenset()
