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

Remaining merge-gate work: transcription/provider execution, speaking AI,
speaking scoring, mixed Writing+Speaking rescoring, and remote PostgreSQL and
object-storage execution proof.
