# PrüfAI Phase 4 / Step 2 — Governed memory

Governed memory retains bounded operational context without becoming a second
authority. Resolution is strictly ordered:

```text
authoritative knowledge > rules-as-code > approved policy
> approved long-term memory > short-term workflow context
```

Long-term memory is a versioned domain record. Every version has a lifecycle,
scope, origin, effective window, optional expiry/review date, checksum and
provenance bindings. AI may propose a version, but only a human with the review
and activation capability can move it into production context. Active versions
are immutable; retirement and supersession preserve historical resolution.

Short-term memory is run- and organisation-scoped. Production uses the injected
Redis backend with explicit TTL and bounded JSON values. The in-memory backend
is marked test-only and is never selected as a fallback when Redis is absent.
Keys contain only tenant/run/namespace identifiers; personal data, prompts and
credentials are not accepted as implicit keys or serialized executable data.

Memory is context, not evidence. Knowledge, standards, rules, HITL decisions,
RBAC and audit remain authoritative. Retrieval excludes expired or overdue
records from new production context, while historical resolution remains
available for reproducibility. Step 2 deliberately does not implement feedback
learning, autonomous approval, prompt mutation or candidate personal memory.
