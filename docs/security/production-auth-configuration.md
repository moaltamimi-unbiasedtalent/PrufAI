# Production authentication configuration

This technical control does not authorize a deployment or close independent review gates.

With `ENVIRONMENT=production`, startup requires both:

- `AUTH_JWT_SECRET`: independently provisioned signing material of at least 32 characters. Provision at least 32 cryptographically random bytes through the approved deployment secret manager. Missing, empty, whitespace-padded, development, uniform-fill and known placeholder values are rejected. Length and placeholder checks cannot prove entropy or external custody.
- `AUTH_COOKIE_SECURE=true`: required for refresh and CSRF cookies, including deletion responses. Refresh cookies remain HttpOnly. SameSite, cookie paths, issuer, audience, TTL, refresh rotation, CSRF checks and session revocation retain their existing policy.

Generate the key once in an approved provisioning process and deliver it through the deployment secret manager to all API instances. Never generate a replacement at startup, place it in Git, print it, bake it into an image, or copy the development `.env.example` into production. Confirm authorized secret access, HTTPS termination and transport, and a separately approved rotation/session-invalidation procedure before deployment. No secret-manager integration or production resource is created by this change.

Settings representations and default serialization omit the signing key. The supported `Settings(...)` constructor (including environment loading), `Settings.model_validate()` and `Settings.model_validate_json()` entry points redact validation input values, including structured `errors()` and `json()` diagnostics. The JSON entry point accepts strings, bytes and bytearrays: malformed input is rejected with a stable `json_invalid` error, redacted input and fixed parser context, without retaining the original parser exception. Valid JSON continues through native Pydantic JSON validation and the existing model-level redactor; validation options and unrelated error categories are preserved. No partial JSON is accepted.

This is not a sanitizer for arbitrary Pydantic APIs or instrumentation. Direct use of the internal `__pydantic_validator__`, an independently constructed `TypeAdapter`, raw input logging, tracing integrations or local-variable/exception-frame dumps can bypass these entry points or retain source data. Application code still needs the signing key in process memory; do not log raw configuration/environment mappings, inspect secret attributes in diagnostics, or enable local-variable dumps. Development and test defaults remain local-only.

Production must also retain Redis authentication rate limiting, the existing S3/storage and real-provider deployment guards, and pilot restrictions. The configuration tests do not prove Redis/S3/provider reachability or production topology safety. HTTP unit tests use synthetic users and explicitly isolated test dependencies; hosted integration evidence is reported separately.

Separate pre-launch defect: the existing field validators for `object_storage_backend`, `knowledge_embedding_backend` and `knowledge_reranker_backend` precede `environment`, so production-labelled Settings can accept `memory` storage and `deterministic` embedding/reranking. This was reproduced through constructor and JSON validation on the PR #81 merge baseline as well as this correction. Repair and independent acceptance of those deployment guards remain required before production; the JSON diagnostic correction preserves their current behavior.

PR #60 P2 and E02A-S1 remain OPEN; E03A remains BLOCKED. Legacy-fence P2 formal closure, P1 acceptance, H10 and historical merge-review exceptions remain OPEN. The E05 historical startup timeout remains ROOT CAUSE UNRESOLVED. Production readiness is NO and pilot launch is NOT AUTHORIZED.
