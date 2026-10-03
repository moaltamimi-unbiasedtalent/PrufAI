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

Speaking assessment reuses Phase 7 Step 1 rubric, criterion, disagreement,
HumanReviewRequest, immutable correction, deterministic Decimal aggregation,
and Phase 6 new-ScoringRun/result-version integration. Recorded speaking
requires governed online upload; robust offline audio recovery is not claimed.

Known limitations: transcription provider execution, authenticated range
streaming, candidate runtime capture UI, mixed Writing+Speaking rescoring, and
remote object-storage/PostgreSQL integration remain merge-gate work.
