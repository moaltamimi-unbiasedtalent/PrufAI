# ADR-092: Lock appeal rows during consequential decisions

Status: Accepted for Phase 8 / Step 1

Appeal adjudication, lifecycle transitions, information updates, and governed
correction initiation lock the appeal row with PostgreSQL `SELECT ... FOR
UPDATE`. Expected appeal-version and evidence checksums are verified while the
lock is held, and operation identifiers remain idempotent. A stale or concurrent
review therefore cannot create a second decision from the same version.

The lock is an application-level serialization boundary, not a substitute for
authorization or tenant checks. Historical appeal versions and result artifacts
remain append-only. The migration-backed integration gate exercises the database
row-lock primitive; a full fixture-backed remote integration remains a required
follow-up when CI services are available.
