# Phase 7 / Step 2 implementation report

## Executive summary

Implemented the candidate-audio primary evidence slice for Phase 7 / Step 2A:
server-owned recording sessions, candidate microphone UX, private upload and
validation, immutable media assets with SHA-256 checksums, exact response
revision binding, and authenticated candidate playback.

## Scope and governance

The supported response mode is existing `AUDIO`; `LIVE_ORAL` is unsupported.
Original audio remains primary evidence. Transcripts are derived and
versioned. Human review, deterministic aggregation, correction versioning,
tenant isolation, certification guards, and Phase 6 scoring semantics remain
authoritative from Step 1.

## Storage and validation

Audio validation enforces bounded bytes, supported browser-compatible media
types, detectable signatures, private generated storage keys, idempotent
recording-session finalization, and immutable checksum identity. Recording
start is server-timed and policy-gated, including maximum recording attempts.
Production storage remains S3-compatible; the explicit in-memory backend is not
allowed in production. No filename can influence an object path. Candidate
streaming is authenticated and supports single open-ended and suffix byte
ranges. Range requests use the storage abstraction's bounded `read_range`
operation; full-object reads are reserved for non-range playback.

The runtime accepts an AUDIO response only as `{ "media_asset_id": "..." }`.
`CandidateResponseMediaBinding` in additive migration `0018_audio_response_binding`
binds the finalized asset to the exact `CandidateResponseRevision`; cross-attempt
assets and replacement after submission are rejected.

## Transcription

`SpeechTranscription` groups immutable `SpeechTranscriptionVersion` records.
Each version pins source asset and audio checksum, profile, transcript,
segments, checksum, and supersession. Provider execution, bounded retries,
segment-duration validation, and explicit `SPEECH_TRANSCRIPTION` model-purpose
eligibility remain follow-up integration work.

## Migration and validation

Migration `0017_speaking_audio` remains unchanged. Additive migration
`0018_audio_response_binding` is 23 characters. Unit tests cover audio
signatures, rejection, checksum identity, filename safety, and AUDIO payload
shape. No `Base.metadata.create_all()` is used. Remote PostgreSQL/object-storage
gates remain pending. As-of resolution is intentionally deferred.

The candidate control is intentionally online-only: permission preflight is
explicit, recording never auto-starts, the server returns the recording
deadline, and the finalized media ID is persisted through the existing
revision-safe runtime save path. Reviewer playback is capability-gated by
`subjective.read` and scoped through the exact organisation, subjective
assessment, response revision, and media binding. Candidate membership alone
does not grant reviewer access. Audit-event emission, refresh/resume recovery,
and transcription/provider execution remain outside this substep.
