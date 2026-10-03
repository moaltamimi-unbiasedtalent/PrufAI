import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.memory.service import (
    ContextItem,
    InMemoryShortTermMemory,
    MemoryContextAssembler,
    MemoryPrecedencePolicy,
)
from app.models.enums import MemoryContextOrigin


def test_precedence_never_allows_short_term_to_win() -> None:
    items = [
        ContextItem(MemoryContextOrigin.SHORT_TERM_MEMORY, "policy", "unsafe"),
        ContextItem(MemoryContextOrigin.RULE, "policy", "deterministic"),
        ContextItem(MemoryContextOrigin.AUTHORITATIVE_KNOWLEDGE, "source", "normative"),
    ]
    assembled = MemoryContextAssembler().assemble(items)
    assert [item.origin for item in assembled] == [
        MemoryContextOrigin.AUTHORITATIVE_KNOWLEDGE,
        MemoryContextOrigin.RULE,
        MemoryContextOrigin.SHORT_TERM_MEMORY,
    ]
    assert MemoryPrecedencePolicy.can_override(
        MemoryContextOrigin.RULE, MemoryContextOrigin.SHORT_TERM_MEMORY
    )


def test_short_term_backend_isolated_bounded_and_ttl() -> None:
    backend = InMemoryShortTermMemory()
    tenant, other_tenant, run = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    backend.put(tenant, run, "workflow", "state", {"step": 1}, ttl_seconds=1)
    assert backend.get(tenant, run, "workflow", "state") == {"step": 1}
    assert backend.get(other_tenant, run, "workflow", "state") is None
    with pytest.raises(ValueError):
        backend.put(tenant, run, "workflow", "too-large", "x" * 20_000)


def test_context_drops_expired_values() -> None:
    now = datetime.now(timezone.utc)
    item = ContextItem(
        MemoryContextOrigin.APPROVED_LONG_TERM_MEMORY,
        "old",
        "x",
        expires_at=now - timedelta(seconds=1),
    )
    assert MemoryContextAssembler().assemble([item], now=now) == ()
