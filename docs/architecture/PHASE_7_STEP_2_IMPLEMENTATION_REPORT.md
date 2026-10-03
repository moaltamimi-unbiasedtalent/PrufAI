# Phase 7 / Step 2 implementation report

## Executive summary

Added the additive persistence and validation foundation for recorded
speaking evidence: server-owned recording sessions, immutable private media
assets with SHA-256 checksums, and versioned derived speech transcription.

## Scope and governance

The supported response mode is existing `AUDIO`; `LIVE_ORAL` is unsupported.
Original audio remains primary evidence. Transcripts are derived and
versioned. Human review, deterministic aggregation, correction versioning,
tenant isolation, certification guards, and Phase 6 scoring semantics remain
authoritative from Step 1.

## Storage and validation

Audio validation enforces bounded bytes, supported browser-compatible media
types, detectable signatures, private generated storage keys, idempotent
recording-session finalization, and immutable checksum identity. Production
storage remains S3-compatible; the explicit in-memory backend is not allowed in
production. No filename can influence an object path.

## Transcription

`SpeechTranscription` groups immutable `SpeechTranscriptionVersion` records.
Each version pins source asset and audio checksum, profile, transcript,
segments, checksum, and supersession. Provider execution, bounded retries,
segment-duration validation, and explicit `SPEECH_TRANSCRIPTION` model-purpose
eligibility remain follow-up integration work.

## Migration and validation

Migration `0017_speaking_audio` is additive and 22 characters. Unit tests cover
audio signatures, rejection, checksum identity, and filename safety. No
`Base.metadata.create_all()` is used. Remote PostgreSQL/object-storage gates
remain pending. As-of resolution is intentionally deferred.
