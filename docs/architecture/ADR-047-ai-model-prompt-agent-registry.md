# ADR-047: AI model, prompt and agent registries

Status: Accepted for MVP baseline

Context: AI provenance requires stable identities and immutable versions.

Decision: Persist model profiles, prompt identities/versions and agent identities/versions with exact foreign-key pinning and checksums. Secrets remain external.

Consequences: Historical runs are reproducible; changes require governed versions. Registry management adds approval workflow and schema overhead.

Alternatives considered: Vendor-managed prompt stores and mutable “latest” references were rejected because they weaken auditability.
