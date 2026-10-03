# Phase 7 / Step 2 — Governed speaking and audio assessment

Phase 7 Step 2 supports recorded, task-bound monologic speaking only. `AUDIO`
is an existing governed response mode; `LIVE_ORAL` remains unsupported.

Audio is immutable primary evidence. A private `CandidateMediaAsset` is
checksum-bound to the recording session and response revision. Storage uses the
existing private object-storage boundary; keys and private URLs are never
candidate-facing. Technical upload limits and MIME/signature validation are
separate from exam recording-duration policy.

`SpeechTranscription` and immutable `SpeechTranscriptionVersion` are derived
evidence. A transcription cannot replace audio for acoustic criteria and is
never treated as a candidate grammar correction. The current implementation
does not infer accent, nationality, emotion, identity, or protected traits.

Speaking assessment remains outside this substep. Its future workflow may
reuse Phase 7 Step 1 rubric, criterion, disagreement, HumanReviewRequest,
immutable correction, deterministic Decimal aggregation, and Phase 6
new-ScoringRun/result-version integration. Recorded speaking requires governed
online upload; robust offline audio recovery is not claimed.

Candidate and capability-authorized reviewer playback use private authenticated
streaming. Single open-ended and suffix ranges are fetched through the storage
adapter's bounded backend range request; full-object reads are used only when
no range is requested. Lifecycle and logical playback access are recorded with
identifier-only audit metadata. Playback audit is deduplicated by actor,
organisation, and media asset so seek requests do not create unbounded rows.

Finalized audio is restored from the server's exact response revision and media
binding after refresh or navigation. Refresh does not start a recording or
consume an attempt. Rerecording is permitted only when the server policy allows
another attempt; each replacement creates new immutable media and revision
history. Submitted attempts are read-only. Unfinalized browser audio is never
treated as server-persisted.

## SUBSTEP 7.2B — Versioned speech transcription

`SpeechTranscription` is the stable identity for one exact READY
`CandidateMediaAsset`. Each immutable `SpeechTranscriptionVersion` pins the
asset ID, authoritative audio SHA-256, language, explicit
`SPEECH_TRANSCRIPTION` model profile, provider/model provenance, configuration
checksum, normalized transcript, validated segment structure, and a
deterministic version checksum. A later approved retranscription creates a new
version and marks the previous version `SUPERSEDED`; historical versions and
evidence references remain addressable by exact ID.

Transcription is an institution/system workflow action guarded by an explicit
capability and organisation context. Only READY media bound to a response
revision is eligible. The service retrieves private audio through the existing
storage abstraction, verifies the stored bytes against the authoritative
checksum, applies bounded retries, and never falls back from a configured
provider to a deterministic adapter. CI uses only the deterministic adapter;
production provider selection is explicit and purpose-governed.

Provider output is conservatively Unicode-normalized and whitespace-normalized
without grammar, spelling, translation, or pronunciation correction. Segment
IDs, timing, ordering, text size, total transcript size, and optional supplied
confidence are validated; invalid, empty, or oversized output fails safely.
Unknown duration is not fabricated. Transcription failure leaves the primary
audio and response revision valid and cannot create a speaking score, zero
score, review request, or released result.

`AudioEvidenceReference` pins an exact media asset and time bounds.
`TranscriptEvidenceReference` pins an exact transcription version and segment
with optional text offsets. Audio is primary evidence; transcript is derived
evidence and never silently resolves to the current version. Audit events carry
only IDs, checksums, status, and failure categories—never transcript text,
audio bytes, storage keys, provider payloads, or credentials. Candidate-facing
transcript visibility is not enabled. Speaking scoring and human speaking
review remain Step 2C work.

CI runs PostgreSQL-backed migration tests, the real private object-storage
path, deterministic transcription fixtures, and the pinned source-MinIO
process. External paid STT services are not used in CI.

Remaining merge-gate work: speaking AI, speaking scoring, mixed
Writing+Speaking rescoring, and Step 2C review/result integration.

## SUBSTEP 7.2C — Governed Speaking Assessment

Recorded Speaking assessment reuses the generic `SubjectiveAssessment`,
`SubjectiveAssessmentVersion`, `SubjectiveCriterionAssessment`, and
`HumanReviewRequest` entities. `RubricVersion.skill=SPEAKING` declares each
criterion's `AUDIO`, `TRANSCRIPT`, or `AUDIO_AND_TRANSCRIPT` modality. Original
audio remains primary; an exact `SpeechTranscriptionVersion` is optional
derived evidence and can never satisfy an audio-only criterion.

The bounded workflow is `prepare_input`, audio and transcript evidence
analysis, criterion evaluation, consistency review, deterministic validation,
review routing, and human-review request. It preserves independent criterion
proposals and disagreement, rejects malformed or authority-escalating model
output, and routes every valid proposal to mandatory human review. Model
profiles require purpose `SPEAKING_ASSESSMENT`; acoustic criteria additionally
require explicit `supports_audio_evidence` capability. No provider,
majority-vote, transcript-only, accent-origin, emotion, personality, or
protected-characteristic inference is performed.

Generic subjective provenance carries exact media asset/SHA and
transcription-version/checksum references plus safe AI, consistency, and human
value snapshots. Human correction creates a new immutable assessment version.
Only an approved version may later participate in a new Phase 6 ScoringRun;
automatic release and official certification remain outside this substep.
