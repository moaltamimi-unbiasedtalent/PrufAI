from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from app.models.enums import MemoryContextOrigin

SHORT_TTL_SECONDS = 3600
SHORT_MAX_ITEMS = 100
SHORT_MAX_ITEM_BYTES = 16_384
SHORT_MAX_READ_RESULTS = 50
HITL_RETENTION_SECONDS = 86_400
LONG_MAX_CONTEXT_ITEMS = 50


class MemoryBackendError(RuntimeError):
    pass


class MemoryPrecedencePolicy:
    ORDER = (
        MemoryContextOrigin.AUTHORITATIVE_KNOWLEDGE,
        MemoryContextOrigin.RULES,
        MemoryContextOrigin.APPROVED_POLICY,
        MemoryContextOrigin.APPROVED_LONG_TERM_MEMORY,
        MemoryContextOrigin.SHORT_TERM_MEMORY,
        MemoryContextOrigin.USER_INPUT,
        MemoryContextOrigin.SYSTEM,
    )

    @classmethod
    def priority(cls, origin: MemoryContextOrigin) -> int:
        return len(cls.ORDER) - cls.ORDER.index(origin)

    @classmethod
    def can_override(cls, higher: MemoryContextOrigin, lower: MemoryContextOrigin) -> bool:
        return cls.priority(higher) >= cls.priority(lower)


@dataclass(frozen=True)
class ContextItem:
    origin: MemoryContextOrigin
    stable_id: str
    value: Any
    version: str | None = None
    checksum: str | None = None
    provenance: tuple[str, ...] = ()
    expires_at: datetime | None = None


class MemoryContextAssembler:
    def assemble(
        self, items: list[ContextItem] | tuple[ContextItem, ...], now: datetime | None = None
    ) -> tuple[ContextItem, ...]:
        at = now or datetime.now(timezone.utc)
        eligible = [item for item in items if item.expires_at is None or item.expires_at > at]
        if len(eligible) > LONG_MAX_CONTEXT_ITEMS:
            eligible = sorted(
                eligible, key=lambda i: (-MemoryPrecedencePolicy.priority(i.origin), i.stable_id)
            )[:LONG_MAX_CONTEXT_ITEMS]
        return tuple(
            sorted(
                eligible, key=lambda i: (-MemoryPrecedencePolicy.priority(i.origin), i.stable_id)
            )
        )

    def resolve(self, items: list[ContextItem], key: str) -> ContextItem | None:
        candidates = [item for item in self.assemble(items) if item.stable_id == key]
        return candidates[0] if candidates else None


class ShortTermMemoryBackend(Protocol):
    def put(
        self,
        tenant_id: uuid.UUID | None,
        run_id: uuid.UUID,
        namespace: str,
        key: str,
        value: Any,
        ttl_seconds: int = SHORT_TTL_SECONDS,
    ) -> None: ...
    def get(
        self, tenant_id: uuid.UUID | None, run_id: uuid.UUID, namespace: str, key: str
    ) -> Any | None: ...
    def delete(
        self, tenant_id: uuid.UUID | None, run_id: uuid.UUID, namespace: str, key: str
    ) -> None: ...

    def renew(
        self,
        tenant_id: uuid.UUID | None,
        run_id: uuid.UUID,
        namespace: str,
        key: str,
        ttl_seconds: int = SHORT_TTL_SECONDS,
    ) -> None: ...


def _key(tenant_id: uuid.UUID | None, run_id: uuid.UUID, namespace: str, key: str) -> str:
    if not namespace or not key or any(part in namespace + key for part in ("/", "\\", "..")):
        raise ValueError("unsafe memory key")
    return f"prufai:memory:short:{tenant_id or 'platform'}:{run_id}:{namespace}:{key}"


def _encoded(value: Any) -> str:
    try:
        result = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("short-term memory must be JSON serializable") from exc
    if len(result.encode()) > SHORT_MAX_ITEM_BYTES:
        raise ValueError("short-term memory item exceeds bound")
    return result


class InMemoryShortTermMemory:
    """Explicit test backend; never selected implicitly in production."""

    test_only = True

    def __init__(self) -> None:
        self._values: dict[str, tuple[float, str]] = {}

    def _count(self, tenant_id: uuid.UUID | None, run_id: uuid.UUID, namespace: str) -> int:
        prefix = f"prufai:memory:short:{tenant_id or 'platform'}:{run_id}:{namespace}:"
        return sum(key.startswith(prefix) for key in self._values)

    def put(
        self,
        tenant_id: uuid.UUID | None,
        run_id: uuid.UUID,
        namespace: str,
        key: str,
        value: Any,
        ttl_seconds: int = SHORT_TTL_SECONDS,
    ) -> None:
        item_key = _key(tenant_id, run_id, namespace, key)
        if (
            self._count(tenant_id, run_id, namespace) >= SHORT_MAX_ITEMS
            and item_key not in self._values
        ):
            raise ValueError("short-term memory item count exceeded")
        if ttl_seconds <= 0:
            raise ValueError("ttl must be positive")
        self._values[item_key] = (time.monotonic() + ttl_seconds, _encoded(value))

    def get(
        self, tenant_id: uuid.UUID | None, run_id: uuid.UUID, namespace: str, key: str
    ) -> Any | None:
        item_key = _key(tenant_id, run_id, namespace, key)
        item = self._values.get(item_key)
        if item is None:
            return None
        if item[0] <= time.monotonic():
            self._values.pop(item_key, None)
            return None
        return json.loads(item[1])

    def renew(
        self,
        tenant_id: uuid.UUID | None,
        run_id: uuid.UUID,
        namespace: str,
        key: str,
        ttl_seconds: int = SHORT_TTL_SECONDS,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl must be positive")
        item_key = _key(tenant_id, run_id, namespace, key)
        if item_key not in self._values or self.get(tenant_id, run_id, namespace, key) is None:
            raise MemoryBackendError("short-term memory item unavailable")
        self._values[item_key] = (time.monotonic() + ttl_seconds, self._values[item_key][1])

    def list(
        self,
        tenant_id: uuid.UUID | None,
        run_id: uuid.UUID,
        namespace: str,
        limit: int = SHORT_MAX_READ_RESULTS,
    ) -> list[Any]:
        if limit < 1:
            raise ValueError("limit must be positive")
        prefix = f"prufai:memory:short:{tenant_id or 'platform'}:{run_id}:{namespace}:"
        values = []
        for item_key in list(self._values):
            if item_key.startswith(prefix):
                key = item_key.rsplit(":", 1)[-1]
                value = self.get(tenant_id, run_id, namespace, key)
                if value is not None:
                    values.append(value)
                if len(values) >= min(limit, SHORT_MAX_READ_RESULTS):
                    break
        return values

    def delete(
        self, tenant_id: uuid.UUID | None, run_id: uuid.UUID, namespace: str, key: str
    ) -> None:
        self._values.pop(_key(tenant_id, run_id, namespace, key), None)


class RedisShortTermMemory:
    test_only = False

    def __init__(self, client: Any) -> None:
        self.client = client

    def renew(
        self,
        tenant_id: uuid.UUID | None,
        run_id: uuid.UUID,
        namespace: str,
        key: str,
        ttl_seconds: int = SHORT_TTL_SECONDS,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl must be positive")
        try:
            if not self.client.expire(_key(tenant_id, run_id, namespace, key), ttl_seconds):
                raise MemoryBackendError("short-term memory item unavailable")
        except MemoryBackendError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise MemoryBackendError("redis short-term memory unavailable") from exc

    def put(
        self,
        tenant_id: uuid.UUID | None,
        run_id: uuid.UUID,
        namespace: str,
        key: str,
        value: Any,
        ttl_seconds: int = SHORT_TTL_SECONDS,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl must be positive")
        try:
            self.client.setex(_key(tenant_id, run_id, namespace, key), ttl_seconds, _encoded(value))
        except Exception as exc:  # noqa: BLE001
            raise MemoryBackendError("redis short-term memory unavailable") from exc

    def get(
        self, tenant_id: uuid.UUID | None, run_id: uuid.UUID, namespace: str, key: str
    ) -> Any | None:
        try:
            value = self.client.get(_key(tenant_id, run_id, namespace, key))
            if value is None:
                return None
            return json.loads(value.decode() if isinstance(value, bytes) else value)
        except Exception as exc:  # noqa: BLE001
            raise MemoryBackendError("redis short-term memory unavailable") from exc

    def delete(
        self, tenant_id: uuid.UUID | None, run_id: uuid.UUID, namespace: str, key: str
    ) -> None:
        try:
            self.client.delete(_key(tenant_id, run_id, namespace, key))
        except Exception as exc:  # noqa: BLE001
            raise MemoryBackendError("redis short-term memory unavailable") from exc


def canonical_checksum(content: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def memory_checksum(
    content: dict[str, Any],
    *,
    scope: str,
    category: str,
    evidence_ids: tuple[str, ...] = (),
) -> str:
    """Checksum substantive memory identity; timestamps/audit fields are excluded."""
    return canonical_checksum(
        {
            "category": category,
            "content": content,
            "evidence_ids": sorted(evidence_ids),
            "scope": scope,
        }
    )


def retain_for_human_review(
    backend: ShortTermMemoryBackend,
    tenant_id: uuid.UUID | None,
    run_id: uuid.UUID,
    namespace: str,
    key: str,
) -> None:
    """Refresh required context while an AIRun is waiting for human review."""
    backend.renew(tenant_id, run_id, namespace, key, HITL_RETENTION_SECONDS)
