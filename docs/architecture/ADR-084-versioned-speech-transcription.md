# ADR-084 Versioned speech transcription
Status: Accepted for MVP baseline

Context: Providers and models can change.
Decision: Persist immutable transcription versions pinned to audio checksum and profile.
Consequences: Historical derived evidence remains auditable.
Alternatives considered: Overwriting one transcript, rejected.
