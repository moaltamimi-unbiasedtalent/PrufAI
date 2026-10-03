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

## Substep 7.2B — versioned transcription

`SpeechTranscription` is reused as the stable one-asset root. Additive
migration `0019_transcription_governance` adds idempotent operation identity,
version lifecycle, provider/model and adapter provenance, configuration
checksum, failure category, and retry count without rewriting migrations 0017
or 0018. Completed versions are immutable; explicit retranscription creates a
new numbered version, links `supersedes_version_id`, and resolves current
versus historical versions deterministically.

The service enforces READY media, response binding, organisation/capability
authorization, exact source checksum verification, active explicit
`SPEECH_TRANSCRIPTION` profile eligibility, bounded retries, deterministic
normalization, segment/size validation, and constrained failure categories.
The private storage adapter supplies source bytes. CI uses a deterministic
fixture provider; production has no silent test-provider, model, or local
fallback. Empty or malformed output fails without invalidating audio or the
candidate response and cannot create any speaking score or result.

Audio and transcript evidence references pin exact asset/version coordinates.
Safe transcription audit events contain identifiers and checksums only. No
transcript text, provider payload, storage key, credential, protected-trait
inference, candidate-facing transcript, Speaking HumanReviewRequest, or
speaking scoring was added.

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
does not grant reviewer access. Refresh/resume recovery is server-backed and
transcription/provider execution remain outside this substep.

Candidate audio lifecycle events are emitted through `AuditService` for
recording start, finalization, rejection, expiry, and permitted playback. The
playback event is logically deduplicated by actor, organisation, and media asset
so HTTP range seeks do not create one immutable audit row per request. Audit
metadata contains identifiers, status/failure reason, and checksum only; it
never contains audio bytes, speech, storage keys, private endpoints, or raw
request payloads. Audio remains online-only: an unfinalized browser recording
is not restored after refresh, while a finalized response is restored from the
server's immutable revision/media binding.

The CI workflow starts PostgreSQL/pgvector, Redis, and MinIO services, applies
`alembic upgrade head`, and invokes dedicated Candidate Audio PostgreSQL and
object-storage integration tests with required flags. Local Python validation
uses `python3 -m pytest tests`: 155 passed and 14 expected infrastructure skips
(11 PostgreSQL, 2 Redis, 1 object storage), with no unexpected skips. The local
npm audit endpoint was unavailable due DNS; Python `pip_audit` is not installed.

## Substep 7.2C — governed speaking assessment

Step 2C extends the skill-neutral subjective assessment path rather than
creating Speaking-specific assessment tables. `RubricVersion` accepts
`SPEAKING` and declarative criterion evidence modes (`AUDIO`, `TRANSCRIPT`, or
`AUDIO_AND_TRANSCRIPT`). Migration `0020_speaking_assessment` adds exact audio
asset/SHA and transcription-version/checksum provenance plus safe AI,
consistency, and human-value snapshots to `SubjectiveAssessmentVersion`.

`SpeakingAssessmentService` implements bounded preparation, modality-aware
audio/transcript evidence validation, criterion proposals, consistency review,
deterministic validation, bounded retry, protected-attribute and spoken prompt
injection guards, and mandatory human-review routing. It never approves a
proposal, changes rubric/rules, uses a transcript for an audio-only criterion,
or claims examiner equivalence. Institution speaking-review routes are
organisation-scoped and distinguish original audio as primary evidence from
derived transcription.
