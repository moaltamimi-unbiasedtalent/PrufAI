# ADR-084 Versioned speech transcription
Status: Accepted for MVP baseline

Context: Providers and models can change and provider output is derived evidence.
Decision: Persist immutable transcription versions pinned to the exact READY audio
asset, authoritative audio checksum, explicit SPEECH_TRANSCRIPTION profile,
language, configuration checksum, normalized transcript, validated segments, and
provider provenance. Retranscription creates a new superseding version; failures
do not invalidate primary audio or candidate responses.
Consequences: Historical derived evidence remains auditable and cannot silently
be replaced by a newer model or by corrected text.
Alternatives considered: Overwriting one transcript, rejected.
