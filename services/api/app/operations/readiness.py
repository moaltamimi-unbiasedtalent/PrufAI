"""Independent, read-only operational probes with bounded caller wait."""

import hashlib
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Generic, TypeVar
from urllib.error import URLError
from urllib.parse import urlsplit

import urllib3
from redis import Redis
from redis.backoff import NoBackoff
from redis.exceptions import AuthenticationError, ConnectionError, NoPermissionError, ResponseError
from redis.exceptions import TimeoutError as RedisTimeout
from redis.retry import Retry
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.pool import NullPool

from app.auth.security import rate_limit_key
from app.config import Settings
from app.knowledge.storage import (
    S3CompatibleObjectStorage,
    StorageError,
    StorageIntegrityError,
    build_object_storage,
)
from app.operations.telemetry import Failure, Signal, record

PROBE_DEADLINE = 3.0
CANARY_KEY = "_operations/readiness-canary-v1"
CANARY_BYTES = b"PrufAI synthetic storage readiness canary v1\n"

T = TypeVar("T")


class Probe(Generic[T]):
    """Coalesce matching concurrent probes and cap work during stuck I/O."""

    def __init__(self, name: str):
        self.lock = Lock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=name)
        self.active: Future[T] | None = None
        self.identity: tuple[str, ...] = ()

    def run(self, identity: tuple[str, ...], check: Callable[[], T], timeout: T) -> T:
        with self.lock:
            if self.active is None or self.active.done():
                self.identity = identity
                self.active = self.executor.submit(check)
            elif self.identity != identity:
                return timeout
            future = self.active
        try:
            return future.result(timeout=PROBE_DEADLINE)
        except TimeoutError:
            return timeout


@dataclass(frozen=True)
class Dependency:
    status: str
    failure: Failure | None = None

    def data(self) -> dict[str, str | None]:
        return {"health": self.status, "failure_category": self.failure}


@dataclass(frozen=True)
class Readiness:
    postgres: Dependency
    redis: Dependency
    measured_at: datetime

    @property
    def ready(self) -> bool:
        return self.postgres.status == "HEALTHY" and self.redis.status in {
            "HEALTHY",
            "NOT_CONFIGURED",
        }


@dataclass(frozen=True)
class StorageCapability:
    health: str
    guard: str
    canary: str
    failure_category: Failure | None = None

    def data(self, settings: Settings) -> dict[str, str | None]:
        return {
            "backend": settings.object_storage_backend,
            "health": self.health,
            "guard": self.guard,
            "canary": self.canary,
            "provider_approval": "NOT_VERIFIED",
            "failure_category": self.failure_category,
        }


postgres_probe = Probe[Dependency]("postgres-readiness")
redis_probe = Probe[Dependency]("redis-readiness")
storage_probe = Probe[StorageCapability]("storage-readiness")


def is_database_unavailable(error: DBAPIError) -> bool:
    sqlstate = getattr(error.orig, "sqlstate", None)
    return bool(
        error.connection_invalidated
        or (
            sqlstate
            and (sqlstate.startswith("08") or sqlstate in {"57P01", "57P02", "57P03", "53300"})
        )
        or (sqlstate is None and isinstance(error, OperationalError) and not error.statement)
    )


def _postgres(settings: Settings) -> Dependency:
    url = make_url(settings.database_url)
    options = url.query.get("options", "")
    if url.get_backend_name() != "postgresql" or not isinstance(options, str):
        return Dependency("UNAVAILABLE", Failure.MISCONFIGURED)
    engine = create_engine(
        url,
        poolclass=NullPool,
        connect_args={
            "connect_timeout": 2,
            "options": f"{options} -c statement_timeout=750 -c default_transaction_read_only=on",
        },
    )
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1")).scalar_one()
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "57014":
            return Dependency("UNAVAILABLE", Failure.TIMEOUT)
        if is_database_unavailable(exc):
            return Dependency("UNAVAILABLE", Failure.UNAVAILABLE)
        raise
    finally:
        engine.dispose()
    return Dependency("HEALTHY")


def _redis(settings: Settings) -> Dependency:
    try:
        with Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
            retry=Retry(NoBackoff(), 0),
        ) as client:
            # Same ACL command and key namespace as the limiter. No counters or keys change.
            client.eval("return 1", 1, rate_limit_key("login", "readiness"))
    except AuthenticationError:
        return Dependency("UNAVAILABLE", Failure.AUTHENTICATION_REJECTED)
    except (NoPermissionError, ResponseError, ValueError):
        return Dependency("UNAVAILABLE", Failure.MISCONFIGURED)
    except RedisTimeout:
        return Dependency("UNAVAILABLE", Failure.TIMEOUT)
    except ConnectionError:
        return Dependency("UNAVAILABLE", Failure.UNAVAILABLE)
    return Dependency("HEALTHY")


def measure_readiness(settings: Settings) -> Readiness:
    postgres = postgres_probe.run(
        (settings.database_url,),
        lambda: _postgres(settings),
        Dependency("UNAVAILABLE", Failure.TIMEOUT),
    )
    redis = (
        redis_probe.run(
            (settings.redis_url,),
            lambda: _redis(settings),
            Dependency("UNAVAILABLE", Failure.TIMEOUT),
        )
        if settings.auth_rate_limit_backend == "redis"
        else Dependency("NOT_CONFIGURED")
    )
    for signal, dependency in ((Signal.POSTGRESQL, postgres), (Signal.REDIS, redis)):
        if dependency.failure is not None:
            record(signal, dependency.failure)
    return Readiness(postgres, redis, datetime.now(timezone.utc))


def _guarded_storage(settings: Settings) -> StorageCapability:
    endpoint = settings.object_storage_endpoint
    if "://" not in endpoint:
        endpoint = ("https://" if settings.object_storage_secure else "http://") + endpoint
    url = urlsplit(endpoint)
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
        return StorageCapability(
            "UNAVAILABLE", "NOT_VERIFIED", "NOT_VERIFIED", Failure.MISCONFIGURED
        )
    pool = urllib3.PoolManager(timeout=urllib3.Timeout(total=1.5), retries=False)
    try:
        response = pool.request(
            "HEAD", f"{endpoint.rstrip('/')}/minio/health/ready", redirect=False
        )
        if response.status != 200:
            return StorageCapability(
                "UNAVAILABLE", "RESPONDED", "NOT_VERIFIED", Failure.UNAVAILABLE
            )
    except (urllib3.exceptions.HTTPError, OSError, URLError):
        return StorageCapability("UNAVAILABLE", "UNAVAILABLE", "NOT_VERIFIED", Failure.UNAVAILABLE)
    finally:
        pool.clear()
    if not settings.operations_storage_canary_enabled:
        return StorageCapability("NOT_VERIFIED", "REACHABLE", "STORAGE_CANARY_NOT_CONFIGURED")
    try:
        storage = build_object_storage(settings, metadata_probe=True)
        if not isinstance(storage, S3CompatibleObjectStorage):
            return StorageCapability(
                "UNAVAILABLE", "REACHABLE", "NOT_VERIFIED", Failure.MISCONFIGURED
            )
        content = storage.get_bounded(
            CANARY_KEY, expected_size=len(CANARY_BYTES), max_bytes=len(CANARY_BYTES)
        )
        if hashlib.sha256(content).digest() != hashlib.sha256(CANARY_BYTES).digest():
            raise StorageIntegrityError("synthetic canary digest mismatch")
    except StorageIntegrityError:
        return StorageCapability(
            "DEGRADED", "REACHABLE", "INTEGRITY_MISMATCH", Failure.INTEGRITY_MISMATCH
        )
    except StorageError:
        return StorageCapability("UNAVAILABLE", "REACHABLE", "UNAVAILABLE", Failure.UNAVAILABLE)
    return StorageCapability("HEALTHY", "REACHABLE", "VERIFIED")


def measure_storage(settings: Settings) -> StorageCapability:
    if (
        settings.object_storage_backend != "s3"
        or not settings.operations_storage_guard_probe_enabled
    ):
        return StorageCapability("NOT_CONFIGURED", "NOT_VERIFIED", "STORAGE_CANARY_NOT_CONFIGURED")
    result = storage_probe.run(
        (
            settings.object_storage_endpoint,
            settings.object_storage_bucket,
            settings.object_storage_access_key,
            settings.object_storage_secret_key,
            settings.object_storage_region,
            str(settings.object_storage_secure),
            str(settings.operations_storage_canary_enabled),
        ),
        lambda: _guarded_storage(settings),
        StorageCapability("UNAVAILABLE", "NOT_VERIFIED", "NOT_VERIFIED", Failure.TIMEOUT),
    )
    if result.failure_category is not None:
        record(Signal.STORAGE, result.failure_category)
    return result
