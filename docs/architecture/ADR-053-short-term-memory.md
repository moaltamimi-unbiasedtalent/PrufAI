# ADR-053: Short-term workflow memory

Short-term values are JSON-only, bounded, TTL-backed and isolated by run and
tenant. Redis is the production backend; an explicitly selected in-memory
backend exists only for tests. Backend failures are surfaced rather than
silently degraded.
