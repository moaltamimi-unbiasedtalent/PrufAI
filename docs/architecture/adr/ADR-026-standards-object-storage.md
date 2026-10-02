# ADR-026: Standards Object Storage

Status: Accepted

## Context

Source documents must remain private, durable, checksum-addressable, and
available to the ingestion pipeline. An in-memory adapter is useful for tests
but cannot be a production fallback.

## Decision

Use a private S3-compatible bucket through the maintained boto3 client. The
endpoint, bucket, credentials, region, secure transport flag, and backend are
environment configuration. AWS S3 and MinIO-compatible endpoints are supported.
Production rejects the memory backend and surfaces storage failures without
silently persisting content elsewhere.

## Consequences

Object keys remain collision-resistant and filename-independent. SHA-256,
size, MIME type, and sanitized original filename remain application metadata.
Deletion is intentionally not part of the current upload path; approved
documents are not casually removed.

## Alternatives considered

Direct filesystem storage and public buckets were rejected because they do not
provide the required deployment portability or privacy boundary. Manual S3
signing was rejected in favor of the maintained client library.
