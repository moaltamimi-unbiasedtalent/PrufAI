"""Process-local counters and fixed-vocabulary operational logs."""

import json
import logging
from collections import Counter
from enum import Enum
from threading import Lock

logger = logging.getLogger("prufai.operations")
API_VERSION = "0.1.0"


class Signal(str, Enum):
    POSTGRESQL = "postgresql.readiness"
    REDIS = "redis.readiness"
    STORAGE = "storage.capability"
    AUTHENTICATION = "authentication.dependency"
    WORKER_RETRY = "worker.retry_exhausted"
    WORKER_LEASE = "worker.stale_lease"
    STORAGE_INTEGRITY = "storage.integrity"
    RESULT_RELEASE = "result_release.operation"


class Failure(str, Enum):
    UNAVAILABLE = "UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    AUTHENTICATION_REJECTED = "AUTHENTICATION_REJECTED"
    MISCONFIGURED = "MISCONFIGURED"
    INTEGRITY_MISMATCH = "INTEGRITY_MISMATCH"
    RETRY_EXHAUSTED = "RETRY_EXHAUSTED"
    STALE_LEASE = "STALE_LEASE"
    OPERATION_FAILED = "OPERATION_FAILED"


_counts: Counter[tuple[Signal, Failure]] = Counter()
_lock = Lock()


def record(signal: Signal, category: Failure) -> None:
    with _lock:
        key = (signal, category)
        _counts[key] = min(_counts[key] + 1, 2**31 - 1)
        count = _counts[key]
    # Log the first and power-of-two observations to bound outage log volume.
    if count & (count - 1) == 0:
        logger.warning(
            "%s",
            json.dumps(
                {
                    "event": "operational_failure",
                    "operation": signal.value,
                    "failure_category": category.value,
                    "status_class": "failure",
                    "service_version": API_VERSION,
                    "observations": count,
                },
                separators=(",", ":"),
            ),
        )


def counters() -> dict[str, dict[str, int]]:
    with _lock:
        result: dict[str, dict[str, int]] = {}
        for (signal, category), count in _counts.items():
            result.setdefault(signal.value, {})[category.value] = count
        return result
